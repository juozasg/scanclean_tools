"""Manual hints (<photo>.hints.json, written by hint_tool.py)."""
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
        p = np.array(r, float).reshape(4, 2)
        c = p.mean(0)
        ang = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
        tl, tr, br, bl = p[np.argsort(ang)]          # image y is down: -pi.. => TL,TR,BR,BL
        out += [Guide("h", [tl, tr], "hint", w_hint), Guide("h", [bl, br], "hint", w_hint),
                Guide("v", [tl, bl], "hint", w_hint), Guide("v", [tr, br], "hint", w_hint)]
    crop = np.array(hints["crop"], float).reshape(-1, 2) if hints.get("crop") else None
    return out, crop

