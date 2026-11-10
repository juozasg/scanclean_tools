"""Page layout in the flattened image: where the page ends, what to crop, edge clean-up."""
import cv2
import numpy as np

from .util import odd


def page_span(warped, pm, core, h_px, bands=8):
    """Left / right limits of the page in the flattened image, as two lines x = a + b*y.

    Walking outwards from the area covered by guides, the page ends where the paper brightness
    (a) becomes dark, (b) drops abruptly (page edge) or (c) passes a valley (gutter crease).
    """
    H, W = warped.shape
    x0, y0, x1, y1 = [int(v) for v in core]
    x0, x1 = int(np.clip(x0, 0, W - 2)), int(np.clip(x1, 1, W - 1))
    y0, y1 = int(np.clip(y0, 0, H - 2)), int(np.clip(y1, 1, H - 1))
    hh = max(int(h_px), 4)
    k = odd(0.35 * hh)
    edges = np.linspace(y0, y1, bands + 1).astype(int)
    res = {-1: [], 1: []}
    for b in range(bands):
        ya, yb = edges[b], max(edges[b + 1], edges[b] + 4)
        blk = warped[ya:yb:2].copy()
        blk[pm[ya:yb:2] < 128] = 0
        prof = np.percentile(blk, 80, axis=0).astype(np.float32)
        prof = cv2.dilate(prof[None, :], np.ones((1, k), np.uint8))       # ignore thin rules
        prof = cv2.blur(prof, (k, 1))[0]
        ref = float(np.median(prof[x0:x1 + 1]))
        for d in (-1, 1):
            x = x0 if d < 0 else x1
            run_min, arg_min, lim = prof[x], x, None
            while 0 < x < W - 1:
                x += d
                p = prof[x]
                back = prof[int(np.clip(x - d * hh, 0, W - 1))]
                if p < 0.47 * ref:                                   # dark: crease / book edge
                    lim = x - d * (hh // 3)
                    break
                if p < 0.80 * back:                                  # abrupt drop: page edge
                    lim = x - d * hh
                    break
                if p < run_min:
                    run_min, arg_min = p, x
                elif run_min < 0.85 * ref and p > 1.12 * run_min:    # valley passed: gutter
                    lim = arg_min
                    break
            if lim is None:
                lim = 0 if d < 0 else W - 1
            res[d].append((0.5 * (ya + yb), lim))
    lines = {}
    for d in (-1, 1):
        y, x = np.array(res[d]).T
        cf = np.array([0.0, np.median(x)])
        for _ in range(3):                                           # robust straight line
            r = np.abs(np.polyval(cf, y) - x)
            ok = r <= max(1.5 * hh, 1.5 * np.median(r))
            if ok.sum() >= 3:
                cf = np.polyfit(y[ok], x[ok], 1)
        if abs(cf[0]) > 0.15:
            cf = np.array([0.0, np.median(x)])
        lines[d] = cf
    return lines[-1], lines[1]


def remove_edge_dirt(out, outside, h_px):
    """Erase dark slivers that hang on the page boundary (rest of the gutter shadow, page
    edge). Only blobs that stay within a few text heights of the boundary are removed, so a
    table frame that merely touches the boundary survives."""
    k = odd(0.8 * h_px)
    near = cv2.dilate(outside.astype(np.uint8), np.ones((k, k), np.uint8))
    reach = cv2.dilate(outside.astype(np.uint8), np.ones((odd(7 * h_px), odd(7 * h_px)), np.uint8))
    ink = (out < 160).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    touch = np.zeros(n, bool)
    touch[np.unique(lab[(near > 0) & (ink > 0)])] = True
    far = np.zeros(n, bool)
    far[np.unique(lab[(reach == 0) & (ink > 0)])] = True
    kill = touch & ~far
    kill[0] = False
    out[kill[lab]] = 255


def content_box(out, core, h_px, margin):
    """Crop box = bounding box of everything printed on the page (the caller has already
    whitened whatever lies beyond the gutter / page edges), padded by `margin` px.
    Isolated specks are ignored; `core` (area covered by guides) is always included."""
    H, W = out.shape
    ink = (out < 200).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    sc = min(1.0, 12.0 / max(h_px, 1))                 # work on a small copy
    small = cv2.resize(ink, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
    hs = h_px * sc
    dens = cv2.blur(small.astype(np.float32), (odd(2 * hs), odd(2 * hs)))
    solid = ((small > 0) & (dens > 0.04)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(solid, np.ones((odd(hs), odd(2 * hs)), np.uint8)), 8)
    keep = np.zeros(n, bool)
    keep[1:] = (stats[1:, cv2.CC_STAT_WIDTH] > 1.2 * hs) & (stats[1:, cv2.CC_STAT_HEIGHT] > 0.8 * hs)
    ys, xs = np.where(keep[lab] & (solid > 0))
    x0, y0, x1, y1 = core
    if len(xs):
        x0, y0 = min(x0, xs.min() / sc), min(y0, ys.min() / sc)
        x1, y1 = max(x1, (xs.max() + 1) / sc), max(y1, (ys.max() + 1) / sc)
    return (max(int(x0 - margin), 0), max(int(y0 - margin), 0),
            min(int(x1 + margin), W), min(int(y1 + margin), H))

