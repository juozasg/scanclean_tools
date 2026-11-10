"""Automatic guides: curves in the photo that are straight horizontal / vertical lines on the
flat page (text lines, table rules, figure frames, text-block margins)."""
import warnings

import cv2
import numpy as np

from .paper import char_height, find_paper_mask, paper_level
from .util import odd, to_gray

warnings.simplefilter("ignore", getattr(getattr(np, "exceptions", np), "RankWarning", Warning))


class Guide:
    """A curve in the photo (full-res pixel coords) that is straight on the flat page."""
    __slots__ = ("kind", "pts", "src", "w")

    def __init__(self, kind, pts, src, w=1.0):
        self.kind = kind                       # 'h' or 'v'
        self.pts = np.asarray(pts, np.float64).reshape(-1, 2)
        self.src = src                         # 'text' | 'rule' | 'margin' | 'hint'
        self.w = w


def text_is_rotated(ink, h):
    """True if text runs vertically in the photo (landscape table printed sideways).

    Characters are closer to their neighbours within a line than to the lines above / below,
    so the direction to the nearest neighbouring blob tells the reading direction.
    """
    from scipy.spatial import cKDTree
    n, _, stats, cen = cv2.connectedComponentsWithStats(ink, 8)
    hh, ww = stats[1:, cv2.CC_STAT_HEIGHT], stats[1:, cv2.CC_STAT_WIDTH]
    ok = (np.maximum(hh, ww) > 0.5 * h) & (np.maximum(hh, ww) < 2.5 * h)
    c = cen[1:][ok]
    if len(c) < 40:
        return False
    d, j = cKDTree(c).query(c, k=2)
    v = c[j[:, 1]] - c
    near = d[:, 1] < 2.5 * h
    ang = np.abs(np.arctan2(v[near, 1], v[near, 0]))
    ang = np.minimum(ang, np.pi - ang)
    horiz, vert = (ang < np.pi / 6).sum(), (ang > np.pi / 3).sum()
    return vert > 1.5 * horiz


def trace_rules(n, valid, h, min_len):
    """Long thin near-horizontal dark lines (table rules, figure frames and grids).

    n: paper-normalised grey image (paper = 1), valid: mask of usable pixels.
    A rule pixel is darker than the pixels 3 px above and below it, and that stays true
    continuously along the line. Returns (list of Nx2 polylines, mask of rule pixels).
    """
    from scipy.ndimage import median_filter
    H, W = n.shape
    d = 3
    D = np.zeros_like(n, dtype=np.float32)
    D[d:-d] = np.minimum(n[:-2 * d], n[2 * d:]) - n[d:-d]
    D = np.clip(D, 0, 1) * valid
    L = odd(1.5 * h)
    E = cv2.erode(cv2.dilate(D, np.ones((5, 1), np.uint8)), np.ones((1, L), np.uint8))
    line = (E > 0.07).astype(np.uint8)
    line = cv2.dilate(line, np.ones((1, L), np.uint8))                      # undo the erosion
    line = cv2.morphologyEx(line, cv2.MORPH_CLOSE, np.ones((1, odd(1.2 * h)), np.uint8))
    cnt, lab, stats, _ = cv2.connectedComponentsWithStats(line, 8)
    out, rule_mask = [], np.zeros((H, W), np.uint8)
    for i in range(1, cnt):
        x, y, w, hh, area = stats[i]
        if w < min_len or hh > 0.25 * w + 8:
            continue
        sub = lab[y:y + hh, x:x + w] == i
        Ds = np.where(sub, D[y:y + hh, x:x + w], -1.0)
        yi = Ds.argmax(0)
        val = Ds.max(0)
        xs = np.arange(w)
        good = val > 0.04
        if good.sum() < 0.6 * w:
            continue
        xs, ys = xs[good] + x, (yi[good] + y).astype(np.float64)
        ysm = median_filter(ys, size=odd(2 * h), mode="nearest")
        keep = np.abs(ys - ysm) < 0.2 * h + 1
        if keep.sum() < 0.6 * w:
            continue
        xs, ys = xs[keep], ysm[keep]
        step = max(int(h), 4)
        nb = max(int((xs[-1] - xs[0]) / step), 1)
        bins = np.minimum(((xs - xs[0]) / step).astype(int), nb - 1)
        cntb = np.bincount(bins, minlength=nb)
        okb = cntb >= 0.5 * step
        if okb.sum() < 4:
            continue
        px = (np.bincount(bins, xs, minlength=nb) / np.maximum(cntb, 1))[okb]
        py = (np.bincount(bins, ys, minlength=nb) / np.maximum(cntb, 1))[okb]
        out.append(np.stack([px, py], 1))
        cv2.polylines(rule_mask, [np.stack([px, py], 1).round().astype(np.int32).reshape(-1, 1, 2)],
                      False, 1, max(int(0.35 * h), 3))
    return out, rule_mask


