package org.hurmat.tasaaweer

import android.content.res.AssetManager
import android.graphics.Bitmap
import android.graphics.Rect
import kotlin.math.max
import kotlin.math.min

/** JNI wrapper around ncnn (hurmat_jni.cpp). */
object NativeDetector {
    init {
        System.loadLibrary("hurmat")
    }

    /** Returns a model handle, or 0 if loading failed. */
    external fun load(assets: AssetManager, param: String, model: String, useGpu: Boolean, threads: Int): Long
    external fun free(handle: Long)
    external fun usesGpu(handle: Long): Boolean
    external fun run(handle: Long, bitmap: Bitmap, x: Int, y: Int, w: Int, h: Int, imgsz: Int,
                     meta: FloatArray, shape: IntArray): FloatArray?
    external fun protos(handle: Long, shape: IntArray): FloatArray?
}

/**
 * A detected figure, in the screenshot's pixel coordinates. For segmentation
 * models, `mask` is the outline: a maskW x maskH grid covering maskBox. For
 * pose models, `kpts` holds 17 COCO keypoints as (x, y, confidence).
 */
class Detection(
    val label: String,
    val score: Float,
    var x0: Float, var y0: Float, var x1: Float, var y1: Float,
    val mask: BooleanArray? = null,
    val maskW: Int = 0,
    val maskH: Int = 0,
    var maskBox: FloatArray? = null,  // x0, y0, x1, y1
    var kpts: FloatArray? = null,
    var ellipse: Boolean = false,      // blur an oval filling the box (face mode)
) {
    fun maxSide() = max(x1 - x0, y1 - y0)
    fun box() = floatArrayOf(x0, y0, x1, y1)

    fun shift(dx: Float, dy: Float) {
        x0 += dx; x1 += dx; y0 += dy; y1 += dy
        maskBox?.let { maskBox = floatArrayOf(it[0] + dx, it[1] + dy, it[2] + dx, it[3] + dy) }
        kpts?.let { k -> for (i in 0 until 17) { k[i * 3] += dx; k[i * 3 + 1] += dy } }
    }
}

/**
 * YOLO11 detection of people and animals: plain, -seg (outlines) or -pose
 * (people only, with body keypoints). Port of hurmat/detector.py.
 */
