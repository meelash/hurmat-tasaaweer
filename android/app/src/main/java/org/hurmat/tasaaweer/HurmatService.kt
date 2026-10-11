package org.hurmat.tasaaweer

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.SurfaceControl
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import java.util.concurrent.Executor
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

/**
 * Blurs people and animals in whichever app is open.
 *
 *   window screenshot (≤3/s, never includes our overlay)
 *     → change check → detection (ncnn, GPU) in overlapping tiles
 *     → tracker (hold, trailing outline)    ← scroll events shift regions
 *     → BlurOverlay attached to the window
 *
 * Threads: screenshots and detection on `worker`; tracker and overlay on main.
 */
class HurmatService : AccessibilityService() {

    private val main = Handler(Looper.getMainLooper())
    private val workerThread = HandlerThread("hurmat-detect").apply { start() }
    private val worker = Handler(workerThread.looper)
    private val workerExec = Executor { worker.post(it) }

    private lateinit var detector: Detector
    private var poseDetector: Detector? = null
    private var mode = People.Mode.FIGURE
    private val tracker = Tracker()
    private var overlay: BlurOverlay? = null
    private var windowId = -1          // the app window being watched (main thread)
    @Volatile private var shotWindow = -1  // read by the worker
    @Volatile private var running = false

    private var minSizePx = 0f
    private var tileAspect = TILE_ASPECT
    private var lastSamples: IntArray? = null  // worker: last checked frame's sample grid
    private var scrollSeq = 0L                  // main: count of scroll events seen
    private val scrolls = ArrayDeque<Scroll>()  // main: recent scroll events

    private class Scroll(val seq: Long, val dx: Float, val dy: Float, val view: FloatArray)

    override fun onServiceConnected() {
        // Settings for experiments, pushed from the computer (probe.sh set ...).
        val config = java.util.Properties()
        java.io.File(getExternalFilesDir(null), "config.properties").takeIf { it.exists() }
            ?.inputStream()?.use { config.load(it) }
        val model = config.getProperty("model", MODEL)
        mode = People.Mode.valueOf(config.getProperty("mode", "figure").uppercase())
        val poseModel = config.getProperty("poseModel", POSE_MODEL)
        tileAspect = config.getProperty("tileAspect", TILE_ASPECT.toString()).toFloat()
        minSizePx = config.getProperty("minSizeDp", MIN_SIZE_DP.toString()).toFloat() * resources.displayMetrics.density
        worker.post {
            val t = SystemClock.uptimeMillis()
            val gpu = config.getProperty("gpu", "false").toBoolean()
            val threads = config.getProperty("threads", THREADS.toString()).toInt()
            detector = Detector(assets, model, gpu, threads)
            // "none" turns the pose check off: every detected person is blurred whole.
            if (poseModel != "none") poseDetector = Detector(assets, poseModel, gpu, threads)
            log("models $model + $poseModel loaded in ${SystemClock.uptimeMillis() - t} ms, mode $mode, gpu=${detector.onGpu}, " +
                "min size ${minSizePx.toInt()} px, tile aspect $tileAspect")
            main.post { running = true; syncWindow(); scheduleShot(0) }
        }
    }

    override fun onDestroy() {
        running = false
        overlay?.destroy()
        workerThread.quitSafely()
        super.onDestroy()
    }

    override fun onInterrupt() {}

