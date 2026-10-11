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


def main():
    from .. import engine as eng  # imports ncnn; after the OMP setting in hurmat/__init__
    p = argparse.ArgumentParser(prog="hurmat-tasaaweer", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    eng.add_engine_args(p, max_fps=10)
    p.add_argument("--self-test", action="store_true", help="check detection and capture, print a report, exit")
    p.add_argument("--log-file", default=None, help="also log here (default: a per-user log file)")
    args = p.parse_args()
    log_file = args.log_file or default_log_file()
    eng.setup_logging(args, log_file)
    log.info("Hurmat Tasaaweer starting on %s; log file %s", sys.platform, log_file)

    backend = make_backend()
    engine = eng.load_engine(args)
    if args.self_test:
        from .selftest import run
        sys.exit(run(backend, engine))

    from .base import DesktopApp
    app = DesktopApp(backend, engine, args)
    try:
        backend.run(app.tick)
    finally:
        log.info("shutting down")
        app.close()


if __name__ == "__main__":
    main()
