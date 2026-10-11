"""The platform-independent part of hurmat: decides what to blur in a stream
of frames from one source (a window, or a whole monitor).

A platform layer (GNOME's daemon.py, or the desktop/ backends) captures
frames, hands them to the Engine, and draws the regions it gets back.
"""

import logging
import time
from dataclasses import dataclass, field

import ncnn
import numpy as np

from . import people
from .changes import ChangeTracker
from .detector import Detector, nms
from .paths import models_dir
from .pictures import pack, picture_regions, render, tiles, to_screen
from .tracker import Tracker

log = logging.getLogger("hurmat")

CROP_PAD = 96   # context (px) checked around a changed area
# Re-check picture-like areas at full size when the whole-area pass had to
# shrink the image below this scale.
FULL_SIZE_SCALE = 0.75
TILE_SCALE = 0.67   # large picture areas are checked in tiles at this scale
MAX_VIEWS = 9       # extra areas checked per check (each runs every model), to bound the delay
BGRA = ncnn.Mat.PixelType.PIXEL_BGRA2RGB

DEFAULT_MODEL = "yolo11s-seg_ncnn_model"
DEFAULT_POSE_MODEL = "yolo11s-pose_ncnn_model"


@dataclass
class Source:
    """Detection state for one stream of frames."""
    tracker: Tracker
    changes: ChangeTracker = field(default_factory=ChangeTracker)
    pending: tuple | None = None  # (frame, content rect, changed rect) waiting to be checked
    last_checked: float = 0.0

    def drop_pending(self):
        if self.pending:
            self.pending[0].release()
            self.pending = None


class Engine:
    def __init__(self, detector, args, pose=None):
        self.detector = detector
        self.pose = pose
        self.args = args

    def new_source(self):
        return Source(Tracker(hold_s=self.args.hold))

    def offer(self, src, frame, rect=None):
        """A new frame for src. rect (x, y, w, h) is the part of the frame
        worth checking (default: all of it). Keeps the frame if anything
        changed enough to need a check; otherwise releases it."""
        if rect is None:
            rect = (0, 0, frame.width, frame.height)
        else:
            x, y, w, h = rect
            x, y = max(0, min(x, frame.width - 1)), max(0, min(y, frame.height - 1))
            rect = (x, y, max(1, min(w, frame.width - x)), max(1, min(h, frame.height - y)))
        changed = src.changes.changed_rect(frame, rect)
        src.drop_pending()  # superseded by a newer frame
        # Nothing changed, or too little to hold a figure we'd blur. Skipped
        # changes still count towards the next comparison (it's against the
        # last *checked* frame), so they add up.
        if changed is None or max(changed[2] - changed[0], changed[3] - changed[1]) < self.args.min_size:
            frame.release()
            return
        src.pending = (frame, rect, changed)

    @staticmethod
    def pick(sources):
        """The (key, source) to check next, or None. One check at a time,
        starting with the source that has gone longest without one: new
        content usually appears where things were quiet, so it jumps ahead
        of sources that change every frame (video, streaming text)."""
        ready = [(k, s) for k, s in sources.items() if s.pending]
        return min(ready, key=lambda item: item[1].last_checked) if ready else None

    def check(self, src, send, label="", age_ms=None):
        """Run detection on src's pending frame and pass its regions
        [(id, shape)] to send(regions, frame). send may be called twice: once
        early with the fast pass's results, then with the final ones. The
        frame is released afterwards."""
        frame, rect, changed = src.pending
        src.pending = None
        try:
            self._check(src, frame, rect, changed, send, label, age_ms)
        finally:
            frame.release()

    def _check(self, src, frame, rect, changed, send, label, age_ms):
        t0 = time.perf_counter()
        # Check the changed area plus some context, so a figure that only
        # partly changed (e.g. moved in a video) is still seen whole.
        rx0, ry0, rx1, ry1 = rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]
        cx0, cy0 = max(rx0, changed[0] - CROP_PAD), max(ry0, changed[1] - CROP_PAD)
        cx1, cy1 = min(rx1, changed[2] + CROP_PAD), min(ry1, changed[3] + CROP_PAD)
        bounds, checked = (rx0, ry0, rx1, ry1), (cx0, cy0, cx1, cy1)

        sent_early = [False]

        def send_early(dets):
            # Blur what the fast whole-area pass found right away; the
            # full-size picture checks can take ~100 ms more.
            send(src.tracker.update(dets, time.monotonic(), bounds, checked=checked), frame)
            sent_early[0] = True

        dets, runs = self.detect(frame, checked, send_early)
        src.changes.mark_checked(rect)
        src.last_checked = time.monotonic()
        regions = src.tracker.update(dets, src.last_checked, bounds, checked=checked,
                                     refine=sent_early[0])
        log.debug("%s %dx%d at %.4f: %schecked %dx%d (%d runs), %d dets → %d regions in %.0fms",
                  label, frame.width, frame.height, time.monotonic(),
                  f"queued {age_ms:.0f}ms, " if age_ms is not None else "",
                  cx1 - cx0, cy1 - cy0, runs, len(dets), len(regions),
                  (time.perf_counter() - t0) * 1e3)
        send(regions, frame)

    def detect(self, frame, crop, send_early=None):
        """Figures to blur inside crop (frame coordinates), and how many
        detector runs it took. If more runs follow the first pass, its
        results are passed to send_early."""
        cx0, cy0, cx1, cy1 = crop
        cw, ch = cx1 - cx0, cy1 - cy0
        size = self.detector.imgsz
        models = [self.detector] + ([self.pose] if self.pose else [])

        # The whole area at once: finds large figures, and anything not
        # photo-like (cartoons with flat colours).
        found = [[d.moved(cx0, cy0) for d in m.detect(
            frame.rows(cy0, cy1)[cx0 * 4:], BGRA, cw, ch, frame.stride)] for m in models]
        runs = len(models)

        # If that pass had to shrink the area, small pictures in it may have
        # become too small to recognise. Check picture-like areas again at
        # (closer to) full size: small ones packed into mosaics, large ones
        # (or a grid of thumbnails that merged into one) in overlapping tiles.
        if max(cw, ch) > size / FULL_SIZE_SCALE:
            if send_early:
                send_early(self._finish(found))
            mosaics, large = pack(picture_regions(frame, crop, self.args.min_size), size)
            views = [("mosaic", pl) for pl in mosaics]
            views += [("tile", t) for region in large for t in tiles(region, size, TILE_SCALE)]
            if len(views) > MAX_VIEWS:
                log.debug("%d picture areas, checking the first %d", len(views), MAX_VIEWS)
            for kind, view in views[:MAX_VIEWS]:
                canvas = render(frame, view, size) if kind == "mosaic" else None
                for i, m in enumerate(models):
                    if kind == "mosaic":
                        found[i] += [d for d in (to_screen(d, view) for d in m.detect(
                            canvas, BGRA, size, size, size * 4)) if d]
                    else:
                        x0, y0, x1, y1 = view
                        found[i] += [d.moved(x0, y0) for d in m.detect(
                            frame.rows(y0, y1)[x0 * 4:], BGRA, x1 - x0, y1 - y0, frame.stride)]
                    runs += 1
        return self._finish(found), runs

    def _finish(self, found):
        """Merge each model's passes, drop small figures, apply the people rules."""
        merged = []
        for dets in found:
            if dets:
                # The same figure may be found by several passes.
                xyxy = np.array([d.box() for d in dets])
                dets = [dets[i] for i in nms(xyxy, np.array([d.score for d in dets]), 0.5)]
            merged.append(self._big_enough(dets))
        result = people.apply(self.args.mode, merged[0], merged[1]) if self.pose else merged[0]
        if log.isEnabledFor(logging.DEBUG):
            log.debug("  blurring: %s", ", ".join(
                f"{d.label}{'(face)' if d.ellipse else ''} {d.score:.2f} @{d.x0:.0f},{d.y0:.0f}-{d.x1:.0f},{d.y1:.0f}"
                for d in result) or "nothing")
        return result

    def _big_enough(self, dets):
        # Figures this small on screen are permissible; don't blur them.
        return [d for d in dets if max(d.x1 - d.x0, d.y1 - d.y0) >= self.args.min_size]


