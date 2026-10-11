"""hurmatd: watches every window, detects living beings, tells the extension what to blur.

    python -m hurmat.daemon [--model DIR] [--min-size PX] [-v]
"""

import argparse
import logging
import signal
import time
from dataclasses import dataclass

from gi.repository import Gio, GLib

from .engine import Source, add_engine_args, load_engine, setup_logging
from .mutter_cast import WindowStream

log = logging.getLogger("hurmatd")

EXT_BUS = "org.hurmat.Tasaaweer"
EXT_PATH = "/org/hurmat/Tasaaweer"
EXT_IFACE = "org.hurmat.Tasaaweer1"
# Owned while hurmatd runs; the extension clears all blur when it disappears.
DAEMON_BUS = "org.hurmat.Tasaaweer.Detector"

POLL_MS = 5
RESYNC_S = 2.0  # retry windows whose stream failed (e.g. while the screen was locked)
TICK_BUDGET_S = 0.05
HIDE_GRACE_S = 1.0  # keep recording this long after a window is hidden


@dataclass
class Watched:
    """A window and its detection state. The stream only exists while the
    window can be seen (plus a short grace period after it's hidden)."""
    title: str
    rect: tuple  # content (x, y, w, h) inside the stream, from the extension
    src: Source
    stream: WindowStream | None = None
    visible: bool = True
    hidden_since: float | None = None


