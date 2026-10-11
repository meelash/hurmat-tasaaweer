"""Diagnose SetWindowDisplayAffinity on a Windows machine (run in CI)."""
import ctypes, sys
from ctypes import wintypes as wt
sys.path.insert(0, ".")
from hurmat.desktop import win as W  # noqa: E402  (registers signatures and the window class helpers)

user32, dwm = W.user32, ctypes.WinDLL("dwmapi")
enabled = wt.BOOL()
print("DwmIsCompositionEnabled:", dwm.DwmIsCompositionEnabled(ctypes.byref(enabled)), bool(enabled.value))
print("Windows version:", sys.getwindowsversion())
b = W.WindowsBackend()
import numpy as np
for layered in (False, True):
    for when in ("before show", "after show"):
        for mode in (0x11, 0x01):
            ex = W.WS_EX_TOPMOST | W.WS_EX_TOOLWINDOW | (W.WS_EX_LAYERED if layered else 0)
            hwnd = user32.CreateWindowExW(ex, b.cls, "probe", W.WS_POPUP, 100, 100, 200, 200, None, None, b.hinst, None)
            if when == "after show":
                if layered:
                    b._paint(hwnd, 100, 100, np.full((200, 200, 4), 255, np.uint8))
                user32.ShowWindow(hwnd, W.SW_SHOWNOACTIVATE)
                b._pump()
            ok = user32.SetWindowDisplayAffinity(hwnd, mode)
            err = ctypes.get_last_error()
            print(f"layered={layered} {when:11s} mode=0x{mode:02x}: ok={bool(ok)} err={err}")
            user32.DestroyWindow(hwnd)
