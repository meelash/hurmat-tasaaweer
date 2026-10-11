"""X11 backend (XFCE, Cinnamon, MATE, KDE on X11, i3, ...).

Each top-level window is captured on its own through the Composite
extension: its off-screen pixmap holds just that window's contents, so our
patch windows never appear in what the detector sees. Patches are
override-redirect windows, clipped to the part of their window that other
windows don't cover.
"""

import logging
import time

from Xlib import X, error
from Xlib import display as xdisplay
from Xlib.ext import composite, shape

from ..frames import ArrayFrame
from .base import Backend, SourceInfo

log = logging.getLogger("hurmat")

MIN_WINDOW = 48
PUT_CHUNK = 192 * 1024  # bytes per PutImage request, under the core protocol's request size limit


class X11Backend(Backend):
    name = "x11"

    def __init__(self):
        self.d = xdisplay.Display()
        if not self.d.has_extension("Composite"):
            raise RuntimeError("the X server has no Composite extension")
        if not self.d.has_extension("SHAPE"):
            raise RuntimeError("the X server has no SHAPE extension")
        self.screen = self.d.screen()
        self.root = self.screen.root
        # Keep every top-level window's contents in an off-screen pixmap we
        # can read. Automatic redirection is allowed alongside a compositing
        # window manager.
        self.root.composite_redirect_subwindows(composite.RedirectAutomatic)
        self.d.sync()
        self.ours = {}  # (source key, patch id) → (window, pixmap)
        self._windows = {}
        log.info("X11 display %s, %dx%d", self.d.get_display_name(),
                 self.screen.width_in_pixels, self.screen.height_in_pixels)

    # --- sources: viewable top-level windows ---

    def sources(self):
        out, self._windows = {}, {}
        own = {w.id for w, _ in self.ours.values()}
        try:
            children = self.root.query_tree().children  # bottom to top
        except error.XError:
            return out
        stack = []
        for w in children:
            if w.id in own:
                continue
            try:
                attrs = w.get_attributes()
                if attrs.map_state != X.IsViewable or attrs.override_redirect or attrs.win_class != X.InputOutput:
                    continue
                g = w.get_geometry()
            except error.XError:
                continue
            if g.width < MIN_WINDOW or g.height < MIN_WINDOW:
                continue
            stack.append((w, g))
        screen = (0, 0, self.screen.width_in_pixels, self.screen.height_in_pixels)
        for i, (w, g) in enumerate(stack):
            rect = (g.x, g.y, g.x + g.width, g.y + g.height)
            visible = _intersect(rect, screen)
            pieces = [visible] if visible else []
            for _, above in stack[i + 1:]:
                pieces = _subtract(pieces, (above.x, above.y, above.x + above.width, above.y + above.height))
                if not pieces:
                    break
            if not pieces:
                continue  # fully covered or off screen: not worth capturing
            local = tuple(sorted((a - g.x, b - g.y, c - g.x, e - g.y) for a, b, c, e in pieces))
            out[w.id] = SourceInfo(g.x, g.y, g.width, g.height, label=f"window 0x{w.id:x}", visible=local)
            self._windows[w.id] = w
        return out

    def grab(self, key):
        w = self._windows.get(key)
        if w is None:
            return None
        pm = None
        try:
            g = w.get_geometry()
            pm = w.composite_name_window_pixmap()
            img = pm.get_image(0, 0, g.width, g.height, X.ZPixmap, 0xFFFFFFFF)
        except error.XError:
            return None
        finally:
            if pm is not None:
                try:
                    pm.free()
                except error.XError:
                    pass
        data = img.data
        if g.depth not in (24, 32) or len(data) < g.width * g.height * 4:
            log.debug("window 0x%x: unsupported depth %d", key, g.depth)
            return None
        return ArrayFrame(data, g.width, g.height, stride=len(data) // g.height, pts_ns=time.monotonic_ns())

    # --- patches ---

    def show(self, key, info, patches):
        for (k, pid) in [kp for kp in self.ours if kp[0] == key and kp[1] not in patches]:
            self._destroy((k, pid))
        for pid, p in patches.items():
            self._destroy((key, pid))  # contents changed: simplest to rebuild
            h, w = p.rgba.shape[:2]
            # Only the figure's outline is drawn, clipped to the part of the
            # source window that isn't covered by other windows. (Window
            # managers keep override-redirect windows on top, so stacking
            # them just above the source window isn't reliable.)
            rects = _alpha_rects(p.rgba[:, :, 3])
            if info.visible is not None:
                rects = _clip_rects(rects, [(a - p.x, b - p.y, c - p.x, e - p.y) for a, b, c, e in info.visible])
            if not rects:
                continue
            depth = self.screen.root_depth
            pm = self.root.create_pixmap(w, h, depth)
            gc = pm.create_gc()
            bgrx = p.rgba[:, :, [2, 1, 0, 3]].tobytes()
            rows = max(1, PUT_CHUNK // (w * 4))
            for y in range(0, h, rows):
                n = min(rows, h - y)
                pm.put_image(gc, 0, y, w, n, X.ZPixmap, depth, 0, bgrx[y * w * 4:(y + n) * w * 4])
            gc.free()
            win = self.root.create_window(
                info.x + p.x, info.y + p.y, w, h, 0, depth, X.InputOutput, X.CopyFromParent,
                override_redirect=True, background_pixmap=pm, event_mask=0)
            win.shape_rectangles(shape.SO.Set, shape.SK.Bounding, X.Unsorted, 0, 0, rects)
            # Clicks pass straight through.
            win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])
            win.map()
            win.configure(stack_mode=X.Above)
            self.ours[(key, pid)] = (win, pm)
        self.d.flush()

    def _destroy(self, kp):
        entry = self.ours.pop(kp, None)
        if entry:
            win, pm = entry
            try:
                win.destroy()
                pm.free()
            except error.XError:
                pass

    def close(self):
        for kp in list(self.ours):
            self._destroy(kp)
        self.d.flush()

    def run(self, tick, interval_s=0.005):
        try:
            while True:
                while self.d.pending_events():
                    self.d.next_event()
                if tick() is False:
                    return
                time.sleep(interval_s)
        except KeyboardInterrupt:
            pass


