"""Page layout for the PDF: every page on the same paper size with the same margins.

Photos are taken from slightly different heights, so the print comes out at slightly
different sizes. The printed area of each page is found (dirt touching the image edge is
ignored), and each page is scaled (within limits) so that its printed area matches the typical
size of the set - width and height for full pages, width for a partial page. That area is then
placed on a common page:
centred left to right, at the same distance from the top. A partial page (only a few lines)
therefore sits at the top with empty paper below, like the last page of an article.
"""
import cv2
import numpy as np

from .layout import content_box
from .tone import estimate_text_height


def _drop_edge_dirt(gray):
    """Copy of the page with every dark patch that touches the image edge turned white."""
    n, lab = cv2.connectedComponents((gray < 200).astype(np.uint8), connectivity=8)
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    out = gray.copy()
    out[np.isin(lab, edge[edge > 0])] = 255
    return out


def measure(gray):
    """Text height (px) and printed area (x0, y0, x1, y1) of a cleaned grey page."""
    g = _drop_edge_dirt(gray)
    h = estimate_text_height(g.astype(np.float32))
    H, W = gray.shape
    x0, y0, x1, y1 = content_box(g, (W, H, 0, 0), h, 0)
    if x1 <= x0 or y1 <= y0:                       # blank page
        x0, y0, x1, y1 = 0, 0, W, H
    return h, (x0, y0, x1, y1)


def layout(grays, margin=0.08, max_scale=0.15):
    """Place cleaned grey pages (uint8) on one common page size.

    margin     white border on every side, as a fraction of the widest printed area
    max_scale  pages are scaled by at most this much (0.15 = 85 % .. 115 %) to match sizes
    Returns the list of new page images (uint8, all the same size).
    """
    boxes = [measure(g)[1] for g in grays]
    ws = np.array([x1 - x0 for x0, _, x1, _ in boxes], float)
    hs = np.array([y1 - y0 for _, y0, _, y1 in boxes], float)
    full = hs >= 0.6 * np.median(hs)               # pages printed from top to bottom
    mw = float(np.median(ws[full])) if full.any() else float(np.median(ws))
    mh = float(np.median(hs[full])) if full.any() else float(np.median(hs))
    parts = []
    for g, (x0, y0, x1, y1), w, hh, f in zip(grays, boxes, ws, hs, full):
        s = float(np.sqrt(mw / w * mh / hh)) if f else mw / w
        s = float(np.clip(s, 1 - max_scale, 1 + max_scale))
        part = g[y0:y1, x0:x1]
        if abs(s - 1) > 0.005:
            part = cv2.resize(part, None, fx=s, fy=s, interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        parts.append(part)
    cw = max(p.shape[1] for p in parts)
    ch = max(p.shape[0] for p in parts)
    m = int(round(margin * cw))
    W, H = cw + 2 * m, ch + 2 * m
    pages = []
    for p in parts:
        page = np.full((H, W), 255, np.uint8)
        x = m + (cw - p.shape[1]) // 2                 # centred left to right
        page[m:m + p.shape[0], x:x + p.shape[1]] = p    # same top margin everywhere
        pages.append(page)
    return pages
