package org.hurmat.tasaaweer

import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.RecordingCanvas
import android.graphics.Rect
import android.graphics.RenderEffect
import android.graphics.RenderNode
import android.graphics.Shader
import android.view.Surface
import android.view.SurfaceControl
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.max

/**
 * Blur layers attached to one app window (accessibility overlay). Window
 * screenshots never include these layers, so the detector always sees the
 * clean content.
 *
 * There's no public API to blur what's behind a layer, so each region's
 * layer holds a blurred copy of the window screenshot, cut to the figure's
 * outline. Scrolling moves layers (cheap, no redraw); a new detection
 * redraws them from the newer screenshot.
 */
class BlurOverlay(attach: (SurfaceControl) -> Unit) {

    private val root = SurfaceControl.Builder().setName("hurmat-root").build()
    private val layers = HashMap<Int, Layer>()

    private class Layer(val sc: SurfaceControl, var x: Float, var y: Float, val w: Int, val h: Int)

    init {
        attach(root)
        // Stack above the window's own child layers: video players (YouTube)
        // and browsers (Brave/Chrome) draw on separate layers over the
        // window, which would otherwise cover the blur.
        SurfaceControl.Transaction().setVisibility(root, true).setLayer(root, Int.MAX_VALUE).apply()
    }

    /**
     * Redraw region `id` (bands in current window coordinates) from `source`,
     * whose content is offset by (srcDx, srcDy) from the current position
     * (content scrolled since the screenshot).
     */
    fun draw(id: Int, bands: List<Band>, source: Bitmap, srcDx: Float, srcDy: Float, t: SurfaceControl.Transaction) {
        if (bands.isEmpty()) return remove(id, t)
        val left = floor(bands.minOf { it.x0 })
        val top = floor(bands.minOf { it.y0 })
        val w = max(1, ceil(bands.maxOf { it.x1 } - left).toInt())
        val h = max(1, ceil(bands.maxOf { it.y1 } - top).toInt())

        var layer = layers[id]
        if (layer == null || layer.w != w || layer.h != h) {
            layer?.let { t.reparent(it.sc, null); it.sc.release() }
            val sc = SurfaceControl.Builder().setName("hurmat-$id").setParent(root).setBufferSize(w, h).build()
            layer = Layer(sc, left, top, w, h)
            layers[id] = layer
            t.setVisibility(sc, true)
        }
        layer.x = left
        layer.y = top

        // The part of the screenshot showing what's under this region now.
        val src = Rect((left + srcDx).toInt(), (top + srcDy).toInt(), (left + srcDx).toInt() + w, (top + srcDy).toInt() + h)
        val clipped = Rect(src).apply { intersect(0, 0, source.width, source.height) }

        val surface = Surface(layer.sc)
        val canvas = surface.lockHardwareCanvas()
        try {
            canvas.drawColor(0, android.graphics.PorterDuff.Mode.CLEAR)
            if (!clipped.isEmpty) {
                val node = RenderNode("blur").apply {
                    setPosition(0, 0, w, h)
                    val r = max(24f, 0.05f * minOf(w, h))
                    setRenderEffect(RenderEffect.createBlurEffect(r, r, Shader.TileMode.CLAMP))
                }
                val rec: RecordingCanvas = node.beginRecording()
                rec.drawBitmap(source, clipped,
                    Rect(clipped.left - src.left, clipped.top - src.top, clipped.right - src.left, clipped.bottom - src.top), null)
                node.endRecording()
                canvas.clipPath(Path().apply {
                    for (b in bands) addRect(b.x0 - left, b.y0 - top, b.x1 - left, b.y1 - top, Path.Direction.CW)
                })
                canvas.drawRenderNode(node)
            }
        } finally {
            surface.unlockCanvasAndPost(canvas)
            surface.release()
        }
        t.setPosition(layer.sc, left, top)
    }

    /** Move regions with scrolled content. */
    fun move(id: Int, dx: Float, dy: Float, t: SurfaceControl.Transaction) {
        val layer = layers[id] ?: return
        layer.x += dx
        layer.y += dy
        t.setPosition(layer.sc, layer.x, layer.y)
    }

    fun remove(id: Int, t: SurfaceControl.Transaction) {
        layers.remove(id)?.let { t.reparent(it.sc, null); it.sc.release() }
    }

    fun ids(): Set<Int> = layers.keys

    fun destroy() {
        val t = SurfaceControl.Transaction()
        for (l in layers.values) { t.reparent(l.sc, null) }
        t.reparent(root, null).apply()
        layers.values.forEach { it.sc.release() }
        layers.clear()
        root.release()
    }
}
