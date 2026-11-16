"""Grid straightening (step 2b): a smooth free-form warp of a cleaned page that makes its
text lines, rules and margins straight and square.

Step 1 has already removed perspective and page curl, so what is left is small and local
(a few px of bending, a ripple, uneven curl near the gutter). No camera or sheet model is
used here, only a grid:

model     a lattice of control nodes (one every `cell` text heights); each node has an unknown
          2-D shift, and the shift D(p) of any pixel is the cubic B-spline blend of the 4 x 4
          nodes around it. A page point p goes to p + D(p).
guides    automatic (text lines, table / figure rules, text-block margins, found on the clean
          page) and manual (lines, rectangles, right angles, ignore boxes - see grid_tool.py)
energy    straight + level: the points of a horizontal guide should share one y after the
          warp, the points of a vertical guide one x
          right angles: where rules cross, and at every manual right-angle hint
          even spacing (weak): neighbouring text lines of one column equally far apart
          smoothness: little bending of the grid (second differences of the node shifts)
          shape: grid cells keep their size and shape (no stretch, no shear)
          gauge: the page centre does not move
solve     everything except the right angles is linear in the node shifts, so each round is
          one sparse weighted least-squares solve; the angles are linearised (Gauss-Newton)
          and a Huber weight tames bad points (IRLS). A coarse grid first finds automatic
          guides the warp cannot straighten (misdetections); they are dropped, then the fine
          grid is solved. If any grid cell would fold over, the smoothness is raised.
output    the inverse map (output pixel -> page pixel, by fixed-point iteration), one
          resample, then light grey haze from the resampling is turned back to white.
"""
import json

import cv2
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from .guides import Guide, line_ends, margin_curve, text_is_rotated, trace_rules, trace_text_lines
from .hints import apply_hints
from .paper import char_height


# ---------------------------------------------------------------------- guides on a clean page
def find_clean_guides(gray, h=None, work_long=2000):
    """Automatic guides of a cleaned page (white paper, black ink). Returns (guides, info)."""
    H0, W0 = gray.shape
    sw = min(1.0, work_long / max(H0, W0))
    g = cv2.resize(gray, None, fx=sw, fy=sw, interpolation=cv2.INTER_AREA) if sw < 1 else gray
    norm = g.astype(np.float32) / 255.0
    ink = (norm < 0.5).astype(np.uint8)
    hw = h * sw if h else char_height(ink)
    hw = max(hw, 4.0)
    Hw, Ww = ink.shape
    guides = []

    def add(kind, pts_w, src, w=1.0, transposed=False):
        p = np.asarray(pts_w, np.float64)
        if transposed:
            p = p[:, ::-1]
        guides.append(Guide(kind, (p + 0.5) / sw - 0.5, src, w))

    valid = np.ones((Hw, Ww), np.float32)
    min_len = max(10 * hw, 0.10 * min(Hw, Ww))
    rh, mh = trace_rules(norm, valid, hw, min_len)
    rv, mv = trace_rules(np.ascontiguousarray(norm.T), np.ascontiguousarray(valid.T), hw, min_len)
    for p in rh:
        add("h", p, "rule", 1.5)
    for p in rv:
        add("v", p, "rule", 1.5, transposed=True)
    rules_m = (mh | np.ascontiguousarray(mv.T)).astype(bool)
    txt = (ink & ~rules_m).astype(np.uint8)
    rotated = text_is_rotated(txt, hw)
    tmask = np.ascontiguousarray(txt.T) if rotated else txt
    lines, _ = trace_text_lines(tmask, hw)
    for ln in lines:
        add("v" if rotated else "h", ln["pts"], "text", 1.0, transposed=rotated)
    if len(lines) >= 8:
        Lp, Rp = line_ends(lines, tmask, hw)
        for side, P in ((0, Lp), (1, Rp)):
            m = margin_curve(P, hw, side)
            if m is not None:
                add("h" if rotated else "v", m, "margin", 0.6, transposed=rotated)
    info = {"h": hw / sw, "rotated": bool(rotated), "n_text": len(lines),
            "n_rule": len(rh) + len(rv)}
    return guides, info


