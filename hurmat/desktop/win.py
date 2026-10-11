"""Windows backend (Windows 10 version 2004 or later).

Each monitor is captured whole (GDI, via mss). Patches are small layered,
click-through, always-on-top windows marked WDA_EXCLUDEFROMCAPTURE, so
Windows leaves them out of every screen capture, ours included, and the
detector never sees its own blur. (The mark has to be set before a window
becomes layered; see show().)
"""

import ctypes
import logging
import time
from ctypes import wintypes as wt

import mss
import numpy as np

from ..frames import ArrayFrame
from .base import Backend, SourceInfo

log = logging.getLogger("hurmat")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WDA_EXCLUDEFROMCAPTURE = 0x11
ULW_ALPHA = 2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = wt.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
PM_REMOVE = 1
DIB_RGB_COLORS = 0
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def _sig(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, argtypes
    return fn


_sig(user32.DefWindowProcW, LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
_sig(user32.RegisterClassExW, wt.ATOM, ctypes.POINTER(WNDCLASSEXW))
_sig(user32.CreateWindowExW, wt.HWND, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
_sig(user32.DestroyWindow, wt.BOOL, wt.HWND)
_sig(user32.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
_sig(user32.SetWindowPos, wt.BOOL, wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
_sig(user32.SetWindowDisplayAffinity, wt.BOOL, wt.HWND, wt.DWORD)
_sig(user32.UpdateLayeredWindow, wt.BOOL, wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
     wt.HDC, ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD)
_sig(user32.GetDC, wt.HDC, wt.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wt.HWND, wt.HDC)
_sig(user32.PeekMessageW, wt.BOOL, ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT)
_sig(user32.TranslateMessage, wt.BOOL, ctypes.POINTER(wt.MSG))
_sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wt.MSG))
_sig(gdi32.CreateCompatibleDC, wt.HDC, wt.HDC)
_sig(gdi32.DeleteDC, wt.BOOL, wt.HDC)
_sig(gdi32.CreateDIBSection, wt.HBITMAP, wt.HDC, ctypes.POINTER(BITMAPINFOHEADER), wt.UINT,
     ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD)
_sig(gdi32.SelectObject, wt.HGDIOBJ, wt.HDC, wt.HGDIOBJ)
_sig(gdi32.DeleteObject, wt.BOOL, wt.HGDIOBJ)
_sig(kernel32.GetModuleHandleW, wt.HMODULE, wt.LPCWSTR)
_sig(user32.GetWindowLongPtrW, ctypes.c_ssize_t, wt.HWND, ctypes.c_int)
_sig(user32.SetWindowLongPtrW, ctypes.c_ssize_t, wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
GWL_EXSTYLE = -20


@WNDPROC
def _wndproc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class WindowsBackend(Backend):
    name = "windows"

    def __init__(self):
        # Work in physical pixels everywhere, so captures and patch windows
        # line up on scaled (e.g. 150%) displays.
        try:
            user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
            user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except AttributeError:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        self.sct = mss.mss()
        self.hinst = kernel32.GetModuleHandleW(None)
        self.cls = "HurmatTasaaweerPatch"
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=_wndproc,
                         hInstance=self.hinst, lpszClassName=self.cls)
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.windows = {}  # (source key, patch id) → hwnd
        self.warned_affinity = False
        self.capture_exclusion = None  # reported by the self-test
        self.last_topmost = 0.0

    def sources(self):
        # mss.monitors[0] is the whole virtual screen; the rest are monitors.
        return {i: SourceInfo(m["left"], m["top"], m["width"], m["height"], label=f"monitor {i}")
                for i, m in enumerate(self.sct.monitors[1:], start=1)}

    def grab(self, key):
        try:
            mon = self.sct.monitors[key]
            shot = self.sct.grab(mon)
        except (IndexError, mss.exception.ScreenShotError):
            return None
        return ArrayFrame(shot.raw, shot.width, shot.height, pts_ns=time.monotonic_ns())

    def show(self, key, info, patches):
        for kp in [kp for kp in self.windows if kp[0] == key and kp[1] not in patches]:
            user32.DestroyWindow(self.windows.pop(kp))
        for pid, p in patches.items():
            hwnd = self.windows.get((key, pid))
            if hwnd is None:
                # SetWindowDisplayAffinity refuses layered windows
                # (ERROR_NOT_ENOUGH_MEMORY), but a window that already has it
                # keeps it after becoming layered. So: create a plain window,
                # exclude it from capture, then make it layered and
                # click-through.
                hwnd = user32.CreateWindowExW(
                    WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                    self.cls, "Hurmat Tasaaweer", WS_POPUP, 0, 0, 1, 1, None, None, self.hinst, None)
                if not hwnd:
                    log.warning("CreateWindowEx failed: %s", ctypes.WinError(ctypes.get_last_error()))
                    continue
                self._exclude_from_capture(hwnd)
                ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)
                self.windows[(key, pid)] = hwnd
                self._paint(hwnd, info.x + p.x, info.y + p.y, p.rgba)
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            else:
                self._paint(hwnd, info.x + p.x, info.y + p.y, p.rgba)

    def _exclude_from_capture(self, hwnd):
        ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
        if not ok:
            err = ctypes.WinError(ctypes.get_last_error())
            self.capture_exclusion = f"failed: {err}"
            if not self.warned_affinity:
                self.warned_affinity = True
                log.warning("could not exclude blur windows from screen capture (needs Windows 10 2004+ "
                            "and desktop composition); blur will flicker: %s", err)
        elif self.capture_exclusion is None:
            self.capture_exclusion = "ok"

    def _paint(self, hwnd, x, y, rgba):
        h, w = rgba.shape[:2]
        # Premultiplied BGRA, top-down. Alpha is 0 or 255, so premultiplying
        # is just clearing the colour of transparent pixels.
        bgra = np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])
        bgra[bgra[:, :, 3] == 0] = 0
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        hdr = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        bmp = gdi32.CreateDIBSection(mem, ctypes.byref(hdr), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, bgra.ctypes.data, bgra.nbytes)
            old = gdi32.SelectObject(mem, bmp)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wt.POINT(x, y)), ctypes.byref(wt.SIZE(w, h)),
                                       mem, ctypes.byref(wt.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
            gdi32.SelectObject(mem, old)
        finally:
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)

    def close(self):
        for hwnd in self.windows.values():
            user32.DestroyWindow(hwnd)
        self.windows.clear()
        self._pump()

    def _pump(self):
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def run(self, tick, interval_s=0.005):
        try:
            while True:
                self._pump()
                now = time.monotonic()
                if now - self.last_topmost > 1.0:
                    # Other always-on-top windows can end up above ours; reassert.
                    self.last_topmost = now
                    for hwnd in self.windows.values():
                        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
                if tick() is False:
                    return
                time.sleep(interval_s)
        except KeyboardInterrupt:
            pass
