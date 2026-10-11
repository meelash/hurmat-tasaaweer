"""Step 1 prototype: capture the screen, detect people/animals, report timing.

Nothing is blurred yet. The point is to measure whether detection on this
GPU is fast and accurate enough to drive a compositor-side blur.

    python prototype/main.py                       # live screen capture
    python prototype/main.py --image photo.jpg     # one image, prints detections
    python prototype/main.py --image photo.jpg --bench 200
"""

import argparse
import signal
import sys
import time
from collections import defaultdict
from pathlib import Path

import ncnn
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hurmat.detector import Detector  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = ROOT / "models" / "yolo11n_ncnn_model"


class Stats:
    def __init__(self):
        self.samples = defaultdict(list)

    def add(self, name, ms):
        self.samples[name].append(ms)

    def report(self, elapsed_s):
        n = len(self.samples.get("infer", []))
        parts = [f"{n / elapsed_s:5.1f} det/s"]
        for name, vals in self.samples.items():
            a = np.array(vals)
            parts.append(f"{name} {a.mean():6.1f}ms (p95 {np.percentile(a, 95):6.1f})")
        self.samples.clear()
        return " | ".join(parts)


def detect(det, stats, pixels, pixel_type, w, h, stride):
    t0 = time.perf_counter()
    mat, scale, left, top = det.preprocess(pixels, pixel_type, w, h, stride)
    t1 = time.perf_counter()
    out = det.infer(mat)
    t2 = time.perf_counter()
    dets = det.postprocess(out, scale, left, top, w, h)
    t3 = time.perf_counter()
    stats.add("pre", (t1 - t0) * 1e3)
    stats.add("infer", (t2 - t1) * 1e3)
    stats.add("post", (t3 - t2) * 1e3)
    return dets


def annotate(img, dets, path):
    draw = ImageDraw.Draw(img)
    for d in dets:
        draw.rectangle([d.x0, d.y0, d.x1, d.y1], outline=(255, 0, 0), width=3)
        draw.text((d.x0 + 4, d.y0 + 2), f"{d.label} {d.score:.2f}", fill=(255, 0, 0))
    img.save(path)


def summarize(dets):
    counts = defaultdict(int)
    for d in dets:
        counts[d.label] += 1
    return ", ".join(f"{n}×{label}" for label, n in sorted(counts.items())) or "nothing"


def run_image(det, args):
    img = Image.open(args.image).convert("RGB")
    pixels = np.asarray(img)
    w, h = img.size
    stats = Stats()
    for _ in range(args.warmup):
        detect(det, stats, pixels, ncnn.Mat.PixelType.PIXEL_RGB, w, h, w * 3)
    stats.samples.clear()

    t0 = time.perf_counter()
    for _ in range(max(1, args.bench)):
        dets = detect(det, stats, pixels, ncnn.Mat.PixelType.PIXEL_RGB, w, h, w * 3)
    print(stats.report(time.perf_counter() - t0))

    for d in dets:
        print(f"  {d.label:9s} {d.score:.2f}  ({d.x0:.0f},{d.y0:.0f})-({d.x1:.0f},{d.y1:.0f})")
    if args.save_dir:
        args.save_dir.mkdir(parents=True, exist_ok=True)
        out = args.save_dir / f"{Path(args.image).stem}_detected.png"
        annotate(img, dets, out)
        print(f"saved {out}")


def run_live(det, args):
    from hurmat.portal_capture import ScreenCapture

    print("Requesting screen capture (GNOME may ask which screen to share)...")
    cap = ScreenCapture()
    stop = False

    def on_sigint(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_sigint)
    stats = Stats()
    window_start = time.perf_counter()
    last_summary = None
    last_save = 0.0
    frames = 0
    start_time = time.monotonic()

    try:
        while not stop:
            t_wait = time.perf_counter()
            frame = cap.pull()
            if frame is None:
                continue
            stats.add("wait", (time.perf_counter() - t_wait) * 1e3)
            try:
                dets = detect(det, stats, frame.data, ncnn.Mat.PixelType.PIXEL_BGRA2RGB,
                              frame.width, frame.height, frame.stride)
                # Frame age: from when PipeWire delivered the frame to when its
                # detections are ready. This is the window in which unblurred
                # content would be visible without a blur-first design.
                stats.add("age", (cap.running_time_ns() - frame.pts_ns) / 1e6)

                summary = summarize(dets)
                save_due = args.save_dir and dets and time.monotonic() - last_save > args.save_every
                if save_due:
                    rows = np.frombuffer(frame.data, np.uint8).reshape(frame.height, frame.stride)
                    bgrx = rows[:, : frame.width * 4].reshape(frame.height, frame.width, 4)
                    img = Image.fromarray(bgrx[..., [2, 1, 0]].copy())
            finally:
                frame.release()

            frames += 1
            if summary != last_summary:
                print(f"[{time.strftime('%H:%M:%S')}] {frame.width}x{frame.height}: {summary}")
                last_summary = summary
            if save_due:
                args.save_dir.mkdir(parents=True, exist_ok=True)
                annotate(img, dets, args.save_dir / f"frame_{frames:06d}.png")
                last_save = time.monotonic()

            elapsed = time.perf_counter() - window_start
            if elapsed >= args.report_every:
                print("  " + stats.report(elapsed))
                window_start = time.perf_counter()
            if args.seconds and time.monotonic() - start_time > args.seconds:
                break
    finally:
        cap.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="ncnn model directory")
    p.add_argument("--conf", type=float, default=0.25, help="confidence threshold")
    p.add_argument("--cpu", action="store_true", help="run on CPU instead of Vulkan")
    p.add_argument("--image", help="detect on an image file instead of the screen")
    p.add_argument("--bench", type=int, default=1, help="with --image: repeat N times for timing")
    p.add_argument("--warmup", type=int, default=3, help="with --image: untimed warm-up runs")
    p.add_argument("--save-dir", type=Path, help="save annotated frames here")
    p.add_argument("--save-every", type=float, default=5.0, help="live: min seconds between saves")
    p.add_argument("--report-every", type=float, default=2.0, help="live: seconds between timing lines")
    p.add_argument("--seconds", type=float, default=0, help="live: stop after N seconds (0 = until Ctrl+C)")
    args = p.parse_args()

    det = Detector(args.model, conf=args.conf, use_gpu=not args.cpu)
    print(f"model {args.model.name} imgsz {det.imgsz} on {'GPU (Vulkan)' if det.on_gpu else 'CPU'}")
    if args.image:
        run_image(det, args)
    else:
        run_live(det, args)


if __name__ == "__main__":
    main()