    override fun onAccessibilityEvent(event: AccessibilityEvent) {
        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOWS_CHANGED, AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> syncWindow()
            AccessibilityEvent.TYPE_VIEW_SCROLLED -> if (event.windowId == windowId) onScroll(event)
        }
    }

    // --- window tracking (main thread) ---

    private fun syncWindow() {
        if (!running) return
        val win = windows.filter { it.type == AccessibilityWindowInfo.TYPE_APPLICATION }
            .let { a -> a.firstOrNull { it.isActive } ?: a.firstOrNull { it.isFocused } ?: a.firstOrNull() }
        val id = win?.id ?: -1
        if (id == windowId) return
        overlay?.destroy()
        overlay = null
        tracker.clear()
        scrolls.clear()
        windowId = id
        shotWindow = id
        worker.post { lastSamples = null }
        if (id >= 0) {
            overlay = BlurOverlay { sc -> attachAccessibilityOverlayToWindow(id, sc) }
            log("watching window $id '${win?.title}'")
        }
    }

    private fun onScroll(event: AccessibilityEvent) {
        val dx = -event.scrollDeltaX.toFloat()
        val dy = -event.scrollDeltaY.toFloat()
        if (dx == 0f && dy == 0f) return
        val r = Rect().also { event.source?.getBoundsInScreen(it) }
        val wb = currentWindowBounds()
        val view = floatArrayOf((r.left - wb.left).toFloat(), (r.top - wb.top).toFloat(),
            (r.right - wb.left).toFloat(), (r.bottom - wb.top).toFloat())
        scrolls.addLast(Scroll(++scrollSeq, dx, dy, view))
        while (scrolls.size > 200) scrolls.removeFirst()

        // Content moved: move the blurs with it right away.
        if (tracker.shift(dx, dy, view)) {
            val t = SurfaceControl.Transaction()
            val ov = overlay ?: return
            for (tr in tracker.tracks) if (centreIn(tr.box, view, -dx, -dy)) ov.move(tr.id, dx, dy, t)
            t.apply()
        }
    }

    private fun currentWindowBounds(): Rect {
        val r = Rect()
        windows.firstOrNull { it.id == windowId }?.getBoundsInScreen(r)
        return r
    }

    // --- screenshot loop (worker thread) ---

    private var lastShotAt = 0L

    private fun scheduleShot(delayMs: Long) {
        worker.postDelayed({ takeShot() }, delayMs)
    }

    private fun takeShot() {
        if (!running) return
        val win = shotWindow
        if (win < 0) return scheduleShot(IDLE_MS)
        lastShotAt = SystemClock.uptimeMillis()
        val shotAt = lastShotAt
        val seq = runOnMainSync { scrollSeq }
        takeScreenshotOfWindow(win, workerExec, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val t0 = SystemClock.uptimeMillis()
                val hw = Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                val bmp = hw?.copy(Bitmap.Config.ARGB_8888, false)
                result.hardwareBuffer.close()
                if (bmp != null && win == shotWindow) process(win, bmp, seq, shotAt, t0)
                next()
            }

            override fun onFailure(code: Int) {
                if (code == ERROR_TAKE_SCREENSHOT_SECURE_WINDOW) log("window $win blocks screenshots; can't check it")
                next(if (code == ERROR_TAKE_SCREENSHOT_SECURE_WINDOW) IDLE_MS else 0)
            }
        })
    }

    private fun next(extra: Long = 0) {
        val wait = max(0, lastShotAt + SHOT_INTERVAL_MS - SystemClock.uptimeMillis()) + extra
        scheduleShot(wait)
    }

    private fun process(win: Int, bmp: Bitmap, seq: Long, shotAt: Long, t0: Long) {
        // Skip detection when nothing visible changed since the last check.
        val samples = sample(bmp)
        val prev = lastSamples
        if (prev != null && changedSamples(prev, samples) < MIN_CHANGED_SAMPLES) {
            // Content is unchanged, so this screenshot can redraw shrunken regions.
            main.post { if (win == windowId && tracker.expire(SystemClock.uptimeMillis())) redrawAll(bmp, seq) }
            return
        }
        lastSamples = samples
        val t1 = SystemClock.uptimeMillis()

        val tiles = tiles(bmp.width, bmp.height)
        val found = runAll(detector, bmp, tiles)
        val t2 = SystemClock.uptimeMillis()
        val merged = poseDetector?.let { People.apply(mode, found, runAll(it, bmp, tiles)) } ?: found
        val t3 = SystemClock.uptimeMillis()

        main.post {
            if (win != windowId) return@post
            val now = SystemClock.uptimeMillis()
            // Content may have scrolled since the screenshot: bring detections up to date.
            for (s in scrolls) if (s.seq > seq) for (d in merged) if (centreIn(d.box(), s.view, 0f, 0f)) d.shift(s.dx, s.dy)
            tracker.update(merged, now, floatArrayOf(0f, 0f, bmp.width.toFloat(), bmp.height.toFloat()))
            redrawAll(bmp, seq)
            log("checked %dx%d: wait %d ms, convert %d, detect %d, pose %d (%d tiles), %d found → %d blurred, shown %d ms after shot".format(
                bmp.width, bmp.height, t0 - shotAt, t1 - t0, t2 - t1, t3 - t2, tiles.size, found.size, merged.size, now - shotAt))
        }
    }

    /** Redraw every region from `source` (or just drop expired ones if null). Main thread. */
    private fun redrawAll(source: Bitmap?, seq: Long) {
        val ov = overlay ?: return
        val t = SurfaceControl.Transaction()
        val live = tracker.tracks.map { it.id }.toSet()
        for (id in ov.ids() - live) ov.remove(id, t)
        if (source != null) {
            for (tr in tracker.tracks) {
                // Where this region's content was in the screenshot.
                var dx = 0f
                var dy = 0f
                for (s in scrolls) if (s.seq > seq && centreIn(tr.box, s.view, 0f, 0f)) { dx -= s.dx; dy -= s.dy }
                ov.draw(tr.id, tr.region(), source, dx, dy, t)
            }
        }
        t.apply()
    }

    // --- helpers ---

    /** One model over all tiles; duplicates from overlapping tiles merged; small figures dropped. */
    private fun runAll(d: Detector, bmp: Bitmap, tiles: List<Rect>): List<Detection> {
        val dets = tiles.flatMap { d.detect(bmp, it) }
        return Detector.nms(dets.map { it.box() }, dets.map { it.score }, 0.5f)
            .map { dets[it] }.filter { it.maxSide() >= minSizePx }
    }

    /** Overlapping tiles of near-square shape covering the window, so tall phone windows aren't shrunk too far. */
    private fun tiles(w: Int, h: Int): List<Rect> {
        val n = max(1, ceil(h / (w * tileAspect)).toInt())
        if (n == 1) return listOf(Rect(0, 0, w, h))
        val th = min(h, ceil(h / n.toFloat()).toInt() + (w * 0.15f).toInt())
        return (0 until n).map { i -> val top = i * (h - th) / (n - 1); Rect(0, top, w, top + th) }
    }

    private fun sample(bmp: Bitmap): IntArray {
        val out = IntArray(SAMPLE_W * SAMPLE_H)
        for (y in 0 until SAMPLE_H) for (x in 0 until SAMPLE_W)
            out[y * SAMPLE_W + x] = bmp.getPixel(((x + 0.5f) * bmp.width / SAMPLE_W).toInt(),
                ((y + 0.5f) * bmp.height / SAMPLE_H).toInt())
        return out
    }

    private fun changedSamples(a: IntArray, b: IntArray): Int {
        var n = 0
        for (i in a.indices) {
            val p = a[i]; val q = b[i]
            val d = max(abs((p shr 16 and 255) - (q shr 16 and 255)),
                max(abs((p shr 8 and 255) - (q shr 8 and 255)), abs((p and 255) - (q and 255))))
            if (d > 24) n++
        }
        return n
    }

    private fun centreIn(box: FloatArray, view: FloatArray, dx: Float, dy: Float): Boolean {
        val cx = (box[0] + box[2]) / 2 + dx
        val cy = (box[1] + box[3]) / 2 + dy
        return cx >= view[0] && cx <= view[2] && cy >= view[1] && cy <= view[3]
    }

    private fun <T> runOnMainSync(block: () -> T): T {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val lock = Object()
        var result: Any? = null
        var done = false
        main.post { synchronized(lock) { result = block(); done = true; lock.notifyAll() } }
        synchronized(lock) { while (!done) lock.wait() }
        @Suppress("UNCHECKED_CAST") return result as T
    }

    private fun log(msg: String) = Log.i("Hurmat", msg)

    companion object {
        // Measured on the razr 2025 (Dimensity 7400X): ncnn on 4 CPU cores runs
        // the nano model in ~100 ms for the whole window; its Vulkan path on
        // the Mali GPU took ~180 ms, and the small model or two tiles 2-3x more.
        const val MODEL = "yolo11n-seg"
        const val POSE_MODEL = "yolo11n-pose"
        const val THREADS = 4
        // Tiles are up to (width × this) tall. 2.5 checks a phone window in
        // one go (shrunk ~4x); 1.3 splits it in two, for smaller figures at
        // about twice the cost.
        const val TILE_ASPECT = 2.5f
        const val MIN_SIZE_DP = 64f      // figures smaller than this on screen are left alone
        const val SHOT_INTERVAL_MS = 340L  // Android allows one window screenshot per ~333 ms
        const val IDLE_MS = 1000L
        const val SAMPLE_W = 48
        const val SAMPLE_H = 96
        const val MIN_CHANGED_SAMPLES = 6
    }
}