def crossings(guides, h):
    """Points where a horizontal and a vertical rule (or hint line) meet or cross:
    list of (point, direction along the h line, direction along the v line)."""
    hs = [g for g in guides if g.kind == "h" and g.src in ("rule", "hint")]
    vs = [g for g in guides if g.kind == "v" and g.src in ("rule", "hint")]
    out = []
    for gh in hs:
        ph = gh.pts[np.argsort(gh.pts[:, 0])]
        for gv in vs:
            pv = gv.pts[np.argsort(gv.pts[:, 1])]
            x = float(np.median(pv[:, 0]))
            y = float(np.median(ph[:, 1]))
            for _ in range(3):
                y = float(np.interp(x, ph[:, 0], ph[:, 1]))
                x = float(np.interp(y, pv[:, 1], pv[:, 0]))
            tol = 0.5 * h
            if not (ph[0, 0] - tol <= x <= ph[-1, 0] + tol and pv[0, 1] - tol <= y <= pv[-1, 1] + tol):
                continue
            d = 3 * h
            # arms point into the lines from the crossing (a corner of a frame has one arm each way)
            sx = 1 if x + d <= ph[-1, 0] + tol else -1
            sy = 1 if y + d <= pv[-1, 1] + tol else -1
            a = np.array([x + sx * d, np.interp(x + sx * d, ph[:, 0], ph[:, 1])])
            c = np.array([np.interp(y + sy * d, pv[:, 1], pv[:, 0]), y + sy * d])
            out.append((a, np.array([x, y]), c))
    return out


# ---------------------------------------------------------------------- B-spline grid
def _bspline(u):
    """Uniform cubic B-spline weights of the 4 nodes around fractional position u (0..1)."""
    u2, u3 = u * u, u * u * u
    return np.stack([(1 - u) ** 3 / 6, (3 * u3 - 6 * u2 + 4) / 6, (-3 * u3 + 3 * u2 + 3 * u + 1) / 6, u3 / 6], -1)


class Grid:
    """Control lattice over a W x H page: cx x cy cells, (cx + 3) x (cy + 3) nodes."""

    def __init__(self, W, H, cx, cy):
        self.W, self.H, self.cx, self.cy = W, H, max(int(cx), 1), max(int(cy), 1)
        self.sx, self.sy = (W - 1) / self.cx, (H - 1) / self.cy
        self.nx, self.ny = self.cx + 3, self.cy + 3
        self.n = self.nx * self.ny

    def _axis(self, t, cells):
        i0 = np.clip(np.floor(t).astype(int), 0, cells - 1)
        return i0, _bspline(np.clip(t - i0, 0.0, 1.0))

    def axis_matrix(self, coords, horizontal):
        """Dense (len(coords), nx or ny) 1-D basis matrix along one axis."""
        s, cells, n = (self.sx, self.cx, self.nx) if horizontal else (self.sy, self.cy, self.ny)
        i0, w = self._axis(np.asarray(coords, np.float64) / s, cells)
        M = np.zeros((len(i0), n))
        for k in range(4):
            np.add.at(M, (np.arange(len(i0)), i0 + k), w[:, k])
        return M

    def matrix(self, pts):
        """Sparse (N, n) matrix B with D(p) = B @ u for node shifts u (one coordinate)."""
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        ix, wx = self._axis(pts[:, 0] / self.sx, self.cx)
        iy, wy = self._axis(pts[:, 1] / self.sy, self.cy)
        rows = np.repeat(np.arange(len(pts)), 16)
        cols = ((iy[:, None, None] + np.arange(4)[None, :, None]) * self.nx
                + ix[:, None, None] + np.arange(4)[None, None, :]).reshape(-1)
        vals = (wy[:, :, None] * wx[:, None, :]).reshape(-1)
        return sparse.csr_matrix((vals, (rows, cols)), shape=(len(pts), self.n))

    def field(self, ux, uy, xs, ys):
        """Shift field on the lattice of x positions xs and y positions ys: (Dx, Dy) arrays."""
        Bx, By = self.axis_matrix(xs, True), self.axis_matrix(ys, False)
        return (By @ ux.reshape(self.ny, self.nx) @ Bx.T), (By @ uy.reshape(self.ny, self.nx) @ Bx.T)

    def forward(self, ux, uy, pts):
        """Page points -> output points."""
        pts = np.asarray(pts, np.float64).reshape(-1, 2)
        B = self.matrix(pts)
        return pts + np.stack([B @ ux, B @ uy], 1)


