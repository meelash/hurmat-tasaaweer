# PyInstaller recipe for the Windows, macOS and X11 desktop builds.
#   pyinstaller packaging/hurmat-tasaaweer.spec   (from the repo root, with models/ exported)
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
MODELS = ["yolo11s-seg_ncnn_model", "yolo11s-pose_ncnn_model"]
NAME = "HurmatTasaaweer"

datas = [(str(ROOT / "assets" / "test-people.jpg"), "assets")]
for m in MODELS:
    for f in ("model.ncnn.param", "model.ncnn.bin", "metadata.yaml"):
        datas.append((str(ROOT / "models" / m / f), f"models/{m}"))
datas += [(str(ROOT / "LICENSE"), "."), (str(ROOT / "NOTICE.md"), ".")]

hidden = ["hurmat.desktop.selftest", "hurmat.desktop.base"]
if sys.platform == "win32":
    hidden += ["hurmat.desktop.win"]
elif sys.platform == "darwin":
    hidden += ["hurmat.desktop.mac", "AppKit", "Quartz", "Foundation", "PyObjCTools.AppHelper"]
else:
    hidden += ["hurmat.desktop.x11", "Xlib.ext.composite", "Xlib.ext.shape"]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=["gi", "tkinter", "torch", "ultralytics", "cv2"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME,
          console=sys.platform != "darwin",  # Windows/Linux: keep a console for logs and Ctrl+C
          upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
if sys.platform == "darwin":
    app = BUNDLE(coll, name=f"{NAME}.app", bundle_identifier="org.hurmat.tasaaweer",
                 info_plist={
                     "LSUIElement": True,  # menu bar app, no Dock icon
                     "CFBundleShortVersionString": "0.1.0",
                     "NSHumanReadableCopyright": "AGPL-3.0",
                 })
