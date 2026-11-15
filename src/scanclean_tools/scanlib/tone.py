"""Step 2 logic: colour page -> greyscale scan (flat-field, levels, clean-up, trim)."""
import cv2
import numpy as np

from .layout import content_box, remove_edge_dirt
from .paper import char_height, find_paper_mask, ink_level, paper_level
from .util import to_gray


def tone(gray, h_px, white=0.88, black=0.25, gamma=1.0, sharpen=0.5):
    """Flat-field + local levels: paper -> white, ink -> black. Returns uint8."""
    bg = paper_level(gray, tile=max(int(3.0 * h_px), 24), fine=max(int(1.2 * h_px), 12))
    n = gray / bg
    if sharpen > 0:
        blur = cv2.GaussianBlur(n, (0, 0), max(h_px / 18, 0.8))
        n = n + sharpen * (n - blur)
    ink = ink_level(n, max(int(5.0 * h_px), 32), 0.12, 0.70)
    lo = ink + black * (white - ink)
    n = np.clip((n - lo) / (white - lo), 0, 1)
    if gamma != 1.0:
        n = n ** gamma
    return (n * 255 + 0.5).astype(np.uint8)


def estimate_text_height(gray, long_side=2000):
    """Typical character height in pixels of `gray` (used when step 1 left no metadata)."""
    s = min(1.0, long_side / max(gray.shape))
    g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else gray
    ink = (g / paper_level(g, tile=max(g.shape) // 40) < 0.80).astype(np.uint8)
    return char_height(ink) / s


def auto_mask(bgr, long_side=2000):
    """Paper mask (255 = paper) computed from the image itself."""
    s = min(1.0, long_side / max(bgr.shape[:2]))
    small = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else bgr
    m = find_paper_mask(small) * 255
    return cv2.resize(m, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)


def tone_page(bgr, mask=None, text_height=None, core=None, trim=True, margin=4.0,
              white=0.88, black=0.25, gamma=1.0, sharpen=0.5):
    """Colour (or grey) page image -> uint8 greyscale scan.

    mask         255 where the pixel belongs to the page; everything else becomes white
    text_height  character height in px (estimated when None)
    core         x0, y0, x1, y1 that must stay inside the trimmed result (optional)
    trim         crop to the printed area plus `margin` text heights
    """
    gray = to_gray(bgr) if bgr.ndim == 3 else bgr.astype(np.float32)
    h = float(text_height) if text_height else estimate_text_height(gray)
    out = tone(gray, h, white, black, gamma, sharpen)
    if mask is not None:
        off = mask < 128
        out[off] = 255
        remove_edge_dirt(out, off, h)
    if trim:
        H, W = out.shape
        x0, y0, x1, y1 = content_box(out, core if core is not None else (W, H, 0, 0), h, margin * h)
        if x1 > x0 and y1 > y0:
            out = out[y0:y1, x0:x1]
    return out
