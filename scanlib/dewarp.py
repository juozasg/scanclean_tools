"""Step 1 logic: photo -> geometrically flat colour page, page mask and metadata."""
import math

import cv2
import numpy as np

from .guides import find_guides
from .hints import apply_hints
from .layout import page_span
from .sheet import Polish, Sheet, build_maps, guide_rms, straightness
from .util import imwrite, odd, to_gray

COL = {"text": (0, 170, 0), "rule": (255, 120, 0), "margin": (200, 0, 200), "hint": (0, 0, 255)}


def photo_border(W0, H0, n=60):
    """Points along the four edges of the photo."""
    e = np.linspace(0, 1, n)
    return np.concatenate([np.stack([e * (W0 - 1), 0 * e], 1), np.stack([e * (W0 - 1), 0 * e + H0 - 1], 1),
                           np.stack([0 * e, e * (H0 - 1)], 1), np.stack([0 * e + W0 - 1, e * (H0 - 1)], 1)])


def fit_geometry(bgr, guides, text_height, focal35=26.0, use_polish=True):
    """Fit sheet model (+ polish) to the guides. Returns (sheet, polish, guides_used, report)."""
    H0, W0 = bgr.shape[:2]
    sheet = Sheet((W0, H0), focal35 / 43.27 * math.hypot(W0, H0))
    rep = {}
    n_con = sum(len(g.pts) - 1 for g in guides)          # number of straightness constraints
    fit = None
    if n_con >= 10:
        fit = sheet.fit(guides)
        # guides that the fitted sheet cannot straighten are mis-detections (figure hatching,
        # noise): drop them and fit once more
        tol = max(0.3 * text_height, 4.0)
        good = [g for g in guides if g.src == "hint" or guide_rms(g, sheet.to_flat(g.pts)) < tol]
        if len(good) < len(guides) and sum(len(g.pts) - 1 for g in good) >= 30:
            rep["dropped"] = len(guides) - len(good)
            guides = good
            fit = sheet.fit(guides)
    flat = [sheet.to_flat(g.pts) for g in guides]
    rep["raw"] = straightness(guides, [g.pts for g in guides])
    rep["sheet"] = straightness(guides, flat)
    polish = None
    if use_polish and fit is not None:
        bf = sheet.to_flat(photo_border(W0, H0))
        polish = Polish(bf.min(0), bf.max(0))
        polish.fit(list(zip(guides, flat)))
    rep["final"] = straightness(guides, [polish.forward(P) for P in flat] if polish else flat)
    rep["constraints"] = n_con
    rep["fitted"] = fit is not None
    return sheet, polish, guides, rep


def flat_frame(sheet, polish, size):
    """Rectangle (x0, y0, w, h) in flat coordinates that contains the whole photo."""
    W0, H0 = size
    bf = sheet.to_flat(photo_border(W0, H0))
    if polish is not None:
        bf = polish.forward(bf)
    ctr = sheet.to_flat(np.array([[sheet.cx, sheet.cy]]))[0]
    lim = 0.85 * np.array([W0, H0])
    lo, hi = np.maximum(bf.min(0), ctr - lim), np.minimum(bf.max(0), ctr + lim)
    x0, y0 = math.floor(lo[0]), math.floor(lo[1])
    return x0, y0, int(math.ceil(hi[0] - lo[0])), int(math.ceil(hi[1] - lo[1]))


