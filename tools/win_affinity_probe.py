"""Diagnose SetWindowDisplayAffinity on a Windows machine (run in CI)."""
import ctypes, sys, time
from ctypes import wintypes as wt
sys.path.insert(0, ".")
import numpy as np  # noqa: E402
from hurmat.desktop import win as W  # noqa: E402

user32 = W.user32
GWL_EXSTYLE = -20
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.GetWindowDisplayAffinity.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
b = W.WindowsBackend()
magenta = np.zeros((200, 200, 4), np.uint8); magenta[..., 0] = 255; magenta[..., 2] = 255; magenta[..., 3] = 255


def visible_in_capture():
    t = time.monotonic()
    while time.monotonic() - t < 0.7:
        b._pump(); time.sleep(0.02)
    shot = b.sct.grab({"left": 150, "top": 150, "width": 100, "height": 100})
    px = np.frombuffer(shot.raw, np.uint8).reshape(100, 100, 4)
    return float(((px[..., 2] > 200) & (px[..., 1] < 60) & (px[..., 0] > 200)).mean())


for variant in ("affinity, then make layered", "layered with alpha 1 trick"):
    hwnd = user32.CreateWindowExW(W.WS_EX_TOPMOST | W.WS_EX_TOOLWINDOW | W.WS_EX_NOACTIVATE, b.cls, "probe",
                                  W.WS_POPUP, 100, 100, 200, 200, None, None, b.hinst, None)
    ok1 = user32.SetWindowDisplayAffinity(hwnd, 0x11); e1 = ctypes.get_last_error()
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | W.WS_EX_LAYERED | W.WS_EX_TRANSPARENT)
    b._paint(hwnd, 100, 100, magenta)
    user32.ShowWindow(hwnd, W.SW_SHOWNOACTIVATE)
    aff = wt.DWORD()
    user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(aff))
    print(f"{variant}: set={bool(ok1)} err={e1} affinity_after=0x{aff.value:02x} magenta_in_capture={visible_in_capture():.2f}")
    user32.DestroyWindow(hwnd)
    break

# Control: plain layered window without affinity, to confirm the capture sees windows at all.
hwnd = user32.CreateWindowExW(W.WS_EX_LAYERED | W.WS_EX_TRANSPARENT | W.WS_EX_TOPMOST | W.WS_EX_TOOLWINDOW, b.cls, "c",
                              W.WS_POPUP, 100, 100, 200, 200, None, None, b.hinst, None)
b._paint(hwnd, 100, 100, magenta); user32.ShowWindow(hwnd, W.SW_SHOWNOACTIVATE)
print(f"control (no affinity): magenta_in_capture={visible_in_capture():.2f}")
