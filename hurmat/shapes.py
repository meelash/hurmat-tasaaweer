"""Blur shapes: a figure's outline as a stack of horizontal bands.

Blurring only the outline (rather than the whole bounding box) leaves
nearby buttons, text and other content sharp. A shape is a list of
non-overlapping bands (y0, y1, x0, x1), top to bottom; the extension blurs
their bounding box and lays sharp copies of the window over the rest.

Bands are deliberately generous: each takes the width of its neighbours
too, and everything is padded, so a figure that moves a little between
checks stays covered.
"""

import numpy as np

BAND_PX = 24        # target band height on screen
MIN_BANDS, MAX_BANDS = 3, 24


def figure_shape(det, margin, margin_px, bounds):
    """Padded bands covering a detection's outline (or its whole box if the
    model gives no outline), clipped to bounds (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = det.x0, det.y0, det.x1, det.y1
    pad_x = (x1 - x0) * margin + margin_px
    pad_y = (y1 - y0) * margin + margin_px
    if det.ellipse:
        return _clip(_oval(x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y), bounds)
    spans = _outline_spans(det)
    if spans is None:
        return _clip([(y0 - pad_y, y1 + pad_y, x0 - pad_x, x1 + pad_x)], bounds)

    bands = []
    for i, (by0, by1, _, _) in enumerate(spans):
        near = [s for s in spans[max(0, i - 1):i + 2] if s[2] is not None]
        if not near:
            continue
        bands.append([by0, by1, min(s[2] for s in near) - pad_x, max(s[3] for s in near) + pad_x])
    if not bands:
        return _clip([(y0 - pad_y, y1 + pad_y, x0 - pad_x, x1 + pad_x)], bounds)
    bands[0][0] -= pad_y
    bands[-1][1] += pad_y
    return _clip(_merge(bands), bounds)


def _outline_spans(det):
    """[(y0, y1, x_min, x_max or None)] per band over the detection box, from
    its mask; None if there is no usable mask."""
    if det.mask is None or not det.mask.any():
        return None
    mask = det.mask
    mx0, my0, mx1, my1 = det.mask_box
    mh, mw = mask.shape
    cw, ch = (mx1 - mx0) / mw, (my1 - my0) / mh
    # Only the part of the mask inside the detection box counts.
    c0, c1 = int(max(0, (det.x0 - mx0) // cw)), int(min(mw, -(-(det.x1 - mx0) // cw)))
    r0, r1 = int(max(0, (det.y0 - my0) // ch)), int(min(mh, -(-(det.y1 - my0) // ch)))
    if c1 <= c0 or r1 <= r0:
        return None
    mask = mask[r0:r1, c0:c1]

    n = int(np.clip(round((det.y1 - det.y0) / BAND_PX), MIN_BANDS, MAX_BANDS))
    edges = np.linspace(0, mask.shape[0], n + 1).round().astype(int)
    spans = []
    for i in range(n):
        rows = mask[edges[i]:max(edges[i + 1], edges[i] + 1)]
        cols = np.flatnonzero(rows.any(axis=0))
        y_a, y_b = my0 + (r0 + edges[i]) * ch, my0 + (r0 + max(edges[i + 1], edges[i] + 1)) * ch
        if len(cols):
            spans.append((y_a, y_b, mx0 + (c0 + cols[0]) * cw, mx0 + (c0 + cols[-1] + 1) * cw))
        else:
            spans.append((y_a, y_b, None, None))
    return spans


def _oval(x0, y0, x1, y1):
    """An oval filling the box, as bands. Each band is as wide as the oval gets within it."""
    n = int(np.clip(round((y1 - y0) / BAND_PX), 6, MAX_BANDS))
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    bands = []
    for i in range(n):
        ya, yb = y0 + (y1 - y0) * i / n, y0 + (y1 - y0) * (i + 1) / n
        # Distance from the centre line to the band's nearest edge (0 if it spans it).
        d = 0.0 if ya <= cy <= yb else min(abs(ya - cy), abs(yb - cy))
        half = rx * np.sqrt(max(0.0, 1 - (d / ry) ** 2))
        bands.append((ya, yb, cx - half, cx + half))
    return bands


def union(a, b):
    """Union of two shapes, as a shape (each band covers both)."""
    if not a:
        return b
    if not b:
        return a
    ys = sorted({y for band in a + b for y in band[:2]})
    out = []
    for y0, y1 in zip(ys, ys[1:]):
        covering = [band for band in a + b if band[0] <= y0 and band[1] >= y1]
        if covering:
            out.append([y0, y1, min(c[2] for c in covering), max(c[3] for c in covering)])
    return _merge(out)


def bounding_box(shape):
    return (min(b[2] for b in shape), min(b[0] for b in shape),
            max(b[3] for b in shape), max(b[1] for b in shape))


def _merge(bands):
    """Join vertically touching bands with the same horizontal extent."""
    out = []
    for b in bands:
        if out and abs(out[-1][1] - b[0]) < 1e-6 and out[-1][2:] == list(b[2:]):
            out[-1][1] = b[1]
        else:
            out.append(list(b))
    return [tuple(b) for b in out]


def _clip(bands, bounds):
    bx0, by0, bx1, by1 = bounds
    out = []
    for y0, y1, x0, x1 in bands:
        y0, y1, x0, x1 = max(by0, y0), min(by1, y1), max(bx0, x0), min(bx1, x1)
        if y1 > y0 and x1 > x0:
            out.append((y0, y1, x0, x1))
    return out