# ---------------------------------------------------------------------- energy rows
class Rows:
    """Collects weighted linear rows a . p = b for the parameter vector p = [ux, uy]."""

    def __init__(self, n):
        self.n, self.A, self.b, self.w, self.tag = n, [], [], [], []

    def add(self, A, b, w, tag=-1):
        A = sparse.csr_matrix(A)
        m = int(A.get_shape()[0])
        self.A.append(A)
        self.b.append(np.broadcast_to(np.asarray(b, np.float64), (m,)))
        self.w.append(np.broadcast_to(np.asarray(w, np.float64), (m,)))
        self.tag.append(np.full(m, tag))

    def stack(self):
        return (sparse.vstack(self.A).tocsr(), np.concatenate(self.b), np.concatenate(self.w),
                np.concatenate(self.tag))


def _xy(B, n, kind):
    """Rows acting on ux (kind 'v' -> x) or uy (kind 'h' -> y)."""
    Z = sparse.csr_matrix(B.shape)
    return sparse.hstack([B, Z] if kind == "v" else [Z, B]).tocsr()


def _line_rows(grid, g):
    """Straight + level: every point minus the guide's mean, along y (h) or x (v)."""
    B = grid.matrix(g.pts)
    Bc = B - sparse.csr_matrix(np.ones((B.shape[0], 1))) @ sparse.csr_matrix(B.mean(0))
    c = g.pts[:, 1 if g.kind == "h" else 0]
    return _xy(Bc, grid.n, g.kind), -(c - c.mean())


def _spacing_rows(grid, guides, h):
    """Weak: three neighbouring text lines of one column should be equally far apart.
    Returns a list of (row, b)."""
    out = []
    for kind in "hv":
        a = 1 if kind == "h" else 0
        L = [g for g in guides if g.src == "text" and g.kind == kind]
        L.sort(key=lambda g: g.pts[:, a].mean())
        for i in range(1, len(L) - 1):
            g0, g1, g2 = L[i - 1], L[i], L[i + 1]
            lo = [g.pts[:, 1 - a].min() for g in (g0, g1, g2)]
            hi = [g.pts[:, 1 - a].max() for g in (g0, g1, g2)]
            ov = min(hi) - max(lo)
            if ov < 0.5 * min(h2 - l2 for l2, h2 in zip(lo, hi)):
                continue
            m = [g.pts[:, a].mean() for g in (g0, g1, g2)]
            s1, s2 = m[1] - m[0], m[2] - m[1]
            if not (0.8 * h < s1 < 4 * h and 0.75 < s2 / s1 < 1.33):
                continue
            Bm = [np.asarray(grid.matrix(g.pts).mean(0)).ravel() for g in (g0, g1, g2)]
            row = Bm[2] - 2 * Bm[1] + Bm[0]
            full = np.zeros(2 * grid.n)
            full[(grid.n if kind == "h" else 0) + np.arange(grid.n)] = row
            out.append((full, -(m[2] - 2 * m[1] + m[0])))
    return out


def _angle_rows(grid, p, tri, scale):
    """Right angle at tri[1] between the arms to tri[0] and tri[2], linearised at p.
    Residual = arm length * cos(angle). Returns (row, b) with row . p = b."""
    ux, uy = p[:grid.n], p[grid.n:]
    P = np.asarray(tri, np.float64)
    B = grid.matrix(P).toarray()
    q = P + np.stack([B @ ux, B @ uy], 1)
    e1, e2 = q[0] - q[1], q[2] - q[1]
    l1, l2 = np.linalg.norm(e1) + 1e-9, np.linalg.norm(e2) + 1e-9
    cos = e1 @ e2 / (l1 * l2)
    L = scale if scale else 0.5 * (l1 + l2)
    g1 = L * (e2 / (l1 * l2) - cos * e1 / l1 ** 2)          # d r / d e1
    g2 = L * (e1 / (l1 * l2) - cos * e2 / l2 ** 2)          # d r / d e2
    gq = np.stack([g1, -g1 - g2, g2])                         # d r / d q0, q1, q2
    row = np.concatenate([gq[:, 0] @ B, gq[:, 1] @ B])
    r = L * cos
    return row, row @ p - r


