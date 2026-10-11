package org.hurmat.tasaaweer

import kotlin.math.hypot
import kotlin.math.max

/**
 * Decides which detected people to blur, and how, using a pose model's body
 * keypoints (COCO order: nose, eyes, ears, shoulders, elbows, wrists, hips,
 * knees, ankles).
 *
 *   figure mode: the whole outline, but only if a head or most of a torso is
 *                visible; a hand, fingers or legs on their own are left alone.
 *   face mode:   an oval around the head only; people without a visible head
 *                are left alone.
 *
 * Animals have no keypoints and are always blurred whole.
 */
object People {
    enum class Mode { FIGURE, FACE }

    private const val VISIBLE = 0.5f
    // A person only one of the two models saw must be a confident find: weak
    // finds the other model doesn't back up are usually not people.
    private const val SOLO_SCORE = 0.5f
    private val HEAD = intArrayOf(0, 1, 2, 3, 4)
    private val TORSO = intArrayOf(5, 6, 11, 12)  // shoulders, hips

    /**
     * Apply the mode to outline-model detections, using pose detections to
     * judge each person. A person only one model found is kept if that model
     * was confident.
     */
    fun apply(mode: Mode, dets: List<Detection>, poses: List<Detection>): List<Detection> {
        val out = ArrayList<Detection>()
        val usedPoses = HashSet<Int>()
        for (d in dets) {
            if (d.label != "person") { out += d; continue }
            val pi = poses.indices.filter { it !in usedPoses }
                .maxByOrNull { Detector.boxIou(d.box(), poses[it].box()) }
                ?.takeIf { Detector.boxIou(d.box(), poses[it].box()) >= 0.4f }
            if (pi == null) { if (d.score >= SOLO_SCORE) out += d; continue }
            usedPoses += pi
            val k = poses[pi].kpts!!
            when (mode) {
                Mode.FIGURE -> if (hasHead(k) || hasTorso(k)) out += d
                Mode.FACE -> headBox(k, d.box())?.let { out += faceDetection(d, it) }
            }
        }
        // People only the pose model found (no outline): same rules, if confident.
        for (i in poses.indices) if (i !in usedPoses && poses[i].score >= SOLO_SCORE) {
            val p = poses[i]
            val k = p.kpts!!
            when (mode) {
                Mode.FIGURE -> if (hasHead(k) || hasTorso(k)) out += p
                Mode.FACE -> headBox(k, p.box())?.let { out += faceDetection(p, it) }
            }
        }
        return out
    }

    private fun visible(k: FloatArray, j: Int) = k[j * 3 + 2] > VISIBLE
    private fun hasHead(k: FloatArray) = HEAD.count { visible(k, it) } >= 2
    private fun hasTorso(k: FloatArray) = TORSO.count { visible(k, it) } >= 3

    private fun dist(k: FloatArray, a: Int, b: Int) =
        if (visible(k, a) && visible(k, b)) hypot(k[a * 3] - k[b * 3], k[a * 3 + 1] - k[b * 3 + 1]) else 0f

    /** Box around the head (with hair and chin), inside the person's box, or null if no head is visible. */
    private fun headBox(k: FloatArray, person: FloatArray): FloatArray? {
        if (!hasHead(k)) return null
        val pts = HEAD.filter { visible(k, it) }
        val cx = pts.map { k[it * 3] }.average().toFloat()
        val cy = pts.map { k[it * 3 + 1] }.average().toFloat()
        // Head width from the most reliable pair visible: ears sit at the
        // head's sides; eyes are about 0.4 of its width apart. Otherwise (a
        // profile) the spread of what's visible. Shoulders give a lower bound.
        var width = when {
            visible(k, 3) && visible(k, 4) -> dist(k, 3, 4) * 1.15f
            visible(k, 1) && visible(k, 2) -> dist(k, 1, 2) * 2.5f
            else -> pts.maxOf { a -> pts.maxOf { b -> dist(k, a, b) } } * 1.6f
        }
        width = max(width, dist(k, 5, 6) * 0.45f)
        // The keypoints sit around eye level: more head above them (hair) than below (chin).
        return floatArrayOf(
            max(cx - 0.6f * width, person[0]), max(cy - 0.75f * width, person[1]),
            kotlin.math.min(cx + 0.6f * width, person[2]), kotlin.math.min(cy + 0.65f * width, person[3]),
        )
    }

    private fun faceDetection(d: Detection, b: FloatArray) =
        Detection(d.label, d.score, b[0], b[1], b[2], b[3], kpts = d.kpts, ellipse = true)
}
