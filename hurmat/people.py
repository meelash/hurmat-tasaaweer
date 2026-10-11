"""Which detected people to blur, and how. Port of android/.../People.kt.

Uses a pose model's 17 body keypoints (COCO order: nose, eyes, ears,
shoulders, elbows, wrists, hips, knees, ankles):

  figure mode: the whole outline, but only if a head or most of a torso is
               visible. A hand, fingers or legs on their own are left alone.
               Headless torsos stay blurred on purpose: revealing bodies
               matter for guarding the gaze even without a head.
  face mode:   an oval around the head only; people without a visible head
               are left alone.

Animals have no keypoints and are always blurred whole. A person only one
of the two models found is blurred (whole, or by the rules above) if that
model was confident; weak finds the other model doesn't back up are dropped.
"""

import numpy as np

from .detector import Detection

VISIBLE = 0.5
# A person only one of the two models saw must be a confident find: weak
# finds the other model doesn't back up are usually not people (a paper bag,
# a dark suit sleeve).
SOLO_SCORE = 0.5
HEAD = [0, 1, 2, 3, 4]
TORSO = [5, 6, 11, 12]  # shoulders, hips


def apply(mode, dets, poses):
    out = []
    used = set()
    for d in dets:
        if d.label != "person":
            out.append(d)
            continue
        cands = [(iou(d.box(), p.box()), i) for i, p in enumerate(poses) if i not in used]
        best = max(cands, default=(0, None))
        if best[0] < 0.4:
            if d.score >= SOLO_SCORE:
                out.append(d)
            continue
        used.add(best[1])
        out += _judge(mode, d, poses[best[1]].kpts)
    # People only the pose model found (no outline): same rules, if confident.
    for i, p in enumerate(poses):
        if i not in used and p.score >= SOLO_SCORE:
            out += _judge(mode, p, p.kpts)
    return out


def _judge(mode, d, k):
    if mode == "figure":
        return [d] if _has_head(k) or _has_torso(k) else []
    box = _head_box(k, d.box())
    if box is None:
        return []
    return [Detection(d.label, d.score, *box, kpts=d.kpts, ellipse=True)]


def _visible(k, j):
    return k[j, 2] > VISIBLE


def _has_head(k):
    return sum(_visible(k, j) for j in HEAD) >= 2


def _has_torso(k):
    return sum(_visible(k, j) for j in TORSO) >= 3


def _dist(k, a, b):
    if _visible(k, a) and _visible(k, b):
        return float(np.hypot(*(k[a, :2] - k[b, :2])))
    return 0.0


def _head_box(k, person_box):
    """Box around the head (with hair and chin), inside the person's box, or
    None if no head is visible."""
    if not _has_head(k):
        return None
    pts = [j for j in HEAD if _visible(k, j)]
    cx, cy = k[pts, 0].mean(), k[pts, 1].mean()
    # Head width from the most reliable pair visible: ears sit at the head's
    # sides; eyes are about 0.4 of its width apart. Otherwise (a profile) the
    # spread of what's visible. Shoulders give a lower bound.
    if _visible(k, 3) and _visible(k, 4):
        width = _dist(k, 3, 4) * 1.15
    elif _visible(k, 1) and _visible(k, 2):
        width = _dist(k, 1, 2) * 2.5
    else:
        width = max(_dist(k, a, b) for a in pts for b in pts) * 1.6
    width = max(width, _dist(k, 5, 6) * 0.45)
    # The keypoints sit around eye level: more head above them (hair) than below (chin).
    x0, y0, x1, y1 = cx - 0.6 * width, cy - 0.75 * width, cx + 0.6 * width, cy + 0.65 * width
    px0, py0, px1, py1 = person_box
    return (max(x0, px0), max(y0, py0), min(x1, px1), min(y1, py1))


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