SHEAR = 1.0
LAM_S, LAM_E, LAM_0 = 4.0, 0.6, 0.1


def _reg_rows(grid, lam_s, lam_e, lam_g):
    """Smoothness (second differences), shape (stretch and shear) and a weak pull of every node
    towards no shift (keeps empty areas unchanged and fixes the page position)."""
    ny, nx, n = grid.ny, grid.nx, grid.n
    idx = np.arange(n).reshape(ny, nx)
    rows, cols, vals, w = [], [], [], []
    r = 0

    def add(terms, weight):
        nonlocal r
        for c, v in terms:
            rows.append(r)
            cols.append(c)
            vals.append(v)
        w.append(weight)
        r += 1

    for off in (0, n):                                         # ux then uy
        for i in range(ny):
            for j in range(nx):
                k = idx[i, j] + off
                if 0 < j < nx - 1:
                    add([(k - 1, 1), (k, -2), (k + 1, 1)], lam_s)
                if 0 < i < ny - 1:
                    add([(k - nx, 1), (k, -2), (k + nx, 1)], lam_s)
                if i < ny - 1 and j < nx - 1:
                    add([(k, 1), (k + 1, -1), (k + nx, -1), (k + nx + 1, 1)], lam_s)
    for i in range(ny):
        for j in range(nx):
            k = idx[i, j]
            if j < nx - 1:
                add([(k, -1), (k + 1, 1)], lam_e)                     # stretch along x
            if i < ny - 1:
                add([(n + k, -1), (n + k + nx, 1)], lam_e)            # stretch along y
            if i < ny - 1 and j < nx - 1:                              # shear
                add([(k, -1), (k + nx, 1), (n + k, -1), (n + k + 1, 1)], SHEAR * lam_e)
    for k in range(2 * n):                                             # stay put where nothing pulls
        add([(k, 1)], lam_g)
    A = sparse.csr_matrix((vals, (rows, cols)), shape=(r, 2 * n))
    return A, np.zeros(r), np.array(w)


# ---------------------------------------------------------------------- solver
def _solve(grid, guides, angles, h, strength, iters=6, huber=0.3, p0=None):
    """Weighted least squares with Huber IRLS and Gauss-Newton for the angles.
    Returns node shifts p, per-guide RMS residual after the fit."""
    n = grid.n
    lam_s = LAM_S / max(strength, 1e-3)
    lam_e = LAM_E / max(strength, 1e-3)
    R = Rows(2 * n)
    for gi, g in enumerate(guides):
        if len(g.pts) < 2:
            continue
        A, b = _line_rows(grid, g)
        R.add(A, b, g.w, gi)
    for row, b in _spacing_rows(grid, guides, h):
        R.add(row[None, :], b, 0.3)
    A_reg, b_reg, w_reg = _reg_rows(grid, lam_s, lam_e, LAM_0)
    A_lin, b_lin, w_lin, tag = R.stack() if R.A else (sparse.csr_matrix((0, 2 * n)), np.zeros(0), np.zeros(0),
                                                       np.zeros(0, int))
    p = np.zeros(2 * n) if p0 is None else p0.copy()
    rw = np.ones(len(b_lin))
    c = huber * h
    for _ in range(iters):
        ang = [_angle_rows(grid, p, tri, None) for tri, _w in angles]
        if ang:
            A_ang = sparse.csr_matrix(np.array([a for a, _ in ang]))
            b_ang = np.array([b for _, b in ang])
            w_ang = np.array([w for _t, w in angles])
            A = sparse.vstack([A_lin, A_ang, A_reg]).tocsr()
            b = np.concatenate([b_lin, b_ang, b_reg])
            w = np.concatenate([w_lin * rw, w_ang, w_reg])
        else:
            A = sparse.vstack([A_lin, A_reg]).tocsr()
            b = np.concatenate([b_lin, b_reg])
            w = np.concatenate([w_lin * rw, w_reg])
        W2 = sparse.diags(w ** 2)
        AtW = A.T @ W2
        p_new = spsolve((AtW @ A + 1e-9 * sparse.eye(2 * n)).tocsc(), AtW @ b)
        step = np.abs(p_new - p).max()
        p = np.asarray(p_new).ravel()
        r = A_lin @ p - b_lin
        rw = np.minimum(1.0, c / np.maximum(np.abs(r), 1e-9)) ** 0.5
        if step < 0.02 and not ang:
            break
    r = A_lin @ p - b_lin
    rms = {}
    for gi in np.unique(tag):
        m = tag == gi
        if gi >= 0:
            rms[int(gi)] = float(np.sqrt(np.mean(r[m] ** 2)))
    return p, rms


