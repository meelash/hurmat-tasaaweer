package org.hurmat.tasaaweer

import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

/** A horizontal slice of a blur shape. A shape is a list of non-overlapping bands, top to bottom. */
data class Band(val y0: Float, val y1: Float, val x0: Float, val x1: Float) {
    fun shifted(dx: Float, dy: Float) = Band(y0 + dy, y1 + dy, x0 + dx, x1 + dx)
}

/** Port of hurmat/shapes.py: a figure's outline as padded horizontal bands. */
object Shapes {
    private const val BAND_PX = 24f
    private const val MIN_BANDS = 3
    private const val MAX_BANDS = 24

    fun figure(det: Detection, margin: Float, marginPx: Float, bounds: FloatArray): List<Band> {
        val padX = (det.x1 - det.x0) * margin + marginPx
        val padY = (det.y1 - det.y0) * margin + marginPx
        if (det.ellipse) return clip(oval(det.x0 - padX, det.y0 - padY, det.x1 + padX, det.y1 + padY), bounds)
        val whole = listOf(Band(det.y0 - padY, det.y1 + padY, det.x0 - padX, det.x1 + padX))
        val spans = outlineSpans(det) ?: return clip(whole, bounds)

        val bands = ArrayList<FloatArray>()  // y0, y1, x0, x1
        for (i in spans.indices) {
            // Each band takes its neighbours' width too, so small movements stay covered.
            val near = spans.subList(max(0, i - 1), min(spans.size, i + 2)).filter { it[2] >= 0 }
            if (near.isEmpty()) continue
            bands += floatArrayOf(spans[i][0], spans[i][1], near.minOf { it[2] } - padX, near.maxOf { it[3] } + padX)
        }
        if (bands.isEmpty()) return clip(whole, bounds)
        bands.first()[0] -= padY
        bands.last()[1] += padY
        return clip(merge(bands.map { Band(it[0], it[1], it[2], it[3]) }), bounds)
    }

    /** Per band over the detection box: [y0, y1, xMin, xMax], xMin = -1 for empty bands. */
    private fun outlineSpans(det: Detection): List<FloatArray>? {
        val mask = det.mask ?: return null
        val mb = det.maskBox ?: return null
        val mw = det.maskW
        val mh = det.maskH
        val cw = (mb[2] - mb[0]) / mw
        val ch = (mb[3] - mb[1]) / mh
        val c0 = max(0, ((det.x0 - mb[0]) / cw).toInt())
        val c1 = min(mw, ceil((det.x1 - mb[0]) / cw).toInt())
        val r0 = max(0, ((det.y0 - mb[1]) / ch).toInt())
        val r1 = min(mh, ceil((det.y1 - mb[1]) / ch).toInt())
        if (c1 <= c0 || r1 <= r0) return null

        val n = ((det.y1 - det.y0) / BAND_PX).roundToInt().coerceIn(MIN_BANDS, MAX_BANDS)
        val rows = r1 - r0
        val spans = ArrayList<FloatArray>()
        var any = false
        for (i in 0 until n) {
            val ra = r0 + (rows * i.toFloat() / n).roundToInt()
            val rb = max(r0 + (rows * (i + 1).toFloat() / n).roundToInt(), ra + 1)
            var lo = -1
            var hi = -1
            for (r in ra until min(rb, r1)) for (c in c0 until c1) if (mask[r * mw + c]) {
                if (lo < 0 || c < lo) lo = c
                if (c > hi) hi = c
            }
            val ya = mb[1] + ra * ch
            val yb = mb[1] + rb * ch
            if (lo >= 0) {
                any = true
                spans += floatArrayOf(ya, yb, mb[0] + lo * cw, mb[0] + (hi + 1) * cw)
            } else {
                spans += floatArrayOf(ya, yb, -1f, -1f)
            }
        }
        return if (any) spans else null
    }

    /** An oval filling the box, as bands. Each band is as wide as the oval gets within it. */
    private fun oval(x0: Float, y0: Float, x1: Float, y1: Float): List<Band> {
        val n = ((y1 - y0) / BAND_PX).roundToInt().coerceIn(6, MAX_BANDS)
        val cx = (x0 + x1) / 2
        val cy = (y0 + y1) / 2
        val rx = (x1 - x0) / 2
        val ry = (y1 - y0) / 2
        return (0 until n).map { i ->
            val ya = y0 + (y1 - y0) * i / n
            val yb = y0 + (y1 - y0) * (i + 1) / n
            // Distance from the centre line to the nearest edge of the band (0 if it spans it).
            val d = if (ya <= cy && yb >= cy) 0f else min(kotlin.math.abs(ya - cy), kotlin.math.abs(yb - cy))
            val half = rx * kotlin.math.sqrt(max(0f, 1 - (d / ry) * (d / ry)))
            Band(ya, yb, cx - half, cx + half)
        }
    }

