"""macOS backend (macOS 12 or later).

Each display is captured whole with CGWindowListCreateImage. Patches are
small borderless, click-through windows above everything else, with
sharingType = none so macOS leaves them out of screen captures, ours
included. Needs the Screen Recording permission (System Settings → Privacy
& Security → Screen Recording); macOS asks the first time.
"""

import logging
import signal
import time

import AppKit
import objc
import Quartz
from Foundation import NSObject

from ..frames import ArrayFrame
from .base import Backend, SourceInfo

log = logging.getLogger("hurmat")

NSWindowSharingNone = 0
LITTLE_BGRA = Quartz.kCGBitmapByteOrder32Little
ALPHA_FIRST = (Quartz.kCGImageAlphaPremultipliedFirst, Quartz.kCGImageAlphaNoneSkipFirst, Quartz.kCGImageAlphaFirst)


class _Ticker(NSObject):
    def initWithCallback_(self, cb):
        self = objc.super(_Ticker, self).init()
        if self is None:
            return None
        self.cb = cb
        return self

    def tick_(self, _timer):
        try:
            if self.cb() is False:
                _stop_app()
        except Exception:  # noqa: BLE001 - keep the event loop alive, log instead
            log.exception("tick failed")


class MacBackend(Backend):
    name = "macos"

    def __init__(self):
        self.app = AppKit.NSApplication.sharedApplication()
        # No Dock icon; a menu bar item instead (see run).
        self.app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.windows = {}  # (source key, patch id) → NSWindow
        self.status_item = None
        self.permission = self._check_permission()

    def _check_permission(self):
        preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        if preflight is None:
            return None
        if preflight():
            return True
        log.warning("Screen Recording permission is missing. Allow Hurmat Tasaaweer in System Settings → "
                    "Privacy & Security → Screen Recording, then start it again.")
        request = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
        if request:
            request()
        return False

    def _displays(self):
        err, ids, count = Quartz.CGGetActiveDisplayList(16, None, None)
        return list(ids[:count]) if err == 0 else []

    def sources(self):
        out = {}
        for did in self._displays():
            b = Quartz.CGDisplayBounds(did)  # points, top-left origin, global
            out[did] = SourceInfo(int(b.origin.x), int(b.origin.y), int(b.size.width), int(b.size.height),
                                  label=f"display {did}")
        return out

    def grab(self, key):
        bounds = Quartz.CGDisplayBounds(key)
        img = Quartz.CGWindowListCreateImage(bounds, Quartz.kCGWindowListOptionOnScreenOnly,
                                             Quartz.kCGNullWindowID, Quartz.kCGWindowImageDefault)
        if img is None:
            return None
        w, h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
        info = Quartz.CGImageGetBitmapInfo(img)
        if (Quartz.CGImageGetBitsPerPixel(img) == 32 and (info & Quartz.kCGBitmapByteOrderMask) == LITTLE_BGRA
                and (info & Quartz.kCGBitmapAlphaInfoMask) in ALPHA_FIRST):
            data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
            stride = Quartz.CGImageGetBytesPerRow(img)
            return ArrayFrame(bytes(data), w, h, stride=stride, pts_ns=time.monotonic_ns())
        # Unusual pixel format: redraw it as BGRA.
        space = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, space,
                                           Quartz.kCGImageAlphaPremultipliedFirst | LITTLE_BGRA)
        Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), img)
        data = Quartz.CGBitmapContextGetData(ctx).as_buffer(w * h * 4)
        return ArrayFrame(bytes(data), w, h, pts_ns=time.monotonic_ns())

    def show(self, key, info, patches):
        for kp in [kp for kp in self.windows if kp[0] == key and kp[1] not in patches]:
            self.windows.pop(kp).close()
        if not patches:
            return
        # Frames are in pixels; windows are placed in points. Retina
        # displays have 2 pixels per point.
        pixels_wide = Quartz.CGDisplayPixelsWide(key) or info.width
        mode = Quartz.CGDisplayCopyDisplayMode(key)
        if mode is not None:
            pixels_wide = Quartz.CGDisplayModeGetPixelWidth(mode)
        scale = pixels_wide / info.width if info.width else 1.0
        main_h = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID()).size.height
        for pid, p in patches.items():
            h, w = p.rgba.shape[:2]
            x, y = info.x + p.x / scale, info.y + p.y / scale
            wp, hp = w / scale, h / scale
            # Cocoa puts the origin at the main display's bottom-left.
            rect = AppKit.NSMakeRect(x, main_h - y - hp, wp, hp)
            win = self.windows.get((key, pid))
            if win is None:
                win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                    rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
                win.setReleasedWhenClosed_(False)
                win.setOpaque_(False)
                win.setBackgroundColor_(AppKit.NSColor.clearColor())
                win.setHasShadow_(False)
                win.setIgnoresMouseEvents_(True)
                win.setLevel_(AppKit.NSScreenSaverWindowLevel)
                win.setSharingType_(NSWindowSharingNone)
                win.setCollectionBehavior_(
                    AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                    | AppKit.NSWindowCollectionBehaviorStationary
                    | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                    | AppKit.NSWindowCollectionBehaviorIgnoresCycle)
                view = win.contentView()
                view.setWantsLayer_(True)
                self.windows[(key, pid)] = win
            else:
                win.setFrame_display_(rect, False)
            win.contentView().layer().setContents_(_cgimage(p.rgba))
            win.orderFrontRegardless()

    def close(self):
        for win in self.windows.values():
            win.close()
        self.windows.clear()

    def run(self, tick, interval_s=0.005):
        if self.status_item is None:
            bar = AppKit.NSStatusBar.systemStatusBar()
            self.status_item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
            self.status_item.button().setTitle_("HT")
            menu = AppKit.NSMenu.alloc().init()
            menu.addItemWithTitle_action_keyEquivalent_("Hurmat Tasaaweer is running", None, "")
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            menu.addItemWithTitle_action_keyEquivalent_("Quit", "terminate:", "q")
            self.status_item.setMenu_(menu)
        self._ticker = _Ticker.alloc().initWithCallback_(tick)
        timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            interval_s, self._ticker, "tick:", None, True)
        # Ctrl+C in a terminal: a Python handler runs at the next tick (the
        # timer keeps Python code running), and stops the loop cleanly.
        old = signal.signal(signal.SIGINT, lambda *_: _stop_app())
        try:
            # Not AppHelper.runEventLoop/stopEventLoop: stopEventLoop
            # *terminates* the app, so nothing after it (the self-test report,
            # removing our windows) would run.
            self.app.run()
        finally:
            signal.signal(signal.SIGINT, old)
            timer.invalidate()


def _stop_app():
    """Make NSApplication.run() return. stop: only takes effect after the
    next event, so post a harmless one."""
    app = AppKit.NSApplication.sharedApplication()
    app.stop_(None)
    event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
        AppKit.NSEventTypeApplicationDefined, AppKit.NSMakePoint(0, 0), 0, 0, 0, None, 0, 0, 0)
    app.postEvent_atStart_(event, True)


def _cgimage(rgba):
    """A CGImage from an RGBA array whose alpha is 0 or 255."""
    h, w = rgba.shape[:2]
    data = rgba.copy()
    data[data[:, :, 3] == 0] = 0  # premultiplied
    provider = Quartz.CGDataProviderCreateWithCFData(AppKit.NSData.dataWithBytes_length_(data.tobytes(), data.nbytes))
    return Quartz.CGImageCreate(w, h, 8, 32, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
                                Quartz.kCGImageAlphaPremultipliedLast | Quartz.kCGBitmapByteOrderDefault,
                                provider, None, False, Quartz.kCGRenderingIntentDefault)