def _fold_ok(grid, p, step=16, min_det=0.3):
    xs = np.arange(0, grid.W, step, dtype=np.float64)
    ys = np.arange(0, grid.H, step, dtype=np.float64)
    Dx, Dy = grid.field(p[:grid.n], p[grid.n:], xs, ys)
    dxx, dxy = np.gradient(Dx, step, axis=1), np.gradient(Dx, step, axis=0)
    dyx, dyy = np.gradient(Dy, step, axis=1), np.gradient(Dy, step, axis=0)
    det = (1 + dxx) * (1 + dyy) - dxy * dyx
    return float(det.min()) > min_det


def _plausible(g, h, max_slope=0.02, max_dev=0.4):
    """Is an automatic guide nearly level (h) or plumb (v) and nearly straight already?"""
    a, b = (0, 1) if g.kind == "h" else (1, 0)
    t, c = g.pts[:, a], g.pts[:, b]
    if np.ptp(t) < 1:
        return False
    k = np.polyfit(t, c, 1)[0]
    return abs(k) < max_slope and float(np.sqrt(np.mean((c - c.mean()) ** 2))) < max_dev * h


def straighten(gray, hints=None, h=None, found=None, strength=1.0, cell=4.0, verbose=False):
    """Clean page (uint8 greyscale) -> dict with the straightened `image`, `meta`, the guides
    used (`guides`, after ignore boxes; automatic ones the fit dropped are left out), the RMS
    error of each used guide (`resid`, keyed by its index in `guides`), and `grid` / `p`
    (the warp; grid.forward(p[:n], p[n:], pts) maps page points to the output)."""
    gray = gray if gray.ndim == 2 else cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    if found is None:
        found = find_clean_guides(gray, h)
    auto, info = found
    h = float(h or info["h"])
    hints = hints or {}
    strength = float(hints.get("strength", strength))
    guides, _ = apply_hints([Guide(g.kind, g.pts.copy(), g.src, g.w) for g in auto], hints)
    # step 1 left the page nearly flat: an automatic guide that is tilted or far from straight
    # is a misdetection (a sloping plot line, a curve of a figure)
    dropped = [g for g in guides if g.src != "hint" and not _plausible(g, h)]
    guides = [g for g in guides if g.src == "hint" or _plausible(g, h)]
    w_hint = 6.0
    angles = []
    for tri in hints.get("angles", []):
        if len(tri) == 3:
            angles.append((np.array(tri, np.float64), w_hint))
    for tri in crossings(guides, h):
        angles.append((np.array(tri), 1.0))

    def grid_for(c):
        return Grid(W, H, max(round(W / (c * h)), 2), max(round(H / (c * h)), 2))

    # coarse grid: find automatic guides the warp cannot straighten, drop them
    coarse = Grid(W, H, 4, 5)
    for _ in range(3):
        p, rms = _solve(coarse, guides, angles, h, strength)
        auto_rms = np.array([rms.get(i, 0.0) for i, g in enumerate(guides) if g.src != "hint"])
        if len(auto_rms) < 3:
            break
        med = float(np.median(auto_rms))
        lim = max(3.0 * 1.4826 * float(np.median(np.abs(auto_rms - med))) + med, 0.35 * h)
        bad = [i for i, g in enumerate(guides) if g.src != "hint" and rms.get(i, 0.0) > lim]
        if not bad:
            break
        dropped += [guides[i] for i in bad]
        guides = [g for i, g in enumerate(guides) if i not in set(bad)]
    angles = [a for a in angles if a[1] != 1.0] + [(np.array(t), 1.0) for t in crossings(guides, h)]

    # fine grid, raising the smoothness while any cell would fold over
    fine = grid_for(cell)
    s = strength
    p, rms = _solve(fine, guides, angles, h, s)
    for _ in range(3):
        if _fold_ok(fine, p):
            break
        s /= 4
        p, rms = _solve(fine, guides, angles, h, s)

    def err(pp):
        e = []
        for g in guides:
            q = fine.forward(pp[:fine.n], pp[fine.n:], g.pts)[:, 1 if g.kind == "h" else 0]
            e.append(q - q.mean())
        return float(np.sqrt(np.mean(np.concatenate(e) ** 2))) if e else 0.0

    zero = np.zeros_like(p)
    before, after = err(zero), err(p)
    out = warp_image(gray, fine, p)
    shift = np.hypot(*fine.field(p[:fine.n], p[fine.n:], np.arange(0, W, 32.0), np.arange(0, H, 32.0)))
    meta = {"text_height": round(h, 2), "grid": [fine.cx, fine.cy], "strength": round(s, 3),
            "guides": len(guides), "dropped": len(dropped), "angles": len(angles),
            "rms_before": round(before, 2), "rms_after": round(after, 2),
            "max_shift": round(float(shift.max()), 1),
            "nodes": {"ux": np.round(p[:fine.n], 2).tolist(), "uy": np.round(p[fine.n:], 2).tolist()}}
    if verbose:
        print(f"  grid {fine.cx}x{fine.cy}, {len(guides)} guides ({len(dropped)} dropped), "
              f"error {before:.2f} -> {after:.2f} px, max shift {meta['max_shift']} px")
    return {"image": out, "meta": meta, "guides": guides, "dropped": dropped, "resid": rms,
            "grid": fine, "p": p, "h": h}