    /** Union of two shapes (each band covers both). */
    fun union(a: List<Band>, b: List<Band>?): List<Band> {
        if (b.isNullOrEmpty()) return a
        if (a.isEmpty()) return b
        val all = a + b
        val ys = all.flatMap { listOf(it.y0, it.y1) }.distinct().sorted()
        val out = ArrayList<Band>()
        for (i in 0 until ys.size - 1) {
            val covering = all.filter { it.y0 <= ys[i] && it.y1 >= ys[i + 1] }
            if (covering.isNotEmpty()) out += Band(ys[i], ys[i + 1], covering.minOf { it.x0 }, covering.maxOf { it.x1 })
        }
        return merge(out)
    }

    private fun merge(bands: List<Band>): List<Band> {
        val out = ArrayList<Band>()
        for (b in bands) {
            val last = out.lastOrNull()
            if (last != null && kotlin.math.abs(last.y1 - b.y0) < 1e-3 && last.x0 == b.x0 && last.x1 == b.x1) {
                out[out.size - 1] = last.copy(y1 = b.y1)
            } else out += b
        }
        return out
    }

    private fun clip(bands: List<Band>, bounds: FloatArray) = bands.mapNotNull {
        val b = Band(max(bounds[1], it.y0), min(bounds[3], it.y1), max(bounds[0], it.x0), min(bounds[2], it.x1))
        if (b.y1 > b.y0 && b.x1 > b.x0) b else null
    }
}

/**
 * Port of hurmat/tracker.py: turns per-screenshot detections into stable
 * blur regions (held briefly after a miss; covering current and previous
 * outline while moving), plus scroll shifting between screenshots.
 */
class Tracker(private val holdMs: Long = 300, private val matchIou: Float = 0.2f,
              private val margin: Float = 0.03f, private val marginPx: Float = 10f) {

    class Track(val id: Int, var box: FloatArray, var shape: List<Band>, var prevShape: List<Band>? = null,
                var missedSince: Long? = null, var movedAt: Long = 0) {
        fun region() = Shapes.union(shape, prevShape)
    }

    val tracks = ArrayList<Track>()
    private var nextId = 1

    fun update(dets: List<Detection>, now: Long, bounds: FloatArray) {
        val boxes = dets.map { floatArrayOf(it.x0, it.y0, it.x1, it.y1) }
        val unmatched = boxes.indices.toMutableSet()
        val matched = HashSet<Int>()
        val pairs = tracks.indices.flatMap { t -> boxes.indices.map { b -> Triple(t, b, Detector.boxIou(tracks[t].box, boxes[b])) } }
            .filter { it.third >= matchIou }.sortedByDescending { it.third }
        for ((ti, bi, _) in pairs) {
            if (bi !in unmatched || ti in matched) continue
            matched += ti
            unmatched -= bi
            val t = tracks[ti]
            t.box = boxes[bi]
            t.missedSince = null
            t.prevShape = t.shape
            t.movedAt = now
            t.shape = Shapes.figure(dets[bi], margin, marginPx, bounds)
        }
        tracks.forEachIndexed { ti, t -> if (ti !in matched && t.missedSince == null) t.missedSince = now }
        for (bi in unmatched) tracks += Track(nextId++, boxes[bi], Shapes.figure(dets[bi], margin, marginPx, bounds))
        expire(now)
    }

    /**
     * Drop regions missed for longer than the hold, and previous outlines
     * older than it (so a figure that stopped moving shrinks back to its own
     * outline). True if anything changed.
     */
    fun expire(now: Long): Boolean {
        val before = tracks.size
        tracks.removeAll { it.missedSince != null && now - it.missedSince!! > holdMs }
        var changed = tracks.size != before
        for (t in tracks) if (t.prevShape != null && now - t.movedAt > holdMs) {
            t.prevShape = null
            changed = true
        }
        return changed
    }

    /** Move regions whose centre is inside the scrolled view, by the scroll distance. */
    fun shift(dx: Float, dy: Float, view: FloatArray): Boolean {
        var any = false
        for (t in tracks) {
            val cx = (t.box[0] + t.box[2]) / 2
            val cy = (t.box[1] + t.box[3]) / 2
            if (cx < view[0] || cx > view[2] || cy < view[1] || cy > view[3]) continue
            t.box = floatArrayOf(t.box[0] + dx, t.box[1] + dy, t.box[2] + dx, t.box[3] + dy)
            t.shape = t.shape.map { it.shifted(dx, dy) }
            t.prevShape = t.prevShape?.map { it.shifted(dx, dy) }
            any = true
        }
        return any
    }

    fun clear() = tracks.clear()
}
