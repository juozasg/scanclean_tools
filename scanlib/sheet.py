"""Geometry: camera + curled-sheet model, residual polish field, resampling maps."""
import math

import cv2
import numpy as np
from scipy.optimize import least_squares


class Sheet:
    """Pinhole camera at distance 1 from a curled sheet  Z = sum_k c_k (X/X0)^k, k = 2..5.

    Page frame: X across the page (perpendicular to the gutter), Y along the gutter.
    Flat coordinates are (arc length along X, Y), reported in pixels (multiplied by f).
    """
    NC = 4

    def __init__(self, size, f):
        self.W, self.H = size
        self.f = float(f)
        self.cx, self.cy = (self.W - 1) / 2, (self.H - 1) / 2
        self.X0 = 0.5 * self.W / self.f
        self.p = np.zeros(3 + self.NC)
        self.tlim = (-1.0, 1.0)
        self._tab = None

    # surface ------------------------------------------------------------------
    def _g(self, X, c):
        t = X / self.X0
        tc = np.clip(t, self.tlim[0], self.tlim[1])        # polynomial only where guides exist,
        p = ((c[3] * tc + c[2]) * tc + c[1]) * tc ** 3 + c[0] * tc * tc
        dp = 2 * c[0] * tc + 3 * c[1] * tc ** 2 + 4 * c[2] * tc ** 3 + 5 * c[3] * tc ** 4
        return p + dp * (t - tc)                           # straight continuation beyond

    def _dg(self, X, c):
        tc = np.clip(X / self.X0, self.tlim[0], self.tlim[1])
        return (2 * c[0] * tc + 3 * c[1] * tc ** 2 + 4 * c[2] * tc ** 3 + 5 * c[3] * tc ** 4) / self.X0

    def _arc_table(self, c):
        X = np.linspace(-3 * self.X0, 3 * self.X0, 1201)
        ds = np.sqrt(1 + self._dg(X, c) ** 2)
        s = np.concatenate([[0], np.cumsum(0.5 * (ds[1:] + ds[:-1]) * np.diff(X))])
        s -= np.interp(0.0, X, s)
        return X, s

    # mappings -----------------------------------------------------------------
    def to_flat(self, pts, p=None):
        """photo pixels -> flat page pixels."""
        p = self.p if p is None else p
        R, _ = cv2.Rodrigues(np.asarray(p[:3], np.float64))
        c = p[3:]
        ray = np.stack([(pts[:, 0] - self.cx) / self.f, (pts[:, 1] - self.cy) / self.f,
                        np.ones(len(pts))], 1)
        d = ray @ R                         # R^T ray
        o = -(np.array([0, 0, 1.0]) @ R)    # camera centre in the page frame
        lam = -o[2] / d[:, 2]
        for _ in range(8):
            X = o[0] + lam * d[:, 0]
            F = o[2] + lam * d[:, 2] - self._g(X, c)
            dF = d[:, 2] - self._dg(X, c) * d[:, 0]
            lam = lam - F / np.where(np.abs(dF) < 1e-6, 1e-6, dF)
        X = o[0] + lam * d[:, 0]
        Y = o[1] + lam * d[:, 1]
        Xt, st = self._arc_table(c)
        return np.stack([np.interp(X, Xt, st), Y], 1) * self.f

    def to_photo(self, flat):
        """flat page pixels -> photo pixels (used to build the resampling map)."""
        R, _ = cv2.Rodrigues(self.p[:3])
        c = self.p[3:]
        if self._tab is None:
            self._tab = self._arc_table(c)
        Xt, st = self._tab
        X = np.interp(flat[:, 0] / self.f, st, Xt)
        P = np.stack([X, flat[:, 1] / self.f, self._g(X, c)], 1) @ R.T + np.array([0, 0, 1.0])
        return np.stack([self.f * P[:, 0] / P[:, 2] + self.cx, self.f * P[:, 1] / P[:, 2] + self.cy], 1)

    # fitting ------------------------------------------------------------------
    def fit(self, guides, reg=0.02, verbose=False):
        pts = np.concatenate([g.pts for g in guides])
        t = (pts[:, 0] - self.cx) / (0.5 * self.W)
        self.tlim = (float(np.percentile(t, 1)) - 0.08, float(np.percentile(t, 99)) + 0.08)
        gid = np.concatenate([np.full(len(g.pts), i) for i, g in enumerate(guides)])
        axis = np.concatenate([np.full(len(g.pts), 1 if g.kind == "h" else 0) for g in guides])
        wts = np.sqrt(np.concatenate([np.full(len(g.pts), g.w) for g in guides]))
        ng = len(guides)
        cnt = np.bincount(gid, minlength=ng).astype(float)
        idx = np.arange(len(pts))
        regw = reg * self.f * np.array([1.0, 1.0, 2.0, 2.0])
        loss_scale = 2.5 * max(self.W, self.H) / 4000

        def resid(p):
            fl = self.to_flat(pts, p)
            v = fl[idx, axis]
            mean = np.bincount(gid, v, minlength=ng) / cnt
            r = (v - mean[gid]) * wts
            return np.concatenate([r, regw * p[3:]])

        # initial in-plane rotation from guide directions
        ang = []
        for g in guides:
            dv = g.pts[-1] - g.pts[0]
            a = math.atan2(dv[1], dv[0]) if g.kind == "h" else math.atan2(-dv[0], dv[1])
            a = (a + math.pi / 2) % math.pi - math.pi / 2
            ang.append(a)
        th0 = float(np.median(ang)) if ang else 0.0
        best = None
        lim = np.array([0.6, 0.6, 0.8, 0.25, 0.25, 0.15, 0.15])
        for c2 in (0.0, 0.03, -0.03):
            p0 = np.array([0, 0, th0, c2, 0, 0, 0], float)
            try:
                r = least_squares(resid, p0, loss="soft_l1", f_scale=loss_scale, x_scale="jac",
                                  bounds=(-lim, lim), max_nfev=200)
            except Exception as e:                       # pragma: no cover
                if verbose:
                    print("   fit failed:", e)
                continue
            if best is None or r.cost < best.cost:
                best = r
        if best is None:
            return None
        self.p = best.x
        self._tab = None
        return best


