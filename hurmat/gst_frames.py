"""Pull raw frames out of a PipeWire stream with GStreamer."""

import ctypes
import re
from dataclasses import dataclass, field

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstApp, GstVideo  # noqa: E402,F401

Gst.init(None)


# PyGObject can only hand out a *copy* of a mapped buffer, and window
# streams are padded to monitor size (~20 MB per frame at 3440x1440). Mapping
# through libgstreamer directly gives zero-copy access, so only the pixels we
# actually read (a sampled thumbnail, a crop for detection) are touched.
_gst = ctypes.CDLL("libgstreamer-1.0.so.0")


class _MapInfo(ctypes.Structure):
    _fields_ = [
        ("memory", ctypes.c_void_p),
        ("flags", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("size", ctypes.c_size_t),
        ("maxsize", ctypes.c_size_t),
        ("user_data", ctypes.c_void_p * 4),
        ("_gst_reserved", ctypes.c_void_p * 4),
    ]


_gst.gst_buffer_map.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MapInfo), ctypes.c_int]
_gst.gst_buffer_map.restype = ctypes.c_int
_gst.gst_buffer_unmap.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MapInfo)]
_GST_MAP_READ = 1
_BUFFER_ADDR = re.compile(r"\(GstBuffer at (0x[0-9a-f]+)\)")


def _buffer_address(buf):
    match = _BUFFER_ADDR.search(repr(buf))
    if not match:
        raise RuntimeError(f"can't find the GstBuffer address in {buf!r}")
    return int(match.group(1), 16)


@dataclass
class Frame:
    """A mapped BGRx frame. `view` reads the buffer in place and is only
    valid until release()."""
    width: int
    height: int
    stride: int
    pts_ns: int
    buffer: Gst.Buffer
    view: memoryview = field(init=False)

    def __post_init__(self):
        self._addr = _buffer_address(self.buffer)
        self._info = _MapInfo()
        if not _gst.gst_buffer_map(self._addr, ctypes.byref(self._info), _GST_MAP_READ):
            raise RuntimeError("gst_buffer_map failed")
        self.view = memoryview((ctypes.c_ubyte * self._info.size).from_address(self._info.data)).cast("B")

    def rows(self, y0, y1):
        """Rows y0..y1 (full stride), in place."""
        return self.view[y0 * self.stride:y1 * self.stride]

    @property
    def data(self):
        """The whole frame, in place."""
        return self.view

    def release(self):
        if self.buffer is not None:
            self.view.release()
            _gst.gst_buffer_unmap(self._addr, ctypes.byref(self._info))
            self.buffer = None


class PipeWireFrames:
    """Keeps only the newest frame of a PipeWire node; pull() never blocks long."""

    def __init__(self, node_id, fd=None, max_fps=None):
        # BGRx is what GNOME's compositor produces natively, so videoconvert is
        # normally a passthrough. max-buffers=1 drop=true keeps only the newest
        # frame: the detector should always look at what's on screen now.
        fd_arg = f"fd={fd} " if fd is not None else ""
        # A frame-rate cap is passed through to the producer (Mutter), so it
        # also saves the compositor copying frames we would drop anyway.
        rate = f",max-framerate=(fraction){max_fps}/1" if max_fps else ""
        self.pipeline = Gst.parse_launch(
            f"pipewiresrc {fd_arg}path={node_id} do-timestamp=true keepalive-time=1000 "
            f"! video/x-raw{rate} ! videoconvert ! video/x-raw,format=BGRx "
            "! appsink name=sink max-buffers=1 drop=true sync=false"
        )
        self.sink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(Gst.State.PLAYING)

    def running_time_ns(self):
        return self.pipeline.get_clock().get_time() - self.pipeline.get_base_time()

    def pull(self, timeout_s=1.0):
        sample = self.sink.try_pull_sample(int(timeout_s * Gst.SECOND))
        if sample is None:
            return None
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buf = sample.get_buffer()
        return Frame(width=info.width, height=info.height, stride=info.stride[0],
                     pts_ns=buf.pts, buffer=buf)

    def close(self):
        self.pipeline.set_state(Gst.State.NULL)
