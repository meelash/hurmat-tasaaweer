"""Hurmat Tasaaweer for Windows, macOS and X11 Linux desktops.

    hurmat-tasaaweer              run until stopped (Ctrl+C, or Quit in the menu bar on macOS)
    hurmat-tasaaweer --self-test  check detection and screen capture, print a report

On GNOME (Wayland), use the GNOME version instead: it blurs inside the
compositor and is more capable.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("hurmat")


def default_log_file():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "HurmatTasaaweer"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "hurmat-tasaaweer"
    base.mkdir(parents=True, exist_ok=True)
    return base / "hurmat-tasaaweer.log"


def make_backend():
    if sys.platform == "win32":
        from .win import WindowsBackend
        return WindowsBackend()
    if sys.platform == "darwin":
        from .mac import MacBackend
        return MacBackend()
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" and not os.environ.get("HURMAT_ALLOW_XWAYLAND"):
        sys.exit("This is a Wayland session. X11 programs can't see or cover Wayland windows,\n"
                 "so this version would miss most of the screen. On GNOME, use the GNOME version.\n"
                 "(Set HURMAT_ALLOW_XWAYLAND=1 to run it anyway, for X11 windows only.)")
    from .x11 import X11Backend
    return X11Backend()


def gpu_works():
    """Run the models on the GPU in a separate process first: a broken GPU
    driver can crash the process outright (seen with virtual GPUs on macOS),
    and then we'd rather fall back to the CPU than not run at all."""
    import subprocess
    cmd = [sys.executable, "--probe-gpu"] if getattr(sys, "frozen", False) else \
          [sys.executable, "-m", "hurmat.desktop", "--probe-gpu"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "timed out"
    if r.returncode == 0 and "GPU-OK" in r.stdout:
        return "ok"
    if r.returncode == 3:
        return "no GPU found"
    return f"probe exited with {r.returncode}"


def probe_gpu(eng, args):
    import ncnn
    if ncnn.get_gpu_count() == 0:
        return 3
    engine = eng.load_engine(args)
    if not engine.detector.on_gpu:
        return 3
    from ..frames import ArrayFrame
    from ..paths import assets_dir
    import numpy as np
    from PIL import Image
    img = np.asarray(Image.open(assets_dir() / "test-people.jpg").convert("RGB"))
    h, w = img.shape[:2]
    bgra = np.dstack([img[..., 2], img[..., 1], img[..., 0], np.full((h, w), 255, np.uint8)])
    dets, _ = engine.detect(ArrayFrame(bgra.tobytes(), w, h), (0, 0, w, h))
    if sum(d.label == "person" for d in dets) < 3:
        return 4  # runs, but gives wrong answers
    print("GPU-OK", flush=True)
    return 0


def main():
    from .. import engine as eng  # imports ncnn; after the OMP setting in hurmat/__init__
    p = argparse.ArgumentParser(prog="hurmat-tasaaweer", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    eng.add_engine_args(p, max_fps=10)
    p.add_argument("--self-test", action="store_true", help="check detection and capture, print a report, exit")
    p.add_argument("--report", default=None, help="with --self-test: also write the report to this file")
    p.add_argument("--probe-gpu", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--log-file", default=None, help="also log here (default: a per-user log file)")
    args = p.parse_args()
    log_file = args.log_file or default_log_file()
    eng.setup_logging(args, log_file)
    # If native code (GPU driver, ncnn, the OS) crashes the process, still
    # leave a trace of where, in the log, for bug reports.
    import faulthandler
    faulthandler.enable(open(log_file, "a", encoding="utf-8"), all_threads=True)
    log.info("Hurmat Tasaaweer starting on %s; log file %s", sys.platform, log_file)

    if args.probe_gpu:
        sys.exit(probe_gpu(eng, args))
    gpu_note = None
    if not args.cpu:
        gpu_note = gpu_works()
        if gpu_note != "ok":
            log.warning("GPU detection doesn't work here (%s); using the CPU instead", gpu_note)
            args.cpu = True

    backend = make_backend()
    engine = eng.load_engine(args)
    if args.self_test:
        from .selftest import run
        sys.exit(run(backend, engine, extra={"gpu_probe": gpu_note or "skipped (--cpu)"}, report_path=args.report))

    from .base import DesktopApp
    app = DesktopApp(backend, engine, args)
    try:
        backend.run(app.tick)
    finally:
        log.info("shutting down")
        app.close()


if __name__ == "__main__":
    main()
