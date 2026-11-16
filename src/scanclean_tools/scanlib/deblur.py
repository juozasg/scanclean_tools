"""Out-of-focus correction: a phone photo of a curved page is sharp in one part and blurred
where the paper bends away from the focal plane.

1. blur_map: local blur, as the sigma of a Gaussian, from how much the edges of the print
   soften when the image is blurred once more (the re-blur gradient ratio).
2. deblur: the image is denoised, deconvolved (Wiener filter) for a ladder of blur levels, and
   each pixel takes the level that matches the blur its region has beyond the sharpest part
   of the page. Sharp regions stay as they are.
"""
import cv2
import numpy as np


def blur_map(gray, h_px, s0=1.5):
    """Per-pixel Gaussian blur sigma (px) of the print, smooth over the page."""
    g = gray.astype(np.float32)

    def grad(a):
        return np.hypot(cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3))

    g1, g2 = grad(g), grad(cv2.GaussianBlur(g, (0, 0), s0))
    edges = cv2.Canny(np.clip(g, 0, 255).astype(np.uint8), 40, 100) > 0
    if not edges.any():
        return np.zeros_like(g)
    edges &= g1 > np.percentile(g1[edges], 50)                      # clear edges only
    r = g1 / np.maximum(g2, 1e-3)
    sig = np.where(edges & (r > 1.01), s0 / np.sqrt(np.maximum(r * r - 1, 1e-6)), np.nan)
    tile = max(int(4 * h_px), 32)
    H, W = g.shape
    gh, gw = H // tile + 1, W // tile + 1
    m = np.full((gh, gw), np.nan, np.float32)
    for i in range(gh):
        for j in range(gw):
            v = sig[i * tile:(i + 1) * tile, j * tile:(j + 1) * tile]
            v = v[np.isfinite(v)]
            if len(v) > 30:
                m[i, j] = np.median(v)
    ok = np.isfinite(m)
    if not ok.any():
        return np.zeros_like(g)
    num = cv2.GaussianBlur(np.where(ok, m, 0).astype(np.float32), (0, 0), 1.2)
    den = cv2.GaussianBlur(ok.astype(np.float32), (0, 0), 1.2)
    m = np.where(den > 0.05, num / np.maximum(den, 1e-6), float(np.median(m[ok])))
    return cv2.resize(m.astype(np.float32), (W, H), interpolation=cv2.INTER_CUBIC)


def _wiener_levels(g, sigmas, K):
    """g deconvolved for a Gaussian blur of each sigma (Wiener filter, noise-to-signal K)."""
    pad = 32
    a = cv2.copyMakeBorder(g, pad, pad, pad, pad, cv2.BORDER_REFLECT)
    F = np.fft.rfft2(a)
    f2 = np.fft.fftfreq(a.shape[0])[:, None] ** 2 + np.fft.rfftfreq(a.shape[1])[None, :] ** 2
    out = []
    for s in sigmas:
        if s <= 0:
            out.append(g.astype(np.float32))
            continue
        Hf = np.exp(-2 * np.pi ** 2 * s * s * f2)
        r = np.fft.irfft2(F * Hf / (Hf * Hf + K), s=a.shape)
        out.append(r[pad:-pad, pad:-pad].astype(np.float32))
    return out


def deblur(gray, h_px, gain=2.5, K=0.03, step=0.5, smax=3.0):
    """Sharpen the blurry parts of a grey page image (float32, 0-255). gain scales the
    estimated extra blur; 0 returns the image unchanged."""
    gray = gray.astype(np.float32)
    if gain <= 0:
        return gray
    bm = blur_map(gray, h_px)
    ref = float(np.percentile(bm, 20))                              # the sharp part of the page
    extra = gain * np.sqrt(np.maximum(bm ** 2 - ref ** 2, 0))
    if extra.max() < step / 2:
        return gray
    clean = cv2.fastNlMeansDenoising(np.clip(gray, 0, 255).astype(np.uint8), None, 6, 5, 15).astype(np.float32)
    sig = np.arange(0, smax + 1e-6, step)
    levels = _wiener_levels(clean, sig, K)
    t = np.clip(extra / step, 0, len(sig) - 1.001)
    i = np.floor(t).astype(int)
    f = (t - i).astype(np.float32)
    out = np.zeros_like(gray)
    for k in range(len(sig) - 1):
        m = i == k
        out[m] = (1 - f[m]) * levels[k][m] + f[m] * levels[k + 1][m]
    return out
