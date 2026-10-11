"""Turns per-frame detections into stable blur regions.

Raw detections flicker: the same person can be found, missed, then found
again within a second. The tracker smooths that out:
  - new detections become regions immediately (never delay covering);
  - a region is held for `hold_s` after the first check that missed it
    (a check that found it again resets the clock);
  - each region is the figure's outline (see shapes.py), padded, and
    while it moves it covers both where it is and where it just was, so the
    blur trails slightly and a moving subject stays covered;
  - when only part of a window was checked, regions outside that part are
    kept as they are: their content hasn't changed.
Region ids stay stable across frames so the extension can update each blur
in place instead of recreating it.
"""

from dataclasses import dataclass
from itertools import count

import numpy as np

from .shapes import figure_shape, union


@dataclass
class Track:
    id: int
    box: np.ndarray       # x0, y0, x1, y1 of the latest detection (for matching)
    shape: list           # padded outline bands of the latest detection
    prev_shape: list | None = None  # the one before, kept briefly while moving
    moved_at: float = 0.0
    missed_since: float | None = None  # first check that didn't find it


class Tracker:
    def __init__(self, hold_s=0.3, match_iou=0.2, margin=0.03, margin_px=10):
        self.hold_s = hold_s
        self.match_iou = match_iou
        self.margin = margin
        self.margin_px = margin_px
        self.tracks = []
        self.bounds = (0, 0, 0, 0)
        self._ids = count(1)

    def update(self, dets, now, bounds, checked=None, refine=False):
        """dets: Detections found inside `checked` (x0, y0, x1, y1; None means
        everywhere). Shapes are clipped to `bounds` (x0, y0, x1, y1).
        refine: these are more thorough results for the same frame as the
        last update, so shapes are replaced, not trailed (nothing moved).
        Returns [(id, shape), ...]; see shapes.py."""
        self.bounds = bounds
        boxes = [np.array([d.x0, d.y0, d.x1, d.y1], dtype=float) for d in dets]
        unmatched = set(range(len(boxes)))
        matched_tracks = set()

        if self.tracks and boxes:
            ious = np.array([[iou(t.box, b) for b in boxes] for t in self.tracks])
            for ti, bi in sorted(np.ndindex(ious.shape), key=lambda ij: -ious[ij]):
                if ious[ti, bi] < self.match_iou:
                    break
                if bi in unmatched and ti not in matched_tracks:
                    matched_tracks.add(ti)
                    t = self.tracks[ti]
                    t.box, t.missed_since = boxes[bi], None
                    if not refine:
                        t.prev_shape, t.moved_at = t.shape, now
                    t.shape = self._shape(dets[bi])
                    unmatched.discard(bi)

        for ti, t in enumerate(self.tracks):
            # A region outside the checked area wasn't missed: its content
            # hasn't changed, so whatever was there still is.
            missed = ti not in matched_tracks and (checked is None or _center_inside(t.box, checked))
            if missed and t.missed_since is None:
                t.missed_since = now
        for bi in unmatched:
            self.tracks.append(Track(next(self._ids), boxes[bi], self._shape(dets[bi])))
        self.expire(now)
        return self.regions()

    def expire(self, now):
        """Drop regions missed for longer than the hold, and previous outlines
        older than it. Returns True if anything changed.

        Called on every update and also on a timer, so a region whose figure
        is gone disappears on time even if the window then stops changing,
        and a figure that stopped moving shrinks back to its own outline
        (otherwise e.g. a photo viewer's opening zoom would leave the blur
        covering where the photo was mid-animation, for good).
        """
        before = len(self.tracks)
        self.tracks = [t for t in self.tracks
                       if t.missed_since is None or now - t.missed_since <= self.hold_s]
        changed = len(self.tracks) != before
        for t in self.tracks:
            if t.prev_shape is not None and now - t.moved_at > self.hold_s:
                t.prev_shape = None
                changed = True
        return changed

    def regions(self):
        return [(t.id, union(t.shape, t.prev_shape)) for t in self.tracks if t.shape]

    def _shape(self, det):
        return figure_shape(det, self.margin, self.margin_px, self.bounds)


def _center_inside(box, rect):
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3]


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
