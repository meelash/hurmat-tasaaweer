"""--self-test: checks the parts that can be checked without a person
watching, and prints a report testers can paste into an issue.

1. detection: the models load and find the people in a bundled photo;
2. capture: a source can be grabbed, and how long it takes;
3. exclusion: a patch we draw does NOT show up in our own captures (if it
   did, the detector would see its own blur and the blur would flicker).
"""

import json
import logging
import platform
import sys
import time

import numpy as np
from PIL import Image

from ..patches import Patch
from ..paths import assets_dir

log = logging.getLogger("hurmat")

MARK = (255, 0, 255)  # magenta test patch


def run(backend, engine, extra=None, report_path=None):
    report = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "backend": backend.name,
        # (Not ncnn.get_gpu_count(): on a broken GPU driver that alone can
        # crash the process. The GPU probe in a child process covers it.)
        "gpu": engine.detector.on_gpu,
        **(extra or {}),
    }
    ok = True

    # 1. detection
    log.info("self-test: detection")
    try:
        img = np.asarray(Image.open(assets_dir() / "test-people.jpg").convert("RGB"))
        h, w = img.shape[:2]
        bgra = np.dstack([img[..., 2], img[..., 1], img[..., 0], np.full((h, w), 255, np.uint8)])
        from ..frames import ArrayFrame
        frame = ArrayFrame(bgra.tobytes(), w, h)
        t = time.perf_counter()
        dets, runs = engine.detect(frame, (0, 0, w, h))
        report["detection"] = {
            "people_found": sum(d.label == "person" for d in dets),
            "expected_at_least": 3,
            "ms": round((time.perf_counter() - t) * 1e3),
        }
        report["detection"]["ok"] = report["detection"]["people_found"] >= 3
    except Exception as e:  # noqa: BLE001 - a report is more useful than a crash
        report["detection"] = {"ok": False, "error": repr(e)}
    ok &= report["detection"]["ok"]

    # 2. capture + 3. exclusion
    try:
        log.info("self-test: capture")
        sources = backend.sources()
        report["sources"] = len(sources)
        if not sources:
            report["capture"] = {"ok": False, "error": "nothing to capture (no monitor/window found)"}
        else:
            key, info = max(sources.items(), key=lambda kv: kv[1].width * kv[1].height)
            t = time.perf_counter()
            frame = backend.grab(key)
            report["capture"] = {
                "ok": frame is not None,
                "size": [frame.width, frame.height] if frame else None,
                "ms": round((time.perf_counter() - t) * 1e3),
            }
            if frame is not None:
                log.info("self-test: exclusion")
                report["exclusion"] = _exclusion(backend, key, info)
                ok &= report["exclusion"].get("ok", True) is not False
        ok &= report["capture"]["ok"]
    except Exception as e:  # noqa: BLE001
        report["capture"] = {"ok": False, "error": repr(e)}
        ok = False
    finally:
        backend.close()

    if getattr(backend, "capture_exclusion", None):
        report.setdefault("exclusion", {})["set_window_display_affinity"] = backend.capture_exclusion
    report["ok"] = bool(ok)
    text = json.dumps(report, indent=2)
    print(text)
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0 if ok else 1


def _exclusion(backend, key, info):
    size = max(64, min(240, info.width // 4, info.height // 4))
    x, y = info.width // 2 - size // 2, info.height // 2 - size // 2
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[..., :3] = MARK
    rgba[..., 3] = 255
    backend.show(key, info, {"selftest": Patch(x, y, rgba)})
    deadline = time.monotonic() + 1.0
    backend.run(lambda: time.monotonic() < deadline)  # let the window system draw it
    frame = backend.grab(key)
    backend.show(key, info, {})
    if frame is None:
        return {"ok": None, "note": "could not grab after drawing the test patch"}
    px = frame.pixels()[y:y + size, x:x + size, :3].astype(int)
    magenta = (np.abs(px[..., 2] - 255) < 40) & (px[..., 1] < 60) & (np.abs(px[..., 0] - 255) < 40)
    share = float(magenta.mean())
    return {
        "ok": share < 0.05,
        "test_patch_visible_in_capture": round(share, 3),
        "note": "ok means our blur windows are invisible to our own capture, as they must be",
    }