def dewarp_page(bgr, hints=None, focal35=26.0, scale=1.0, work=2000, use_polish=True):
    """Photo -> dict with
         image   flat colour page (only resampled, colours untouched), cropped to the page
         mask    uint8, 255 where the pixel belongs to this page (not desk / gutter / facing page)
         meta    text height, core box, trim flag, fit quality  (JSON-serialisable)
         guides, sheet, polish, box   for debugging (box = crop in flat coordinates)
    """
    H0, W0 = bgr.shape[:2]
    guides, info = find_guides(bgr, work)
    guides, crop_hint = apply_hints(guides, hints or {})
    sheet, polish, guides, rep = fit_geometry(bgr, guides, info["h"], focal35, use_polish)

    def to_final(P):
        P = sheet.to_flat(P)
        return polish.forward(P) if polish is not None else P

    # one resampling of the colour photo
    x0, y0, w, h = flat_frame(sheet, polish, (W0, H0))
    mx, my = build_maps(sheet, polish, x0, y0, w, h)
    if scale != 1.0:
        mx = cv2.resize(mx, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        my = cv2.resize(my, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    warped = cv2.remap(bgr, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    sw = info["scale"]
    pm = cv2.remap(info["page_w"] * 255, (mx + 0.5) * sw - 0.5, (my + 0.5) * sw - 0.5, cv2.INTER_LINEAR,
                   borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    hs = info["h"] * scale
    k = odd(0.8 * hs)
    mask = (cv2.erode(pm, np.ones((k, k), np.uint8)) >= 128)       # not desk / book edge / outside

    def to_out(P):
        return (P - [x0, y0]) * scale

    Ho, Wo = mask.shape
    allp = to_out(np.concatenate([to_final(g.pts) for g in guides])) if guides else None
    if allp is not None:
        # robust horizontal extent: a few stray guides on the facing page must not count
        core = (np.percentile(allp[:, 0], 2), allp[:, 1].min(), np.percentile(allp[:, 0], 98), allp[:, 1].max())
    else:
        core = (0.25 * Wo, 0.25 * Ho, 0.75 * Wo, 0.75 * Ho)
    # nothing beyond the gutter / the page edges belongs to this page
    cl, cr = page_span(to_gray(warped), pm, core, hs)
    yy = np.arange(Ho, dtype=np.float32)[:, None]
    xx = np.arange(Wo, dtype=np.float32)[None, :]
    mask &= ~((xx < np.polyval(cl, yy) + 0.3 * hs) | (xx > np.polyval(cr, yy) - 0.3 * hs))
    if allp is not None:
        lx, rx = np.polyval(cl, allp[:, 1]), np.polyval(cr, allp[:, 1])
        inp = allp[(allp[:, 0] > lx) & (allp[:, 0] < rx)]
        if len(inp):
            core = (*inp.min(0), *inp.max(0))

    trim = True
    if crop_hint is not None and len(crop_hint) >= 2:              # the user decided the crop
        cf = to_out(to_final(crop_hint))
        bx = (max(int(cf[:, 0].min()), 0), max(int(cf[:, 1].min()), 0),
              min(int(cf[:, 0].max()), Wo), min(int(cf[:, 1].max()), Ho))
        trim = False
    else:                                                          # the page itself
        ys, xs = np.where(mask)
        bx = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if len(xs) else (0, 0, Wo, Ho)
    image = warped[bx[1]:bx[3], bx[0]:bx[2]]
    mask = (mask[bx[1]:bx[3], bx[0]:bx[2]] * 255).astype(np.uint8)

    n_hint = sum(g.src == "hint" for g in guides)
    meta = {"text_height": round(hs, 2), "trim": trim, "scale": scale,
            "core": [round(float(core[0] - bx[0]), 1), round(float(core[1] - bx[1]), 1),
                     round(float(core[2] - bx[0]), 1), round(float(core[3] - bx[1]), 1)],
            "text": info["n_text"], "rules": info["n_rule"], "hints": n_hint, "rotated": info["rotated"],
            "raw": rep["raw"], "sheet": rep["sheet"], "final": rep["final"],
            "dropped": rep.get("dropped", 0), "constraints": rep["constraints"], "fitted": rep["fitted"]}
    box = (bx[0] / scale + x0, bx[1] / scale + y0, bx[2] / scale + x0, bx[3] / scale + y0)
    return {"image": image, "mask": mask, "meta": meta, "guides": guides, "sheet": sheet,
            "polish": polish, "box": box}


def status(meta, warn=2.5):
    """'ok', 'CHECK' or 'NO GUIDES - add hints' for the table printed by step 1."""
    if not meta["fitted"]:
        return "NO GUIDES - add hints"
    worst = max(meta["final"].values(), default=99.0)
    return "CHECK" if (worst > warn or (meta["constraints"] < 150 and not meta["hints"])) else "ok"


def draw_debug(bgr, guides, sheet, polish, box, path, max_side=1600):
    s = max_side / max(bgr.shape[:2])
    vis = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    vis = (0.55 * vis + 0.45 * 255).astype(np.uint8)
    for g in guides:
        p = (g.pts * s).astype(np.int32)
        cv2.polylines(vis, [p.reshape(-1, 1, 2)], False, COL[g.src], 2 if g.src != "hint" else 3, cv2.LINE_AA)
        if g.src in ("hint", "margin"):
            for q in p:
                cv2.circle(vis, tuple(int(v) for v in q), 4, COL[g.src], -1, cv2.LINE_AA)
    if sheet is not None and box is not None:
        x0, y0, x1, y1 = box
        for t in np.linspace(0, 1, 9):                 # fitted page grid drawn back on the photo
            for a, b in (((x0 + t * (x1 - x0), y0), (x0 + t * (x1 - x0), y1)),
                         ((x0, y0 + t * (y1 - y0)), (x1, y0 + t * (y1 - y0)))):
                Q = np.linspace(a, b, 60)
                P = polish.inverse(Q) if polish is not None else Q
                ph = (sheet.to_photo(P) * s).astype(np.int32)
                cv2.polylines(vis, [ph.reshape(-1, 1, 2)], False, (0, 200, 255), 1, cv2.LINE_AA)
    imwrite(path, vis, [cv2.IMWRITE_JPEG_QUALITY, 82])