class Polish:
    """Smooth 2-D correction of the flat coordinates so that guides are exactly straight.

    dy(x,y) contains only terms that depend on x, dx(x,y) only terms that depend on y, so the
    correction cannot shrink the page to make all guides trivially 'constant'.
    """

    def __init__(self, lo, hi, deg_main=4, deg_cross=3):
        self.c0 = (np.asarray(lo, float) + np.asarray(hi, float)) / 2
        self.half = np.maximum((np.asarray(hi, float) - np.asarray(lo, float)) / 2, 1.0)
        self.dm, self.dc = deg_main, deg_cross
        self.coef = {0: None, 1: None}       # displacement of x (0) and y (1)
        self.qlo, self.qhi = np.full(2, -1.0), np.full(2, 1.0)

    def _basis(self, P, axis):
        """axis=1: correction of y -> terms x^i y^j, i>=1.   axis=0: terms y^i x^j, i>=1."""
        q = np.clip((P - self.c0) / self.half, self.qlo, self.qhi)   # no polynomial extrapolation
        a, b = (q[:, 0], q[:, 1]) if axis == 1 else (q[:, 1], q[:, 0])
        cols = [a ** i * b ** j for i in range(1, self.dm + 1) for j in range(0, self.dc + 1)
                if i + j <= self.dm + 1]
        return np.stack(cols, 1)

    def fit(self, flat_guides, lam=3.0, huber=2.0):
        allp = np.concatenate([P for _, P in flat_guides])
        q = (allp - self.c0) / self.half
        pad = 0.08 * (q.max(0) - q.min(0))
        self.qlo, self.qhi = q.min(0) - pad, q.max(0) + pad
        for axis, kind in ((1, "h"), (0, "v")):
            A, b, w = [], [], []
            for g, P in flat_guides:
                if g.kind != kind or len(P) < 2:
                    continue
                Phi = self._basis(P, axis)
                A.append(Phi - Phi.mean(0))
                b.append(-(P[:, axis] - P[:, axis].mean()))
                w.append(np.full(len(P), math.sqrt(g.w)))
            if not A or sum(len(x) for x in A) < 40:
                continue
            A, b, w = np.concatenate(A), np.concatenate(b), np.concatenate(w)
            nb = A.shape[1]
            c = np.zeros(nb)
            for _ in range(6):
                r = A @ c - b
                ww = w * np.minimum(1.0, huber / np.maximum(np.abs(r), 1e-9)) ** 0.5
                Aw = A * ww[:, None]
                c = np.linalg.solve(Aw.T @ Aw + lam * np.eye(nb), Aw.T @ (b * ww))
            self.coef[axis] = c

    def delta(self, P):
        d = np.zeros_like(P)
        for axis in (0, 1):
            if self.coef[axis] is not None:
                d[:, axis] = self._basis(P, axis) @ self.coef[axis]
        return d

    def forward(self, P):                      # flat -> final
        return P + self.delta(P)

    def inverse(self, Q):                      # final -> flat (fixed point, deformation is small)
        P = Q.copy()
        for _ in range(6):
            P = Q - self.delta(P)
        return P


def guide_rms(g, P):
    axis = 1 if g.kind == "h" else 0
    return float(np.sqrt(np.mean((P[:, axis] - P[:, axis].mean()) ** 2)))


def straightness(guides, mapped):
    """Typical deviation (px) of guide points from their straight line, per direction.
    The worst 10 % of points are ignored so that a few bad guides do not hide a good fit."""
    out = {}
    for kind, axis in (("h", 1), ("v", 0)):
        r = [P[:, axis] - P[:, axis].mean() for g, P in zip(guides, mapped)
             if g.kind == kind and g.src != "margin" and len(P) > 2]
        if r:
            r = np.sort(np.abs(np.concatenate(r)))
            r = r[:max(int(0.9 * len(r)), 1)]
            out[kind] = float(np.sqrt(np.mean(r ** 2)))
    return out


def build_maps(sheet, polish, x0, y0, w, h, step=8):
    """Sampling maps (photo x, photo y) for the output rectangle [x0,x0+w) x [y0,y0+h)."""
    gx = np.arange(0, w + step, step, dtype=np.float64)
    gy = np.arange(0, h + step, step, dtype=np.float64)
    GX, GY = np.meshgrid(gx + x0, gy + y0)
    Q = np.stack([GX.ravel(), GY.ravel()], 1)
    P = polish.inverse(Q) if polish is not None else Q
    S = sheet.to_photo(P).reshape(len(gy), len(gx), 2).astype(np.float32)
    # exact bilinear upsampling of the coarse grid
    M = np.float32([[1 / step, 0, 0], [0, 1 / step, 0]])
    full = cv2.warpAffine(S, M, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)
    return full[:, :, 0], full[:, :, 1]

