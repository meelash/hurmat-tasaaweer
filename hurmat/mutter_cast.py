"""Per-window capture through Mutter's own ScreenCast D-Bus API.

A window stream contains only that window's content. The blur overlays the
extension draws are separate actors, so they never show up here; that is what
keeps the detector from seeing (and reacting to) its own blur.

Mutter refuses new sessions while the screen is locked ("Session creation
inhibited") and closes them when the window goes away, so callers should
expect streams to fail or close and retry later.
"""

from gi.repository import Gio, GLib

from .gst_frames import PipeWireFrames

BUS = "org.gnome.Mutter.ScreenCast"
ROOT_PATH = "/org/gnome/Mutter/ScreenCast"
ROOT_IFACE = "org.gnome.Mutter.ScreenCast"
SESSION_IFACE = "org.gnome.Mutter.ScreenCast.Session"
STREAM_IFACE = "org.gnome.Mutter.ScreenCast.Stream"

CURSOR_HIDDEN = 0


class WindowStream:
    """One Mutter ScreenCast session recording one window."""

    def __init__(self, bus, window_id, on_closed=None, max_fps=None):
        self.bus = bus
        self.window_id = window_id
        self.frames = None
        self.closed = False
        self._subs = []

        # is-platform marks the session as part of the desktop itself, which
        # keeps GNOME's "screen is being shared" indicator from showing.
        self.session = self._call(
            ROOT_PATH, ROOT_IFACE, "CreateSession",
            GLib.Variant("(a{sv})", ({"is-platform": GLib.Variant("b", True)},)), "(o)",
        )[0]
        stream = self._call(
            self.session, SESSION_IFACE, "RecordWindow",
            GLib.Variant("(a{sv})", ({
                "window-id": GLib.Variant("t", window_id),
                "cursor-mode": GLib.Variant("u", CURSOR_HIDDEN),
            },)), "(o)",
        )[0]

        def on_stream_added(*args):
            node_id = args[-1].unpack()[0]
            self.frames = PipeWireFrames(node_id, max_fps=max_fps)

        def on_session_closed(*_):
            self.closed = True
            if on_closed:
                on_closed(self)

        self._subs.append(bus.signal_subscribe(
            BUS, STREAM_IFACE, "PipeWireStreamAdded", stream, None, 0, on_stream_added))
        self._subs.append(bus.signal_subscribe(
            BUS, SESSION_IFACE, "Closed", self.session, None, 0, on_session_closed))
        self._call(self.session, SESSION_IFACE, "Start", None, None)

    def _call(self, path, iface, method, args, reply_type):
        ret = self.bus.call_sync(
            BUS, path, iface, method, args,
            GLib.VariantType(reply_type) if reply_type else None,
            Gio.DBusCallFlags.NONE, -1, None,
        )
        return ret.unpack() if ret is not None else None

    def pull(self):
        if self.frames is None or self.closed:
            return None
        return self.frames.pull(timeout_s=0)

    def stop(self):
        for sub in self._subs:
            self.bus.signal_unsubscribe(sub)
        if self.frames:
            self.frames.close()
        if not self.closed:
            try:
                self._call(self.session, SESSION_IFACE, "Stop", None, None)
            except GLib.Error:
                pass  # already gone with its window
        self.closed = True