def _alpha_rects(alpha):
    """Rectangles (x, y, w, h) covering the opaque pixels, merging identical
    consecutive rows (patch shapes are horizontal bands)."""
    rects = []
    prev = None
    for y in range(alpha.shape[0]):
        row = alpha[y] > 0
        if not row.any():
            spans = ()
        else:
            edges = row.astype("int8")
            diff = (edges[1:] != edges[:-1]).nonzero()[0] + 1
            bounds = [0, *diff.tolist(), len(row)]
            spans = tuple((a, b) for a, b in zip(bounds, bounds[1:]) if row[a])
        if prev is not None and spans == prev[0]:
            for r in prev[1]:
                r[3] += 1
        else:
            new = [[a, y, b - a, 1] for a, b in spans]
            rects += new
            prev = (spans, new)
    return [tuple(r) for r in rects]


def _clip_rects(rects, visible):
    """(x, y, w, h) rectangles intersected with (x0, y0, x1, y1) areas."""
    out = []
    for x, y, w, h in rects:
        for v in visible:
            r = _intersect((x, y, x + w, y + h), v)
            if r:
                out.append((r[0], r[1], r[2] - r[0], r[3] - r[1]))
    return out


def _intersect(a, b):
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] < r[2] and r[1] < r[3] else None


def _subtract(rects, cover):
    out = []
    for r in rects:
        o = _intersect(r, cover)
        if not o:
            out.append(r)
            continue
        if r[1] < o[1]:
            out.append((r[0], r[1], r[2], o[1]))
        if o[3] < r[3]:
            out.append((r[0], o[3], r[2], r[3]))
        if r[0] < o[0]:
            out.append((r[0], o[1], o[0], o[3]))
        if o[2] < r[2]:
            out.append((o[2], o[1], r[2], o[3]))
    return out
