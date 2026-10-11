"""Blurred patches for platforms without a compositor-side blur.

On GNOME the extension blurs the live window contents. Elsewhere we draw
our own small windows over each figure, showing a blurred copy of the
pixels that were captured underneath. The copy goes stale if the content
under it changes (a video), but the next check refreshes it, and stale
blurred pixels reveal nothing.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image

# How much a patch is shrunk before being smoothed back up: higher is blurrier.
BLUR_FACTOR = 20


@dataclass
class Patch:
    x: int          # position in the source frame's pixels
    y: int
    rgba: np.ndarray  # (h, w, 4) uint8; alpha is 0 or 255


class Patcher:
    """Turns regions [(id, shape)] from the Engine into Patches, per source."""

    def __init__(self):
        self._blurred = {}  # region id → (x0, y0, x1, y1, blurred RGB array)

    def update(self, regions, frame=None):
        """Patches for these regions, keyed by region id. Without a frame
        (the tracker expired something), cached blurred pixels are reused."""
        out = {}
        live = set()
        for rid, shape in regions:
            if not shape:
                continue
            live.add(rid)
            x0 = int(min(b[2] for b in shape))
            y0 = int(min(b[0] for b in shape))
            x1 = int(np.ceil(max(b[3] for b in shape)))
            y1 = int(np.ceil(max(b[1] for b in shape)))
            if x1 <= x0 or y1 <= y0:
                continue
            cached = self._blurred.get(rid)
            if frame is not None:
                x1, y1 = min(x1, frame.width), min(y1, frame.height)
                rgb = blur(frame, x0, y0, x1, y1)
                self._blurred[rid] = (x0, y0, x1, y1, rgb)
            elif cached and cached[0] <= x0 and cached[1] <= y0 and cached[2] >= x1 and cached[3] >= y1:
                cx0, cy0 = cached[0], cached[1]
                rgb = cached[4][y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0]
            else:
                # No pixels for this area: cover it with a neutral grey
                # rather than leave it showing.
                rgb = np.full((y1 - y0, x1 - x0, 3), 128, np.uint8)
            alpha = np.zeros((y1 - y0, x1 - x0), np.uint8)
            for by0, by1, bx0, bx1 in shape:
                alpha[max(0, int(by0) - y0):max(0, int(np.ceil(by1)) - y0),
                      max(0, int(bx0) - x0):max(0, int(np.ceil(bx1)) - x0)] = 255
            out[rid] = Patch(x0, y0, np.dstack([rgb, alpha]))
        for rid in list(self._blurred):
            if rid not in live:
                del self._blurred[rid]
        return out


def blur(frame, x0, y0, x1, y1):
    """A strongly blurred RGB copy of frame[y0:y1, x0:x1]."""
    w, h = x1 - x0, y1 - y0
    rows = np.frombuffer(frame.view, np.uint8, count=y1 * frame.stride).reshape(y1, frame.stride)
    bgr = rows[y0:y1, x0 * 4:x1 * 4].reshape(h, w, 4)[:, :, 2::-1]
    img = Image.fromarray(np.ascontiguousarray(bgr), "RGB")
    small = img.resize((max(1, w // BLUR_FACTOR), max(1, h // BLUR_FACTOR)), Image.Resampling.BOX)
    return np.asarray(small.resize((w, h), Image.Resampling.BICUBIC))