def warp_image(gray, grid, p, step=8, iters=6):
    """Resample: output pixel q takes the page pixel p with p + D(p) = q (fixed-point
    iteration on a coarse lattice, then interpolated to full size)."""
    H, W = gray.shape
    xs = np.arange(0, W + step, step, dtype=np.float64)
    ys = np.arange(0, H + step, step, dtype=np.float64)
    Dx, Dy = grid.field(p[:grid.n], p[grid.n:], xs, ys)
    Dx, Dy = Dx.astype(np.float32), Dy.astype(np.float32)
    qx, qy = np.meshgrid(xs, ys)
    px, py = qx.copy(), qy.copy()
    for _ in range(iters):                                   # p = q - D(p)
        mx = (px / step).astype(np.float32)
        my = (py / step).astype(np.float32)
        dx = cv2.remap(Dx, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        dy = cv2.remap(Dy, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        px, py = qx - dx, qy - dy
    X, Y = np.meshgrid(np.arange(W, dtype=np.float32) / step, np.arange(H, dtype=np.float32) / step)
    mapx = cv2.remap(px.astype(np.float32), X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    mapy = cv2.remap(py.astype(np.float32), X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    out = cv2.remap(gray, mapx, mapy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    out[out > 240] = 255                                       # grey haze of the resampling
    return out



def load_grid_hints(img_path):
    """Hints of a clean page: <page>.grid.json next to it (written by grid-tool), or {}."""
    p = img_path.with_suffix(".grid.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def text_height_for(img_path):
    """Text height of a clean page from step 1's metadata (../flat/meta/<name>.json), or None."""
    m = img_path.resolve().parent.parent / "flat" / "meta" / (img_path.stem + ".json")
    if m.exists():
        return json.loads(m.read_text(encoding="utf-8")).get("text_height")
    return None
