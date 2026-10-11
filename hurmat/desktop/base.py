"""The capture → detect → draw loop shared by the Windows, macOS and X11
backends.

A backend provides *sources* (areas it can capture without our own blur
showing up in them: a monitor on Windows/macOS, a window on X11), grabs
frames from them, and draws Patches (patches.py) as small click-through
windows on top.
"""

import logging
import time
from dataclasses import dataclass, field

from ..patches import Patcher

log = logging.getLogger("hurmat")

RESYNC_S = 0.25     # how often the list of sources and their positions is refreshed
TICK_BUDGET_S = 0.05


@dataclass
class SourceInfo:
    """Where a source's frames sit on screen. Frame pixel (px, py) is drawn
    at screen pixel (x + px, y + py)."""
    x: int
    y: int
    width: int
    height: int
    label: str = ""
    # Parts of the source that can be seen, as (x0, y0, x1, y1) in frame
    # pixels; None means all of it. Patches are clipped to these.
    visible: tuple | None = None


class Backend:
    """What each platform implements."""
    name = "?"

    def sources(self):
        """{key: SourceInfo} for every source worth capturing right now."""
        raise NotImplementedError

    def grab(self, key):
        """A frame (frames.ArrayFrame) of the source, without our patches in
        it, or None if it can't be captured right now."""
        raise NotImplementedError

    def show(self, key, info, patches):
        """Replace the patches drawn for this source. patches: {id: Patch},
        positions in the source's frame pixels (see SourceInfo)."""
        raise NotImplementedError

    def run(self, tick, interval_s=0.005):
        """Run the platform event loop, calling tick() about every
        interval_s, until tick() returns False or the user quits."""
        raise NotImplementedError

    def close(self):
        """Remove every patch."""


@dataclass
class _State:
    info: SourceInfo
    src: object
    patcher: Patcher = field(default_factory=Patcher)
    last_grab: float = 0.0
    last_sent: object = None


class DesktopApp:
    def __init__(self, backend, engine, args):
        self.backend = backend
        self.engine = engine
        self.args = args
        self.states = {}
        self.last_resync = 0.0
        self.grab_interval = 1 / args.max_fps if args.max_fps else 0

    def tick(self):
        now = time.monotonic()
        if now - self.last_resync > RESYNC_S:
            self._resync()
        for key, st in self.states.items():
            if st.src.tracker.expire(now):
                self._send(key, st, st.src.tracker.regions(), None)
            if now - st.last_grab >= self.grab_interval:
                st.last_grab = now
                frame = self.backend.grab(key)
                if frame is not None:
                    self.engine.offer(st.src, frame)
        deadline = time.monotonic() + TICK_BUDGET_S
        while time.monotonic() < deadline:
            picked = self.engine.pick({k: s.src for k, s in self.states.items()})
            if not picked:
                break
            key, _ = picked
            st = self.states[key]
            self.engine.check(st.src, lambda regions, frame, key=key, st=st: self._send(key, st, regions, frame),
                              label=st.info.label or str(key))
        return True

    def _resync(self):
        self.last_resync = time.monotonic()
        current = self.backend.sources()
        for key in list(self.states):
            if key not in current:
                st = self.states.pop(key)
                st.src.drop_pending()
                self.backend.show(key, st.info, {})
                log.debug("stopped watching %s", st.info.label or key)
        for key, info in current.items():
            st = self.states.get(key)
            if st is None:
                self.states[key] = _State(info, self.engine.new_source())
                log.info("watching %s (%dx%d at %d,%d)", info.label or key, info.width, info.height, info.x, info.y)
            elif info != st.info:
                moved_only = (info.width, info.height) == (st.info.width, st.info.height)
                st.info = info
                if moved_only and st.last_sent is not None:
                    # Same content, moved or partly covered/uncovered: redraw
                    # the patches in their new place and clipping at once.
                    self._send(key, st, st.last_sent, None, force=True)
                elif not moved_only:
                    # Resized: old regions no longer line up with the content.
                    st.src = self.engine.new_source()
                    self.backend.show(key, info, {})
                    st.last_sent = None

    def _send(self, key, st, regions, frame, force=False):
        if frame is None and not force and regions == st.last_sent:
            return
        st.last_sent = regions
        self.backend.show(key, st.info, st.patcher.update(regions, frame))

    def close(self):
        for key, st in self.states.items():
            st.src.drop_pending()
        self.backend.close()