def add_engine_args(p, max_fps=15):
    """Options shared by every platform."""
    p.add_argument("--model", default=None,
                   help=f"ncnn model directory (default: {DEFAULT_MODEL} in the models folder)")
    p.add_argument("--pose-model", default=None,
                   help="pose model directory, used to judge people by their visible body parts; "
                        f"'none' blurs every detected person whole (default: {DEFAULT_POSE_MODEL})")
    p.add_argument("--mode", choices=["figure", "face"], default="figure",
                   help="figure: whole outline if a head or most of a torso is visible "
                        "(lone hands/legs are left alone); face: only an oval around the head")
    p.add_argument("--conf", type=float, default=0.25, help="detection confidence threshold")
    p.add_argument("--min-size", type=float, default=48,
                   help="ignore figures whose longest side is under this many pixels")
    p.add_argument("--hold", type=float, default=0.3,
                   help="seconds to keep blurring after a figure was last detected")
    p.add_argument("--max-fps", type=int, default=max_fps,
                   help="cap frames per second captured per source (0 = no cap)")
    p.add_argument("--cpu", action="store_true", help="run detection on CPU instead of the GPU")
    p.add_argument("-v", "--verbose", action="store_true")


def load_engine(args):
    """Load the models named in args (see add_engine_args) and warm them up."""
    model = args.model or str(models_dir() / DEFAULT_MODEL)
    pose_model = args.pose_model or str(models_dir() / DEFAULT_POSE_MODEL)
    detector = Detector(model, conf=args.conf, use_gpu=not args.cpu)
    # The first inference builds GPU pipelines and is several times slower;
    # do it now rather than on the first real image.
    blank = np.zeros((64, 64, 3), np.uint8)
    detector.infer(detector.preprocess(blank, ncnn.Mat.PixelType.PIXEL_RGB, 64, 64, 64 * 3)[0])
    log.info("model %s @%d on %s", model.rstrip("/").split("/")[-1], detector.imgsz,
             "GPU" if detector.on_gpu else "CPU")
    pose = None
    if pose_model != "none":
        pose = Detector(pose_model, conf=args.conf, use_gpu=not args.cpu)
        pose.infer(pose.preprocess(blank, ncnn.Mat.PixelType.PIXEL_RGB, 64, 64, 64 * 3)[0])
        log.info("pose model %s, mode %s", pose_model.rstrip("/").split("/")[-1], args.mode)
    return Engine(detector, args, pose)


def setup_logging(args, log_file=None):
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=handlers)

