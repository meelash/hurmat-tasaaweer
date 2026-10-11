"""A frame backed by plain memory (a screen grab), with the same interface as
gst_frames.Frame: BGRA (or BGRx) pixels, rows of `stride` bytes."""

import numpy as np


class ArrayFrame:
    def __init__(self, data, width, height, stride=None, pts_ns=0):
        self.width, self.height = width, height
        self.stride = stride or width * 4
        self.pts_ns = pts_ns
        self.view = memoryview(data).cast("B")
        if len(self.view) < self.stride * height:
            raise ValueError(f"frame data too short: {len(self.view)} < {self.stride * height}")

    def rows(self, y0, y1):
        return self.view[y0 * self.stride:y1 * self.stride]

    @property
    def data(self):
        return self.view

    def pixels(self):
        """(height, width, 4) uint8 view of the BGRA pixels."""
        rows = np.frombuffer(self.view, np.uint8, count=self.height * self.stride)
        return rows.reshape(self.height, self.stride)[:, :self.width * 4].reshape(self.height, self.width, 4)

    def release(self):
        pass
