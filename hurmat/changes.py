"""Find which part of a window changed since it was last checked.

Detection is the expensive step, and most window updates are irrelevant to
it: a blinking cursor, a line of streaming text, a progress bar. Comparing a
coarse thumbnail against the last frame that was actually checked tells us
where (if anywhere) new content could have appeared, so detection can skip
the frame or run on just that area.
"""

import numpy as np

STEP = 8        # sample every STEP-th pixel in each direction
THRESHOLD = 24  # per-channel difference that counts as a change


class ChangeTracker:
    def __init__(self):
        self.reference = None  # thumbnail of the last checked frame
        self.ref_rect = None

    def changed_rect(self, frame, rect):
        """Bounding box (x0, y0, x1, y1) of what changed inside rect, or None.

        Compares against the last *checked* frame (see mark_checked), not the
        previous frame, so small changes that are skipped still accumulate
        until they're big enough to matter.
        """
        thumb = self._thumbnail(frame, rect)
        if self.reference is None or self.ref_rect != rect or self.reference.shape != thumb.shape:
            self._pending = thumb
            x, y, w, h = rect
            return (x, y, x + w, y + h)
        diff = np.abs(thumb - self.reference).max(axis=2) > THRESHOLD
        self._pending = thumb
        if not diff.any():
            return None
        rows = np.flatnonzero(diff.any(axis=1))
        cols = np.flatnonzero(diff.any(axis=0))
        x, y = rect[0], rect[1]
        return (x + cols[0] * STEP, y + rows[0] * STEP,
                x + (cols[-1] + 1) * STEP, y + (rows[-1] + 1) * STEP)

    def mark_checked(self, rect):
        """The frame last passed to changed_rect was run through detection."""
        self.reference = self._pending
        self.ref_rect = rect

    @staticmethod
    def _thumbnail(frame, rect):
        x, y, w, h = rect
        rows = np.frombuffer(frame.view, np.uint8, count=(y + h) * frame.stride)
        rows = rows.reshape(y + h, frame.stride)[y:y + h:STEP, x * 4:(x + w) * 4]
        # Only the sampled pixels are read and copied.
        return rows.reshape(-1, w, 4)[:, ::STEP, :3].astype(np.int16)
