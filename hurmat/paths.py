"""Where bundled data lives, both from a source checkout and in a packaged
(PyInstaller) build."""

import os
import sys
from pathlib import Path


def resource_root():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def models_dir():
    return Path(os.environ.get("HURMAT_MODELS", resource_root() / "models"))


def assets_dir():
    return resource_root() / "assets"
