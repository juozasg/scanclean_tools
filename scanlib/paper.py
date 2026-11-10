"""Photometric analysis of a page photo: where is paper, how bright is it, how dark is the ink."""
import math

import cv2
import numpy as np

from .util import odd


def _tile_percentile(gray, tile, q):
    H, W = gray.shape
    gh, gw = math.ceil(H / tile), math.ceil(W / tile)
    pad = cv2.copyMakeBorder(gray, 0, gh * tile - H, 0, gw * tile - W, cv2.BORDER_REFLECT)
    blocks = pad.reshape(gh, tile, gw, tile).transpose(0, 2, 1, 3).reshape(gh, gw, -1)
    step = max(blocks.shape[2] // 400, 1)
    return np.percentile(blocks[:, :, ::step], q, axis=2).astype(np.float32)


def paper_level(gray, tile, fine=0):
    """Smooth map of local paper brightness (illumination x paper tint).

    Coarse pass: tile-wise high percentile; tiles that are mostly ink (solid figure fills) are
    replaced by their neighbourhood. Optional fine pass (smaller tiles) follows steep shadows
    such as the one next to the gutter, wherever the fine tile still looks like paper.
    """
    H, W = gray.shape
    tile = max(int(tile), 8)
    p = _tile_percentile(gray, tile, 88)
    med = cv2.medianBlur(p, 5) if min(p.shape) >= 5 else p
    p = np.where(p < 0.9 * med, med, p)
    p = cv2.GaussianBlur(p, (0, 0), 0.8)
    coarse = cv2.resize(p, (p.shape[1] * tile, p.shape[0] * tile), interpolation=cv2.INTER_CUBIC)[:H, :W]
    if not fine:
        return np.maximum(coarse, 1.0)
    ft = max(int(fine), 6)
    pf = _tile_percentile(gray, ft, 85)
    cs = cv2.resize(coarse, (pf.shape[1], pf.shape[0]), interpolation=cv2.INTER_AREA)
    pf = np.where((pf > 0.45 * cs) & (pf < 1.3 * cs), pf, cs)
    pf = cv2.GaussianBlur(pf, (0, 0), 1.0)
    out = cv2.resize(pf, (pf.shape[1] * ft, pf.shape[0] * ft), interpolation=cv2.INTER_CUBIC)[:H, :W]
    return np.maximum(out, 1.0)


def ink_level(n, tile, lo, hi):
    """Smooth map of how dark the ink is locally (faint print near the gutter gets a lift)."""
    H, W = n.shape
    gh, gw = math.ceil(H / tile), math.ceil(W / tile)
    pad = cv2.copyMakeBorder(n, 0, gh * tile - H, 0, gw * tile - W, cv2.BORDER_REFLECT)
    blocks = pad.reshape(gh, tile, gw, tile).transpose(0, 2, 1, 3).reshape(gh, gw, -1)
    p = np.percentile(blocks[:, :, ::3], 3, axis=2).astype(np.float32)
    valid = (p < hi).astype(np.float32)
    p = np.clip(p, lo, hi)
    if valid.sum() < 4:
        return np.full(n.shape, lo, np.float32)
    num = cv2.GaussianBlur(p * valid, (0, 0), 1.5)
    den = cv2.GaussianBlur(valid, (0, 0), 1.5)
    fill = np.where(den > 0.02, num / np.maximum(den, 1e-6), float(np.median(p[valid > 0])))
    big = cv2.resize(fill.astype(np.float32), (gw * tile, gh * tile), interpolation=cv2.INTER_CUBIC)
    return big[:H, :W]


def find_paper_mask(bgr_w):
    """Lenient paper mask at work scale: 1 where the pixel may be paper, 0 where it is
    certainly not (dark book edge, gutter crease, coloured desk). Shadowed paper stays in."""
    hsv = cv2.cvtColor(cv2.GaussianBlur(bgr_w, (0, 0), 3), cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    k = odd(min(bgr_w.shape[:2]) * 0.02)
    v_cl = cv2.morphologyEx(v, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    t, _ = cv2.threshold(v_cl, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = v_cl > t
    ref = np.median(v_cl[bright])
    s_ref = np.median(s[bright])
    paper = ((v_cl > 0.42 * ref) & (s < s_ref + 60)).astype(np.uint8)
    paper = cv2.morphologyEx(paper, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(paper, 8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] > 0.02 * paper.size
    m = keep[lab].astype(np.uint8)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cnts, -1, 1, -1)
    return out if out.mean() > 0.15 else np.ones(paper.shape, np.uint8)


def char_height(ink):
    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    hh = stats[1:, cv2.CC_STAT_HEIGHT]
    ww = stats[1:, cv2.CC_STAT_WIDTH]
    ok = (hh >= 4) & (hh <= 80) & (ww >= 2) & (ww <= 80) & (stats[1:, cv2.CC_STAT_AREA] >= 8)
    if ok.sum() < 30:
        return 12.0
    return float(np.median(hh[ok]))

