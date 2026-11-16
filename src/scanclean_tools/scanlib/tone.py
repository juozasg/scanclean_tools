"""Step 2 logic: colour page -> greyscale scan (flat-field, levels, trim).
The page image is assumed to show only paper (cropped by step 1 or by hand)."""
import cv2
import numpy as np

from .deblur import deblur as deblur_gray
from .layout import content_box
from .paper import char_height, ink_level, paper_level
from .util import to_gray


def print_profile(n, h_px):
    """Darkest print per vertical band across the page: x centres, ink level (low = dark), has-print."""
    H, W = n.shape
    bw = max(int(3 * h_px), 16)
    y0, y1 = int(0.03 * H), int(0.97 * H)
    xs, ink = [], []
    for x0 in range(0, W, bw // 2):
        band = n[y0:y1, x0:x0 + bw]
        if band.shape[1] < bw // 2:
            break
        xs.append(x0 + band.shape[1] / 2)
        ink.append(float(np.percentile(band[::2, ::2], 1.5)))
    xs, ink = np.array(xs), np.array(ink)
    return xs, ink, ink < 0.88


def fade_zones(n, h_px, faded=0.8, max_gutter=0.35):
    """Find faded print near the left and right gutters.

    Returns (r, zones): r[x] is the print depth relative to the page centre (1 = as dark as the
    centre, smaller = faded), and zones = (left_end, right_start) in px - the gutter zones are
    the runs of faded bands touching each side edge (at most `max_gutter` of the width); the
    centre between them keeps r = 1.
    """
    H, W = n.shape
    xs, ink, valid = print_profile(n, h_px)
    if valid.sum() < 3:
        return np.ones(W, np.float32), (0, W)
    depth = 1 - ink
    mid = valid & (xs > 0.3 * W) & (xs < 0.7 * W)
    c0 = np.median(depth[mid] if mid.any() else depth[valid])
    idx = np.flatnonzero(valid)
    d = np.interp(np.arange(len(xs)), idx, depth[idx])          # blank bands: nearest print
    d = np.convolve(np.pad(d, 2, mode="edge"), np.ones(5) / 5, mode="valid")
    r = np.clip(d / c0, 0.15, 1.0)
    lim = max_gutter * W
    k = 0
    while k < len(r) and r[k] < faded and xs[k] < lim:
        k += 1
    m = len(r)
    while m > k and r[m - 1] < faded and xs[m - 1] > W - lim:
        m -= 1
    # gutter zones fade back to 1 over one band beyond the last faded band
    left = xs[k] if k < len(xs) else W
    right = xs[m - 1] if m > 0 else 0
    r[k:m] = 1.0
    if k == 0:
        left = 0
    if m == len(r):
        right = W
    return np.interp(np.arange(W), xs, r).astype(np.float32), (int(left), int(right))


def tone(gray, h_px, white=0.88, black=0.25, gamma=1.0, sharpen=0.5, adaptive=True, info=None):
    """Flat-field + local levels: paper -> white, ink -> black. Returns uint8.

    adaptive  in gutter zones where the print is fainter than in the centre, move the white
              point towards the paper and the black point towards the faint print, so faded
              letters keep their contrast (zones are written to `info["zones"]` if given)
    """
    bg = paper_level(gray, tile=max(int(3.0 * h_px), 24), fine=max(int(1.2 * h_px), 12))
    n = gray / bg
    if sharpen > 0:
        blur = cv2.GaussianBlur(n, (0, 0), max(h_px / 18, 0.8))
        n = n + sharpen * (n - blur)
    ink = ink_level(n, max(int(5.0 * h_px), 32), 0.12, 0.70)
    wl = white
    if adaptive:
        r, z = fade_zones(n, h_px)
        if info is not None:
            info["zones"] = z
        if r.min() < 1:
            R = r[None, :]
            wl = 1 - (1 - white) * R
            ink_faint = 1 - R * (1 - np.minimum(ink, 0.70))
            f = np.clip((1 - R) / 0.5, 0, 1)
            ink = (1 - f) * ink + f * np.maximum(ink_faint, ink)
    lo = ink + black * (wl - ink)
    n = np.clip((n - lo) / (wl - lo), 0, 1)
    if gamma != 1.0:
        n = n ** gamma
    return (n * 255 + 0.5).astype(np.uint8)


def estimate_text_height(gray, long_side=2000):
    """Typical character height in pixels of `gray` (used when step 1 left no metadata)."""
    s = min(1.0, long_side / max(gray.shape))
    g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    ink = (g / paper_level(g, tile=max(g.shape) // 40) < 0.80).astype(np.uint8)
    return char_height(ink) / s


def remove_show_through(out, strong=110, weak=235):
    """Remove print showing through from the back of the page.

    Show-through is faint and blurred: none of its pixels gets really dark. Every connected
    patch of non-white pixels (darker than `weak`) is kept only if it contains at least one
    pixel darker than `strong`; faint patches without such a core become white. Real print -
    including thin strokes and the grey edges of letters - is connected to a dark core and stays.
    """
    n, lab = cv2.connectedComponents((out < weak).astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[np.unique(lab[out < strong])] = True
    keep[0] = False
    res = out.copy()
    res[~keep[lab]] = 255
    return res


def remove_edge_shadow(out, h_px, weak=200, near=1.0, min_len=6.0, max_thick=4.0, band=0.15):
    """Remove dark streaks along the image edges left by gutter / page-edge shadows.

    Dark pixels (< `weak`) are merged with a small closing; a merged blob within `near` text
    heights of an edge is removed when it runs along that edge for more than `min_len` text
    heights while being thinner than `max_thick` on average, or when it fills a corner (touches
    two edges, small). Specks smaller than a text height next to a removed streak go too.
    Text cut by the image edge is left alone: its blobs are short along the edge.
    Only the border strip `band` (fraction of width / height) along each edge is looked at
    and changed; the inside of the page is never touched.
    """
    H, W = out.shape
    bx, by = int(band * W), int(band * H)
    inside = np.zeros((H, W), bool)
    inside[by:H - by, bx:W - bx] = True
    dark = ((out < weak) & ~inside).astype(np.uint8)
    k = max(int(h_px / 2) | 1, 3)
    m = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    b = int(near * h_px)
    kill = np.zeros(int(n), dtype=bool)
    for i in range(1, n):
        x, y, w, hh, a = (int(v) for v in st[i])
        runs = [hh] * ((x <= b) + (x + w >= W - b)) + [w] * ((y <= b) + (y + hh >= H - b))
        if not runs:
            continue
        L = max(runs)
        streak = L > min_len * h_px and a / L < max_thick * h_px
        corner = len(runs) >= 2 and a < (8 * h_px) ** 2 and max(w, hh) < 0.3 * max(H, W)
        kill[i] = streak or corner
    if not kill.any():
        return out
    gone = np.take(kill, np.asarray(lab, np.int32))
    near_gone = cv2.dilate(gone.astype(np.uint8), np.ones((int(2 * h_px) | 1,) * 2, np.uint8)) > 0
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    speck = (np.maximum(st2[:, 2], st2[:, 3]) < h_px)
    speck[0] = False
    res = out.copy()
    res[(gone | (np.take(speck, lab2) & near_gone)) & ~inside] = 255
    return res


def tone_page(bgr, text_height=None, core=None, trim=True, margin=4.0,
              white=0.88, black=0.25, gamma=1.0, sharpen=0.5, show_through=110, deblur=2.5,
              adaptive=True, edge_shadow=True, edge_band=0.15, info=None):
    """Colour (or grey) page image, showing only paper -> uint8 greyscale scan.

    text_height  character height in px (estimated when None)
    core         x0, y0, x1, y1 that must stay inside the trimmed result (optional)
    trim         crop to the printed area plus `margin` text heights
    deblur       strength of the out-of-focus correction of blurry regions (0 = off)
    show_through grey level (0-255) a patch must reach somewhere to count as print; fainter
                 patches - show-through from the back of the page - are removed (0 = keep all)
    adaptive     lift faded print in the gutter zones (see tone)
    edge_shadow  remove dark shadow streaks along the image edges
    edge_band    width of the border strip checked for edge shadow (fraction of the page)
    info         dict that receives the detected gutter zones
    """
    gray = to_gray(bgr) if bgr.ndim == 3 else bgr.astype(np.float32)
    h = float(text_height) if text_height else estimate_text_height(gray)
    if deblur:
        gray = deblur_gray(gray, h, gain=deblur)
    out = tone(gray, h, white, black, gamma, sharpen, adaptive, info)
    if show_through:
        out = remove_show_through(out, strong=show_through)
    if edge_shadow:
        out = remove_edge_shadow(out, h, band=edge_band)
    if trim:
        H, W = out.shape
        x0, y0, x1, y1 = content_box(out, core if core is not None else (W, H, 0, 0), h, margin * h)
        if x1 > x0 and y1 > y0:
            out = out[y0:y1, x0:x1]
    return out