def trace_text_lines(ink, h, min_len_h=7.0):
    """Text lines as ridges of the horizontally smeared ink mask -> list of dicts."""
    H, W = ink.shape
    # drop blobs that are not characters (figure fills, thick frames)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    big = (stats[:, cv2.CC_STAT_HEIGHT] > 3.5 * h) | (stats[:, cv2.CC_STAT_WIDTH] > 8 * h)
    big[0] = False
    txt = ink.copy()
    txt[big[lab]] = 0
    B = cv2.GaussianBlur(txt.astype(np.float32), (0, 0), sigmaX=1.8 * h, sigmaY=0.33 * h)
    win = odd(1.5 * h)
    mx = cv2.dilate(B, np.ones((win, 1), np.uint8))
    cand = (B >= mx - 1e-7) & (B > 0.02)
    if cand.sum() < 20:
        return [], 0.0
    thr = 0.28 * np.percentile(B[cand], 90)
    ridge = (cand & (B > thr)).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(ridge, 8)
    lines = []
    for i in range(1, n):
        x, y, w, hh, area = stats[i]
        if w < min_len_h * h:
            continue
        sub = lab[y:y + hh, x:x + w] == i
        cols = sub.sum(0)
        good = cols > 0
        ys = (sub * np.arange(hh)[:, None]).sum(0) / np.maximum(cols, 1) + y
        xs = (np.arange(w) + x)[good]
        ys = ys[good]
        # sub-pixel refinement of the ridge position
        yi = np.clip(np.round(ys).astype(int), 1, H - 2)
        a, b, c = B[yi - 1, xs], B[yi, xs], B[yi + 1, xs]
        den = a - 2 * b + c
        ys = yi + np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / np.where(den == 0, 1, den), 0).clip(-1, 1)
        deg = 3 if w > 20 * h else 2
        cf = np.polyfit(xs, ys, deg)
        res = np.abs(np.polyval(cf, xs) - ys)
        if np.percentile(res, 90) > 0.5 * h:
            continue
        keep = res < 0.35 * h
        xs, ys = xs[keep], ys[keep]
        step = max(int(h), 4)
        nb = max(int((xs[-1] - xs[0]) / step), 1)
        bins = np.minimum(((xs - xs[0]) / step).astype(int), nb - 1)
        px = np.array([xs[bins == j].mean() for j in range(nb) if (bins == j).any()])
        py = np.array([ys[bins == j].mean() for j in range(nb) if (bins == j).any()])
        if len(px) < 5:
            continue
        lines.append({"pts": np.stack([px, py], 1), "len": float(xs[-1] - xs[0]), "poly": cf,
                      "x0": float(xs[0]), "x1": float(xs[-1])})
    score = sum(l["len"] for l in lines)
    return lines, score


def line_ends(lines, ink, h):
    """Refine left / right ends of text lines using the ink mask itself."""
    H, W = ink.shape
    half = max(int(0.8 * h), 2)
    L, R = [], []
    for ln in lines:
        cf = ln["poly"]
        for side in (0, 1):
            x_end = ln["x0"] if side == 0 else ln["x1"]
            rng = np.arange(int(x_end - 3 * h), int(x_end + 3 * h) + 1)
            rng = rng[(rng >= 0) & (rng < W)]
            if len(rng) == 0:
                continue
            yc = np.polyval(cf, np.clip(rng, ln["x0"], ln["x1"]))
            occ = np.zeros(len(rng), bool)
            for j, (xx, yy) in enumerate(zip(rng, yc)):
                y0, y1 = max(int(yy) - half, 0), min(int(yy) + half + 1, H)
                occ[j] = ink[y0:y1, xx].any()
            if not occ.any():
                continue
            # close gaps narrower than a word space, then take the block that touches x_end
            gap = max(int(1.6 * h), 3)
            occ_c = cv2.morphologyEx(occ.astype(np.uint8)[None, :], cv2.MORPH_CLOSE,
                                     np.ones((1, gap), np.uint8))[0].astype(bool)
            idx = np.where(occ_c)[0]
            j = idx[0] if side == 0 else idx[-1]
            (L if side == 0 else R).append((float(rng[j]), float(yc[j])))
    return np.array(L).reshape(-1, 2), np.array(R).reshape(-1, 2)


def margin_curve(P, h, side, min_n=6):
    """RANSAC a quadratic x = q(y) through line ends; returns inlier points or None."""
    if len(P) < min_n:
        return None
    rng = np.random.default_rng(0)
    y, x = P[:, 1], P[:, 0]
    ys = (y - y.mean()) / (np.ptp(y) + 1e-9)
    best = None
    tol = 0.45 * h
    for _ in range(300):
        i = rng.choice(len(P), 3, replace=False)
        if np.ptp(ys[i]) < 0.3:
            continue
        cf = np.polyfit(ys[i], x[i], 2)
        if abs(cf[0]) > 0.15 * np.ptp(y):          # absurd curvature
            continue
        r = np.polyval(cf, ys) - x
        # text never sticks out beyond its margin: penalise points on the outer side
        outer = (r > tol) if side == 0 else (r < -tol)
        inl = np.abs(r) < tol
        sc = inl.sum() - 2.0 * outer.sum()
        if best is None or sc > best[0]:
            best = (sc, inl)
    if best is None or best[1].sum() < max(min_n, 0.35 * len(P)):
        return None
    inl = best[1]
    if np.ptp(y[inl]) < 12 * h:
        return None
    return P[inl][np.argsort(y[inl])]