class Daemon:
    def __init__(self, engine, args):
        self.engine = engine
        self.args = args
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.windows = {}  # window id → Watched
        self.sent = {}     # window id → last regions sent
        self.ext_present = False
        self.last_resync = 0.0

        Gio.bus_watch_name_on_connection(
            self.bus, EXT_BUS, Gio.BusNameWatcherFlags.NONE,
            self._on_ext_appeared, self._on_ext_vanished,
        )
        self.bus.signal_subscribe(
            EXT_BUS, EXT_IFACE, "WindowsChanged", EXT_PATH, None, 0,
            lambda *_: self.resync(),
        )
        self.bus.signal_subscribe(
            EXT_BUS, EXT_IFACE, "WindowResized", EXT_PATH, None, 0, self._on_resized,
        )
        self.bus.signal_subscribe(
            EXT_BUS, EXT_IFACE, "VisibilityChanged", EXT_PATH, None, 0, self._on_visibility,
        )
        Gio.bus_own_name_on_connection(self.bus, DAEMON_BUS, Gio.BusNameOwnerFlags.NONE, None, None)
        GLib.timeout_add(POLL_MS, self.tick)

    def shutdown(self):
        """Remove our blur and stop recording before exiting."""
        if self.ext_present:
            try:
                self._ext_call("ClearAll")
            except GLib.Error as e:
                log.warning("ClearAll failed: %s", e.message)
        self._stop_all()

    # --- extension connection ---

    def _ext_call(self, method, args=None, reply=None, sync=True):
        if sync:
            ret = self.bus.call_sync(EXT_BUS, EXT_PATH, EXT_IFACE, method, args,
                                     GLib.VariantType(reply) if reply else None, 0, -1, None)
            return ret.unpack() if ret is not None else None
        self.bus.call(EXT_BUS, EXT_PATH, EXT_IFACE, method, args, None, 0, -1, None, None)

    def _on_ext_appeared(self, *_):
        log.info("extension connected")
        self.ext_present = True
        self.sent.clear()
        self.resync()

    def _on_ext_vanished(self, *_):
        if self.ext_present:
            log.warning("extension gone; waiting for it to come back")
        self.ext_present = False
        self._stop_all()

    def _on_resized(self, *args):
        wid, *rect = args[-1].unpack()
        if wid in self.windows:
            self.windows[wid].rect = tuple(rect)

    def _on_visibility(self, *args):
        wid, visible = args[-1].unpack()
        if wid in self.windows:
            self._set_visible(wid, visible)

    def _set_visible(self, wid, visible):
        win = self.windows[wid]
        if visible == win.visible:
            return
        win.visible = visible
        log.debug("window %d %s", wid, "visible" if visible else "hidden")
        if visible:
            win.hidden_since = None
            # Always start a fresh recording: frames from while the window
            # was hidden may have been captured mid-animation (scaled), and a
            # new recording begins with a frame of the window as it is now.
            self._stop_stream(win)
            self._start_stream(wid)
        else:
            # The stream is stopped after HIDE_GRACE_S (see tick), so windows
            # briefly covered while dragging don't restart their stream.
            win.hidden_since = time.monotonic()

    # --- window streams ---

    def resync(self):
        self.last_resync = time.monotonic()
        if not self.ext_present:
            return
        try:
            listed = {wid: (title, tuple(rest[:4]), rest[4]) for wid, title, *rest
                      in self._ext_call("ListWindows", reply="(a(tsiiiib))")[0]}
        except GLib.Error as e:
            log.warning("ListWindows failed: %s", e.message)
            return

        for wid in list(self.windows):
            if wid not in listed:
                self._forget(wid)
        for wid, (title, rect, visible) in listed.items():
            if wid not in self.windows:
                self.windows[wid] = Watched(title, rect, self.engine.new_source())
                log.info("tracking window %d %r", wid, title)
            win = self.windows[wid]
            win.title, win.rect = title, rect
            self._set_visible(wid, visible)
            if win.stream and win.stream.closed:
                self._stop_stream(win)
            if win.visible and not win.stream:
                # Retries streams that failed or were closed by Mutter, e.g.
                # while the screen was locked.
                self._start_stream(wid)

    def _start_stream(self, wid):
        win = self.windows[wid]
        if win.stream:
            return
        try:
            win.stream = WindowStream(self.bus, wid, max_fps=self.args.max_fps)
            log.debug("recording window %d", wid)
        except GLib.Error as e:
            # Expected while the screen is locked; retried on the next resync.
            log.debug("can't record window %d: %s", wid, e.message)

    def _stop_stream(self, win):
        """Stop recording but keep the window's regions and change reference:
        what's on screen for it hasn't changed."""
        win.src.drop_pending()
        if win.stream:
            win.stream.stop()
            win.stream = None

    def _forget(self, wid):
        win = self.windows.pop(wid)
        self._stop_stream(win)
        self.sent.pop(wid, None)
        log.info("stopped tracking window %d %r", wid, win.title)

    def _stop_all(self):
        for wid in list(self.windows):
            self._forget(wid)

    # --- detection loop ---

    def tick(self):
        now = time.monotonic()
        if now - self.last_resync > RESYNC_S:
            self.resync()
        for wid, win in self.windows.items():
            if win.stream and win.hidden_since and now - win.hidden_since > HIDE_GRACE_S:
                log.debug("window %d hidden: stopped recording", wid)
                self._stop_stream(win)
        deadline = time.monotonic() + TICK_BUDGET_S
        while True:
            self._collect()
            picked = self.engine.pick({wid: win.src for wid, win in self.windows.items()})
            if not picked:
                break
            wid, src = picked
            win = self.windows[wid]
            frame = src.pending[0]
            age_ms = (win.stream.frames.running_time_ns() - frame.pts_ns) / 1e6 if win.stream else None
            self.engine.check(src, lambda regions, _frame, wid=wid: self._send(wid, regions),
                              label=f"window {wid}", age_ms=age_ms)
            if time.monotonic() > deadline:
                break  # let D-Bus and stream events through
        return GLib.SOURCE_CONTINUE

    def _collect(self):
        """Pull the newest frame of every window and keep it if it needs checking."""
        now = time.monotonic()
        for wid, win in self.windows.items():
            # Every tick, not only when no frame came: a still window still
            # gets a repeat frame every second (keepalive).
            if win.src.tracker.expire(now):
                self._send(wid, win.src.tracker.regions())
            frame = win.stream.pull() if win.stream else None
            if frame is not None:
                self.engine.offer(win.src, frame, win.rect)

    def _send(self, wid, regions):
        """regions: [(id, shape)], shape = [(y0, y1, x0, x1), ...] bands."""
        rounded = [(i, [tuple(float(round(v)) for v in band) for band in shape])
                   for i, shape in regions]
        if self.sent.get(wid) == rounded:
            return
        self.sent[wid] = rounded
        log.debug("window %d: sending %d regions (%d bands)", wid, len(rounded),
                  sum(len(shape) for _, shape in rounded))
        self._ext_call("SetRegions", GLib.Variant("(ta(ua(dddd)))", (wid, rounded)), sync=False)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Mutter only enforces a minimum gap between frames, so a quiet window
    # that suddenly changes is still recorded at once; only windows changing
    # constantly (video) are throttled. Saves the compositor copying frames.
    add_engine_args(p, max_fps=15)
    args = p.parse_args()
    setup_logging(args)
    daemon = Daemon(load_engine(args), args)
    loop = GLib.MainLoop()

    def on_signal():
        log.info("shutting down")
        daemon.shutdown()
        loop.quit()
        return GLib.SOURCE_REMOVE

    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, on_signal)
    loop.run()


if __name__ == "__main__":
    main()
