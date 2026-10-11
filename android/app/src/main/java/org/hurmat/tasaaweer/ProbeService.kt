package org.hurmat.tasaaweer

import android.accessibilityservice.AccessibilityService
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.Rect
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.Display
import android.view.Surface
import android.view.SurfaceControl
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import java.io.File
import java.util.concurrent.Executor

/**
 * Experiments for the Android version, triggered from a computer with:
 *
 *   adb shell am broadcast -a org.hurmat.tasaaweer.PROBE --es test rate
 *
 * Tests: rate (screenshot speed), overlay (is our overlay left out of the
 * window's screenshot?), nodes (image descriptions apps expose).
 * Results go to logcat, tag "Hurmat"; screenshots to the app's files dir.
 */
class ProbeService : AccessibilityService() {

    private val main = Handler(Looper.getMainLooper())
    private val executor = Executor { main.post(it) }
    private var receiver: BroadcastReceiver? = null

    override fun onServiceConnected() {
        log("service connected")
        receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                when (val test = intent.getStringExtra("test")) {
                    "rate" -> rateTest()
                    "overlay" -> overlayTest()
                    "nodes" -> nodesTest()
                    "mixed" -> mixedTest()
                    "scroll" -> scrollTest()
                    else -> log("unknown test '$test' (use rate, overlay, nodes, mixed or scroll)")
                }
            }
        }
        // Exported so that adb (the shell user) can trigger tests.
        registerReceiver(receiver, IntentFilter(ACTION), Context.RECEIVER_EXPORTED)
    }

    override fun onDestroy() {
        receiver?.let { unregisterReceiver(it) }
        super.onDestroy()
    }

    private var scrollLogUntil = 0L

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        if (SystemClock.uptimeMillis() > scrollLogUntil) return
        if (event.eventType == AccessibilityEvent.TYPE_VIEW_SCROLLED) {
            val r = Rect().also { event.source?.getBoundsInScreen(it) }
            log("scroll: +${SystemClock.uptimeMillis() - scrollStart} ms dy=${event.scrollDeltaY} dx=${event.scrollDeltaX} " +
                "scrollY=${event.scrollY} view=${event.className?.toString()?.substringAfterLast('.')} $r")
        } else if (event.eventType == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED) {
            contentChanges++
        }
    }

    // --- 4. Is the screenshot limit shared between window and display shots? ---

    private fun mixedTest() {
        val win = appWindow() ?: return log("mixed: no app window")
        var n = 0
        measureRate("alternating window/display", 3000) { cb ->
            if (n++ % 2 == 0) takeScreenshotOfWindow(win.id, executor, cb)
            else takeScreenshot(Display.DEFAULT_DISPLAY, executor, cb)
        }
    }

    // --- 5. What do scroll events tell us, and how often? ---

    private var scrollStart = 0L
    private var contentChanges = 0

    private fun scrollTest() {
        scrollStart = SystemClock.uptimeMillis()
        contentChanges = 0
        scrollLogUntil = scrollStart + 4000
        log("scroll: logging scroll events for 4 s")
        main.postDelayed({ log("scroll: done, $contentChanges content-changed events meanwhile") }, 4100)
    }

    override fun onInterrupt() {}

    /** The app window the user is looking at (not system UI, not us). */
    private fun appWindow(): AccessibilityWindowInfo? =
        windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
            .let { apps -> apps.firstOrNull { it.isActive } ?: apps.firstOrNull { it.isFocused } ?: apps.firstOrNull() }

    // --- 1. How fast can we take screenshots? ---

    private fun rateTest() {
        val win = appWindow() ?: return log("rate: no app window")
        log("rate: window ${win.id} '${win.title}' ${bounds(win)}")
        measureRate("window", 3000) { cb -> takeScreenshotOfWindow(win.id, executor, cb) }
        main.postDelayed({
            measureRate("display", 3000) { cb -> takeScreenshot(Display.DEFAULT_DISPLAY, executor, cb) }
        }, 3500)
    }

    /** Request screenshots back-to-back for durationMs and report what we got. */
    private fun measureRate(label: String, durationMs: Long, take: (TakeScreenshotCallback) -> Unit) {
        val start = SystemClock.uptimeMillis()
        var ok = 0
        val errors = mutableMapOf<Int, Int>()
        val latencies = mutableListOf<Long>()
        var size = ""

        fun next() {
            if (SystemClock.uptimeMillis() - start >= durationMs) {
                val secs = (SystemClock.uptimeMillis() - start) / 1000.0
                val lat = latencies.sorted()
                log(
                    "rate[$label]: %.1f screenshots/s (%d ok in %.1fs), errors %s, latency median %d ms, max %d ms, %s".format(
                        ok / secs, ok, secs, errors.mapKeys { errorName(it.key) },
                        lat.getOrElse(lat.size / 2) { -1 }, lat.lastOrNull() ?: -1, size
                    )
                )
                return
            }
            val t0 = SystemClock.uptimeMillis()
            take(object : TakeScreenshotCallback {
                override fun onSuccess(result: ScreenshotResult) {
                    latencies += SystemClock.uptimeMillis() - t0
                    val hb = result.hardwareBuffer
                    size = "${hb.width}x${hb.height}"
                    hb.close()
                    ok++
                    next()
                }

                override fun onFailure(code: Int) {
                    errors[code] = (errors[code] ?: 0) + 1
                    // Don't spin: a refused request is retried after a short pause.
                    main.postDelayed({ next() }, 10)
                }
            })
        }
        next()
    }

    // --- 2. Is an overlay attached to a window left out of its screenshot? ---

    private fun overlayTest() {
        val win = appWindow() ?: return log("overlay: no app window")
        val b = bounds(win)
        val w = b.width() / 2
        val h = b.height() / 4
        val sc = SurfaceControl.Builder().setName("hurmat-probe").setBufferSize(w, h).build()
        Surface(sc).also { surface ->
            val canvas = surface.lockHardwareCanvas()
            canvas.drawColor(Color.TRANSPARENT, PorterDuff.Mode.CLEAR)
            canvas.drawRect(0f, 0f, w.toFloat(), h.toFloat(), Paint().apply { color = Color.RED })
            surface.unlockCanvasAndPost(canvas)
            surface.release()
        }
        // Positions are relative to the window it's attached to.
        val x = (b.width() - w) / 2f
        val y = (b.height() - h) / 2f
        SurfaceControl.Transaction().setVisibility(sc, true).setPosition(sc, x, y).apply()
        attachAccessibilityOverlayToWindow(win.id, sc)
        log("overlay: red ${w}x$h box attached to window ${win.id} '${win.title}' at (${x.toInt()},${y.toInt()})")

        // Give the compositor a moment, then look at both kinds of screenshot.
        main.postDelayed({
            takeScreenshotOfWindow(win.id, executor, checkRed("window", (x + w / 2).toInt(), (y + h / 2).toInt()))
            main.postDelayed({
                takeScreenshot(Display.DEFAULT_DISPLAY, executor,
                    checkRed("display", (b.left + x + w / 2).toInt(), (b.top + y + h / 2).toInt()))
            }, 400)
        }, 500)
        // Leave it up a few seconds so it can be seen (and scrolled under), then remove it.
        main.postDelayed({
            SurfaceControl.Transaction().reparent(sc, null).apply()
            sc.release()
            log("overlay: removed")
        }, 6000)
    }

    private fun checkRed(label: String, cx: Int, cy: Int) = object : TakeScreenshotCallback {
        override fun onSuccess(result: ScreenshotResult) {
            val hw = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
            val bmp = hw?.copy(Bitmap.Config.ARGB_8888, false)
            result.hardwareBuffer.close()
            if (bmp == null) return log("overlay[$label]: couldn't read screenshot")
            val px = bmp.getPixel(cx.coerceIn(0, bmp.width - 1), cy.coerceIn(0, bmp.height - 1))
            val red = Color.red(px) > 200 && Color.green(px) < 60 && Color.blue(px) < 60
            val file = File(getExternalFilesDir(null), "overlay_$label.png")
            file.outputStream().use { bmp.compress(Bitmap.CompressFormat.PNG, 100, it) }
            log(
                "overlay[$label]: ${bmp.width}x${bmp.height}, pixel at overlay centre #%06X → overlay %s screenshot (saved %s)".format(
                    px and 0xFFFFFF, if (red) "IS IN" else "is NOT in", file.absolutePath
                )
            )
        }

        override fun onFailure(code: Int) = log("overlay[$label]: screenshot failed: ${errorName(code)}")
    }

    // --- 3. What do apps say about their images? ---

    private fun nodesTest() {
        val win = appWindow() ?: return log("nodes: no app window")
        val root = win.root ?: return log("nodes: window ${win.id} has no content (yet)")
        log("nodes: window '${win.title}' package ${root.packageName}")
        var count = 0
        fun visit(node: AccessibilityNodeInfo, depth: Int) {
            val cls = node.className?.toString() ?: ""
            val desc = node.contentDescription?.toString()
            val isImage = cls.contains("Image") || node.collectionItemInfo == null && desc != null && cls.endsWith("View")
            if (isImage && count < 80) {
                val r = Rect().also { node.getBoundsInScreen(it) }
                if (r.width() >= 48 || r.height() >= 48) {
                    count++
                    log("nodes:   ${cls.substringAfterLast('.')} $r ${node.viewIdResourceName ?: ""} \"${desc ?: ""}\"")
                }
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let { visit(it, depth + 1) }
        }
        visit(root, 0)
        log("nodes: $count image-like nodes (48px+)")
    }

    // --- helpers ---

    private fun bounds(win: AccessibilityWindowInfo) = Rect().also { win.getBoundsInScreen(it) }

    private fun errorName(code: Int) = when (code) {
        ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR -> "INTERNAL_ERROR"
        ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "NO_ACCESS"
        ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "TOO_SOON"
        ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY -> "INVALID_DISPLAY"
        ERROR_TAKE_SCREENSHOT_INVALID_WINDOW -> "INVALID_WINDOW"
        ERROR_TAKE_SCREENSHOT_SECURE_WINDOW -> "SECURE_WINDOW"
        else -> "code $code"
    }

    private fun log(msg: String) {
        Log.i(TAG, msg)
    }

    companion object {
        const val TAG = "Hurmat"
        const val ACTION = "org.hurmat.tasaaweer.PROBE"
    }
}