def find_guides(bgr, work_long=2000, verbose=False):
    """Detect all automatic guides. Returns (guides, info dict)."""
    H0, W0 = bgr.shape[:2]
    sw = min(1.0, work_long / max(H0, W0))
    bw = cv2.resize(bgr, None, fx=sw, fy=sw, interpolation=cv2.INTER_AREA) if sw < 1 else bgr
    g = to_gray(bw)
    norm = (g / paper_level(g, tile=max(g.shape) // 40)).astype(np.float32)
    ink0 = (norm < 0.80).astype(np.uint8)
    page = find_paper_mask(bw)
    h0 = char_height(ink0 & page)
    inner = cv2.erode(page, np.ones((odd(1.5 * h0), odd(1.5 * h0)), np.uint8))
    ink = ink0 & inner
    h = char_height(ink)
    Hw, Ww = ink.shape
    guides = []

    def add(kind, pts_w, src, w=1.0, transposed=False):
        p = np.asarray(pts_w, np.float64)
        if transposed:
            p = p[:, ::-1]
        guides.append(Guide(kind, (p + 0.5) / sw - 0.5, src, w))

    # ruled lines in both directions
    min_len = max(10 * h, 0.10 * min(Hw, Ww))
    rh, mh = trace_rules(norm, inner.astype(np.float32), h, min_len)
    rv, mv = trace_rules(np.ascontiguousarray(norm.T), np.ascontiguousarray(inner.T).astype(np.float32), h, min_len)
    rules_m = (mh | np.ascontiguousarray(mv.T)).astype(bool)
    # a lone line close to the border of the page is a page edge, not a printed rule
    dist = cv2.distanceTransform(cv2.copyMakeBorder(inner, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0),
                                 cv2.DIST_L2, 3)[1:-1, 1:-1]
    other = cv2.dilate(ink, np.ones((3, 3), np.uint8))

    def lone_edge(p, vertical):
        xi = np.clip(p[:, 1 if vertical else 0].astype(int), 0, Ww - 1)
        yi = np.clip(p[:, 0 if vertical else 1].astype(int), 0, Hw - 1)
        if np.median(dist[yi, xi]) > 5 * h:
            return False
        hits = {-1: 0, 1: 0}
        for x_, y_ in zip(xi, yi):
            for sgn in (-1, 1):
                a, b = sorted((int(sgn * 0.6 * h), int(sgn * 2.5 * h)))
                if vertical:
                    seg = other[y_, max(x_ + a, 0):max(x_ + b, 0)]
                else:
                    seg = other[max(y_ + a, 0):max(y_ + b, 0), x_]
                hits[sgn] += bool(seg.any())
        return min(hits.values()) < 0.15 * len(xi)      # nothing printed on one of its sides

    rh = [p for p in rh if not lone_edge(p, False)]
    rv = [p for p in rv if not lone_edge(p, True)]
    for p in rh:
        add("h", p, "rule", 1.5)
    for p in rv:
        add("v", p, "rule", 1.5, transposed=True)
    txt = ink & ~rules_m

    # text lines: try both reading directions, keep the dominant one
    rotated = text_is_rotated(txt, h)
    tmask = np.ascontiguousarray(txt.T) if rotated else txt
    lines, _ = trace_text_lines(tmask, h)
    for ln in lines:
        add("v" if rotated else "h", ln["pts"], "text", 1.0, transposed=rotated)

    # text-block margins (line starts / ends)
    if len(lines) >= 8:
        Lp, Rp = line_ends(lines, tmask, h)
        for side, P in ((0, Lp), (1, Rp)):
            m = margin_curve(P, h, side)
            if m is not None:
                add("h" if rotated else "v", m, "margin", 0.6, transposed=rotated)

    # a vertical line left / right of everything else on the page is a page edge or the gutter
    rest = [g.pts[:, 0] for g in guides if not (g.kind == "v" and g.src == "rule")]
    if rest:
        rest = np.concatenate(rest)
        lo_x, hi_x = rest.min() - h / sw, rest.max() + h / sw
        guides = [g for g in guides if not (g.kind == "v" and g.src == "rule"
                                            and not lo_x < np.median(g.pts[:, 0]) < hi_x)]

    info = {"scale": sw, "h": h / sw, "rotated": bool(rotated), "page_w": page, "ink_w": ink,
            "n_text": len(lines), "n_rule": sum(g.src == "rule" for g in guides)}
    return guides, info

