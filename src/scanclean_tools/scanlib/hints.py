"""Manual hints (<photo>.hints.json, written by dewarp_tool.py)."""
import json

import numpy as np

from .guides import Guide


def load_hints(img_path):
    for cand in (img_path.with_suffix(".hints.json"), img_path.parent / "hints" / (img_path.stem + ".json")):
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8"))
    return {}


def apply_hints(guides, hints, w_hint=6.0):
    """Drop automatic guides inside 'ignore' boxes, add manual lines / rectangles."""
    if not hints:
        return guides, None
    out = []
    boxes = [np.array(b, float).reshape(2, 2) for b in hints.get("ignore", [])]
    use_auto = hints.get("auto", True)
    for g in guides if use_auto else []:
        keep = np.ones(len(g.pts), bool)
        for b in boxes:
            lo, hi = b.min(0), b.max(0)
            keep &= ~np.all((g.pts >= lo) & (g.pts <= hi), axis=1)
        if keep.sum() >= 3:
            g.pts = g.pts[keep]
            out.append(g)
    for ln in hints.get("lines", []):
        p = np.array(ln["pts"], float).reshape(-1, 2)
        if len(p) >= 2 and ln.get("type") in ("h", "v"):
            out.append(Guide(ln["type"], p, "hint", w_hint))
    for r in hints.get("rects", []):
        tl, tr, br, bl = order_quad(r)
        out += [Guide("h", [tl, tr], "hint", w_hint), Guide("h", [bl, br], "hint", w_hint),
                Guide("v", [tl, bl], "hint", w_hint), Guide("v", [tr, br], "hint", w_hint)]
    return out, crop_points(hints)


def order_quad(pts):
    """Four points in any order -> top-left, top-right, bottom-right, bottom-left."""
    p = np.asarray(pts, np.float64).reshape(4, 2)
    c = p.mean(0)
    return p[np.argsort(np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0]))]     # image y points down


def crop_points(hints):
    """The crop quad of a hints dict as 4 photo points (TL, TR, BR, BL), or None.
    Older files hold two opposite corners of a box; they become the four corners of that box."""
    c = hints.get("crop") if hints else None
    if not c:
        return None
    p = np.array(c, float).reshape(-1, 2)
    if len(p) == 2:
        (xa, ya), (xb, yb) = p
        p = np.array([[xa, ya], [xb, ya], [xb, yb], [xa, yb]])
    return order_quad(p) if len(p) == 4 else None

