"""Find picture-like areas on screen, so small images can be checked at full size.

When a large area changes (opening an email, scrolling a page), it has to
be shrunk to fit the model's input, and a small image in it (an 80 px
avatar) shrinks below what the model can recognise. Checking the whole area
at full size would take many detector runs. Instead, a cheap pass marks
blocks that look like photos (many distinct colours, unlike text and flat
interface). Each connected group of such blocks is a candidate picture, and
small candidates are packed side by side into one model-sized mosaic at full
size: a page with a few small pictures costs one or two extra detector runs.
"""

from dataclasses import dataclass

import numpy as np

STEP = 4            # sample every 4th pixel
BLOCK = 8           # blocks of 8x8 samples, i.e. 32x32 screen pixels
BLOCK_PX = STEP * BLOCK
MIN_COLOURS = 12    # distinct (4-bit quantised) colours that make a block "photo-like"
PAD_VALUE = 114


def picture_regions(frame, crop, min_size):
    """Rectangles (x0, y0, x1, y1) inside crop that look like pictures."""
    x0, y0, x1, y1 = crop
    rows = np.frombuffer(frame.view, np.uint8, count=y1 * frame.stride).reshape(y1, frame.stride)
    s = rows[y0:y1:STEP, x0 * 4:x1 * 4].reshape(-1, x1 - x0, 4)[:, ::STEP, :3]
    ph, pw = -s.shape[0] % BLOCK, -s.shape[1] % BLOCK
    s = np.pad(s, ((0, ph), (0, pw), (0, 0)), mode="edge").astype(np.int32)

    colour = (s[..., 0] >> 4) << 8 | (s[..., 1] >> 4) << 4 | (s[..., 2] >> 4)
    nby, nbx = colour.shape[0] // BLOCK, colour.shape[1] // BLOCK
    blocks = colour.reshape(nby, BLOCK, nbx, BLOCK).transpose(0, 2, 1, 3).reshape(nby, nbx, -1)
    distinct = 1 + (np.diff(np.sort(blocks, axis=-1), axis=-1) != 0).sum(-1)
    photo = distinct >= MIN_COLOURS

    # Join neighbouring blocks, so flat patches inside a picture (sky, a
    # cartoon's fill colours) don't split it up.
    grown = photo.copy()
    grown[1:] |= photo[:-1]
    grown[:-1] |= photo[1:]
    grown[:, 1:] |= photo[:, :-1]
    grown[:, :-1] |= photo[:, 1:]

    regions = []
    for by0, bx0, by1, bx1 in _components(grown):
        r = (x0 + bx0 * BLOCK_PX, y0 + by0 * BLOCK_PX,
             min(x1, x0 + bx1 * BLOCK_PX), min(y1, y0 + by1 * BLOCK_PX))
        if max(r[2] - r[0], r[3] - r[1]) >= min_size:
            regions.append(r)
    return regions


def _components(mask):
    """Bounding boxes (y0, x0, y1, x1), in blocks, of 4-connected groups."""
    seen = np.zeros_like(mask)
    boxes = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        seen[y, x] = True
        stack = [(y, x)]
        y0, x0, y1, x1 = y, x, y + 1, x + 1
        while stack:
            cy, cx = stack.pop()
            y0, x0, y1, x1 = min(y0, cy), min(x0, cx), max(y1, cy + 1), max(x1, cx + 1)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        boxes.append((int(y0), int(x0), int(y1), int(x1)))
    return boxes


@dataclass
class Placement:
    region: tuple  # (x0, y0, x1, y1) on screen
    x: int         # position in the mosaic
    y: int


def pack(regions, size):
    """Shelf-pack regions that fit into size x size mosaics.

    Returns (mosaics, leftovers): mosaics is a list of placement lists;
    leftovers are regions too big for a mosaic, to be checked on their own.
    """
    fits = [r for r in regions if r[2] - r[0] <= size and r[3] - r[1] <= size]
    leftovers = [r for r in regions if r not in fits]
    fits.sort(key=lambda r: r[3] - r[1], reverse=True)

    mosaics, current = [], []
    x = y = shelf_h = 0
    for r in fits:
        w, h = r[2] - r[0], r[3] - r[1]
        if x + w > size:
            x, y, shelf_h = 0, y + shelf_h, 0
        if y + h > size:
            mosaics.append(current)
            current, x, y, shelf_h = [], 0, 0, 0
        current.append(Placement(r, x, y))
        x += w
        shelf_h = max(shelf_h, h)
    if current:
        mosaics.append(current)
    return mosaics, leftovers


def tiles(region, size, scale, overlap=0.15):
    """Split a large region into overlapping tiles that are checked at
    `scale` (each tile is size/scale screen pixels across)."""
    x0, y0, x1, y1 = region
    span = int(size / scale)
    if max(x1 - x0, y1 - y0) <= span:
        return [region]
    step = int(span * (1 - overlap))

    def starts(a, b):
        if b - a <= span:
            return [a]
        out = list(range(a, b - span, step))
        return out + [b - span]

    return [(tx, ty, min(x1, tx + span), min(y1, ty + span))
            for ty in starts(y0, y1) for tx in starts(x0, x1)]


def render(frame, placements, size):
    """The mosaic image (BGRx) for a list of placements."""
    canvas = np.full((size, size, 4), PAD_VALUE, np.uint8)
    rows = np.frombuffer(frame.view, np.uint8, count=frame.height * frame.stride)
    rows = rows.reshape(frame.height, frame.stride)
    for p in placements:
        x0, y0, x1, y1 = p.region
        canvas[p.y:p.y + y1 - y0, p.x:p.x + x1 - x0] = \
            rows[y0:y1, x0 * 4:x1 * 4].reshape(y1 - y0, x1 - x0, 4)
    return canvas


def to_screen(det, placements):
    """Map a detection found in a mosaic back to the screen (clipped to the
    picture it was found in), or None if it isn't inside any placed picture."""
    cx, cy = (det.x0 + det.x1) / 2, (det.y0 + det.y1) / 2
    for p in placements:
        x0, y0, x1, y1 = p.region
        if p.x <= cx < p.x + x1 - x0 and p.y <= cy < p.y + y1 - y0:
            d = det.moved(x0 - p.x, y0 - p.y)
            d.x0, d.y0, d.x1, d.y1 = max(x0, d.x0), max(y0, d.y0), min(x1, d.x1), min(y1, d.y1)
            return d
    return None