class Detector(assets: AssetManager, name: String, useGpu: Boolean = true, threads: Int = 2,
               private val conf: Float = 0.25f, private val iou: Float = 0.45f, val imgsz: Int = 640) {

    private val handle = NativeDetector.load(assets, "$name.param", "$name.bin", useGpu, threads)
    private val pose = name.contains("pose")
    private val numClasses = if (pose) 1 else NUM_CLASSES
    private val classes = if (pose) mapOf(0 to "person") else LIVING_CLASSES

    init {
        check(handle != 0L) { "can't load model $name" }
    }

    val onGpu get() = NativeDetector.usesGpu(handle)

    /** Detections inside roi of bitmap, in bitmap coordinates. */
    fun detect(bitmap: Bitmap, roi: Rect): List<Detection> {
        val meta = FloatArray(3)
        val shape = IntArray(2)
        val out = NativeDetector.run(handle, bitmap, roi.left, roi.top, roi.width(), roi.height(), imgsz, meta, shape)
            ?: return emptyList()
        val (scale, left, top) = meta
        val rows = shape[0]
        val n = shape[1]
        val segment = !pose && rows > 4 + NUM_CLASSES

        // Candidates: anchors whose best living-being class beats the threshold.
        val cand = ArrayList<IntArray>()  // [anchor, class]
        val scores = ArrayList<Float>()
        for (a in 0 until n) {
            var best = -1
            var bestScore = conf
            for (c in classes.keys) {
                val s = out[(4 + c) * n + a]
                if (s >= bestScore) { bestScore = s; best = c }
            }
            if (best >= 0) { cand += intArrayOf(a, best); scores += bestScore }
        }
        if (cand.isEmpty()) return emptyList()

        val boxes = cand.map { (a, _) ->
            val cx = out[a]; val cy = out[n + a]; val bw = out[2 * n + a]; val bh = out[3 * n + a]
            floatArrayOf(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
        }
        val keep = nms(boxes, scores, iou)

        var protos: FloatArray? = null
        val pShape = IntArray(3)
        if (segment) protos = NativeDetector.protos(handle, pShape)

        fun toRoi(v: Float, offset: Float, limit: Int) = ((v - offset) / scale).coerceIn(0f, limit.toFloat())
        return keep.map { i ->
            val (a, c) = cand[i]
            val b = boxes[i]
            val det = Detection(
                classes.getValue(c), scores[i],
                toRoi(b[0], left, roi.width()) + roi.left, toRoi(b[1], top, roi.height()) + roi.top,
                toRoi(b[2], left, roi.width()) + roi.left, toRoi(b[3], top, roi.height()) + roi.top,
            )
            if (pose) {
                val base = 4 + numClasses
                det.kpts = FloatArray(17 * 3) { j ->
                    val v = out[(base + j) * n + a]
                    when (j % 3) {
                        0 -> (v - left) / scale + roi.left
                        1 -> (v - top) / scale + roi.top
                        else -> v
                    }
                }
            }
            if (protos == null) det else withMask(det, protos, pShape, out, n, a, b, scale, left, top, roi)
        }
    }

    /** Decode the figure's outline inside its box from the mask prototypes. */
    private fun withMask(det: Detection, protos: FloatArray, pShape: IntArray, out: FloatArray, n: Int,
                         anchor: Int, box: FloatArray, scale: Float, left: Float, top: Float, roi: Rect): Detection {
        val (pc, ph, pw) = pShape
        val cell = imgsz.toFloat() / pw
        // Boxes can reach (or pass) the input's edge: keep the grid inside it.
        val mx0 = (box[0] / cell).toInt().coerceIn(0, pw - 1)
        val my0 = (box[1] / cell).toInt().coerceIn(0, ph - 1)
        val mx1 = kotlin.math.ceil(box[2] / cell).toInt().coerceIn(mx0 + 1, pw)
        val my1 = kotlin.math.ceil(box[3] / cell).toInt().coerceIn(my0 + 1, ph)
        val w = mx1 - mx0
        val h = my1 - my0
        val coeff = FloatArray(pc) { out[(4 + NUM_CLASSES + it) * n + anchor] }
        val mask = BooleanArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            var logit = 0f
            val off = (my0 + y) * pw + mx0 + x
            for (k in 0 until pc) logit += coeff[k] * protos[k * ph * pw + off]
            mask[y * w + x] = logit > 0f  // sigmoid > 0.5
        }
        val mb = floatArrayOf(
            (mx0 * cell - left) / scale + roi.left, (my0 * cell - top) / scale + roi.top,
            (mx1 * cell - left) / scale + roi.left, (my1 * cell - top) / scale + roi.top,
        )
        return Detection(det.label, det.score, det.x0, det.y0, det.x1, det.y1, mask, w, h, mb, det.kpts)
    }

    companion object {
        const val NUM_CLASSES = 80  // COCO

        // COCO ids of living beings. Teddy bear (77) is left out, as on the desktop.
        val LIVING_CLASSES = mapOf(
            0 to "person", 14 to "bird", 15 to "cat", 16 to "dog", 17 to "horse", 18 to "sheep",
            19 to "cow", 20 to "elephant", 21 to "bear", 22 to "zebra", 23 to "giraffe",
        )

        fun nms(boxes: List<FloatArray>, scores: List<Float>, thresh: Float): List<Int> {
            val order = scores.indices.sortedByDescending { scores[it] }.toMutableList()
            val keep = ArrayList<Int>()
            while (order.isNotEmpty()) {
                val i = order.removeAt(0)
                keep += i
                order.removeAll { boxIou(boxes[i], boxes[it]) >= thresh }
            }
            return keep
        }

        fun boxIou(a: FloatArray, b: FloatArray): Float {
            val ix = max(0f, min(a[2], b[2]) - max(a[0], b[0]))
            val iy = max(0f, min(a[3], b[3]) - max(a[1], b[1]))
            val inter = ix * iy
            val union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
            return if (union > 0) inter / union else 0f
        }
    }
}
