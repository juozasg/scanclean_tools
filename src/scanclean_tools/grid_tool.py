"""
grid-tool - mark hints on cleaned pages for step 2b (scanclean-grid) and see the result.

    grid-tool CLEAN_DIR                 # step through all clean pages
    grid-tool CLEAN_DIR --only 144 148

Same window and keys as dewarp-tool: the clean page with your hints on the left, the page
straightened by the grid warp on the right; it follows every change after a moment.

What you can mark (press the key, then click on the page):
    h   line that should be HORIZONTAL (text baseline, table rule ...): click 2 or more points
        along it, then ENTER / SPACE or double-click the last point
    v   line that should be VERTICAL (column rule, text margin ...), the same way
    r   rectangle: two opposite corners of a printed rectangle; it becomes 4 straight square edges
    a   right angle: 3 clicks - a point on one arm, the corner, a point on the other arm;
        the corner is made 90 degrees whatever its direction
    x   ignore box: 2 clicks; automatic guides inside it are thrown away (a figure with curves)
    e   edit: click a point of any hint to pick it up, click again to place it (Esc puts it back)

Other keys
    [ / ]        straighten less / more (the strength is saved with the hints of the page)
    g            show / hide the automatic guides, coloured by how straight they end up
                 (green < 0.1, yellow < 0.25, red more text heights; magenta dashed = dropped
                 by the fit, dark dashed = removed by an ignore box), and the warped grid on
                 the right
    wheel, + -   zoom      right-drag  pan      f  fit
    s            save the straightened page as step 2b would: straight/<name>.png and
                 straight/meta/<name>.json next to CLEAN_DIR; the hints are saved too
    u            undo      0  remove all hints      Esc  cancel the item being marked
    n / p        next / previous page (also Right / Left arrow)      q  save hints and quit

Hints are stored next to each clean page as <name>.grid.json (clean page pixel coordinates).
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from .dewarp_tool import COL as DEWARP_COL
from .dewarp_tool import Editor, dashed, guide_key, run
from .scanlib.grid import find_clean_guides, straighten, text_height_for
from .scanlib.util import imread, imwrite, list_images

COL = dict(DEWARP_COL, a=(200, 0, 200))
NEED = {"r": 2, "x": 2, "a": 3}
OPS = [("Horizontal", "h", "h"), ("Vertical", "v", "v"), ("Rectangle", "r", "r"), ("Angle", "a", "a"),
       ("Ignore", "x", "x"), ("Edit", "e", "e"), ("Strength", "[ ]", None), ("Finish", "Enter", None),
       ("Cancel", "Esc", None), ("Undo", "u", None), ("Guides", "g", None), ("List", "l", None),
       ("Fit", "f", None), ("Clear", "0", None), ("Save", "s", None), ("Next", "n/->", None),
       ("Prev", "p/<-", None), ("Quit", "q", None)]
GRID_COL = (150, 200, 150)


def resid_col(e, h):
    """Colour of a guide by its error after the warp (in text heights)."""
    r = e / h
    return (0, 190, 0) if r < 0.1 else (0, 210, 230) if r < 0.25 else (40, 40, 255)


class GridEditor(Editor):
    COL, NEED, OPS = COL, NEED, OPS
    MODES = "hvrxae"
    WORDS = ("straighten", "straightening", "straightened")

    def __init__(self, path, win=(1500, 950)):
        self.strength = 1.0
        self.angles_read = []
        self.h = text_height_for(Path(path))
        self.result_resid = {}             # guide key -> error after the warp
        self.result_grid = None            # (grid, node shifts) of the result
        self.last_res = None               # latest result of the background thread
        super().__init__(path, win)

    # ---- file / model hooks ------------------------------------------------------
    def load(self, path):
        g = imread(path, cv2.IMREAD_GRAYSCALE)
        return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), None

    def find_auto(self):
        return find_clean_guides(self.img[:, :, 0], self.h)

    @property
    def hint_path(self):
        return self.path.with_suffix(".grid.json")

    def read(self):
        super().read()
        if self.hint_path.exists():
            d = json.loads(self.hint_path.read_text(encoding="utf-8"))
            for tri in d.get("angles", []):
                self.items.append(("a", [list(map(float, p)) for p in tri]))
            self.strength = float(d.get("strength", 1.0))

    def hints(self):
        d = super().hints()
        d.pop("crop", None)
        d["angles"] = [[[round(x, 1), round(y, 1)] for x, y in pts] for kind, pts in self.items if kind == "a"]
        d["strength"] = round(self.strength, 3)
        return d

    def write(self):
        if self.items or abs(self.strength - 1.0) > 1e-6:
            self.hint_path.write_text(json.dumps(self.hints(), indent=1), encoding="utf-8")
        elif self.hint_path.exists():
            self.hint_path.unlink()

    def _dewarp(self, key, hints):
        """Step 2b for this page with `hints`; runs in a background thread."""
        try:
            if self.found is None:
                self.found = self.find_auto()
            res = straighten(self.img[:, :, 0], hints, self.h, found=self.found)
            res["used"] = {guide_key(g) for g in res["guides"] if g.src != "hint"}
            res["resid_keys"] = {guide_key(g): res["resid"].get(i, 0.0) for i, g in enumerate(res["guides"])}
            res["image"] = cv2.cvtColor(res["image"], cv2.COLOR_GRAY2BGR)
            res["mask"] = np.zeros(1, np.uint8)
            self.last_res = res
            self.done = (key, res, None)
        except Exception as e:             # the tool must keep working
            self.done = (key, None, f"{type(e).__name__}: {e}")

    def poll(self):
        super().poll()
        res = self.last_res                # extras of the result now shown on the right
        if res is not None and self.result_meta is res["meta"]:
            self.result_resid = res["resid_keys"]
            self.result_grid = (res["grid"], res["p"], res["h"])
            self.last_res = None

    def save_flat(self):
        """Write the straightened page as step 2b does: straight/<name>.png + meta."""
        self.want_save = False
        if self.error or self.result is None or self.result_meta is None:
            self.msg = f"not saved, straightening failed: {self.error}"
            return
        out = self.path.resolve().parent.parent / "straight"
        (out / "meta").mkdir(parents=True, exist_ok=True)
        dst = out / (self.path.stem + ".png")
        imwrite(dst, self.result[:, :, 0], [cv2.IMWRITE_PNG_COMPRESSION, 6])
        jpg = out / (self.path.stem + ".jpg")
        if jpg.exists():
            jpg.unlink()
        meta = dict(self.result_meta, page=self.path.stem, source=self.path.name)
        (out / "meta" / (self.path.stem + ".json")).write_text(json.dumps(meta), encoding="utf-8")
        self.write()
        self.saved_key = self.result_key
        m = self.result_meta
        self.msg = f"saved {out.name}/{dst.name}  error {m['rms_before']:.2f} -> {m['rms_after']:.2f} px"

    def extra_key(self, ch, k):
        if ch in ("[", "]"):
            f = 1.5 if ch == "]" else 1 / 1.5
            self.strength = float(np.clip(self.strength * f, 0.1, 10.0))
            self.msg = f"strength {self.strength:.2f}"
        return None

    # ---- drawing ----------------------------------------------------------------
    def _draw_auto(self, vis):
        super()._draw_auto(vis)
        if not self.fresh or self.found is None or not self.result_resid:
            return
        h = self.result_grid[2] if self.result_grid else 20.0
        for g in self.found[0]:
            e = self.result_resid.get(guide_key(g))
            if e is not None:
                s = [self.to_scr(p) for p in g.pts]
                cv2.polylines(vis, [np.array(s, np.int32)], False, resid_col(e, h), 2, cv2.LINE_AA)
        if self.result_meta:
            m = self.result_meta
            self.msg += (f";  error {m['rms_before']:.2f} -> {m['rms_after']:.2f} px, "
                         f"max shift {m['max_shift']} px, strength {self.strength:.2f}")

    def _draw_result(self):
        pane = super()._draw_result()
        if not self.auto or self.result_grid is None or not self.fresh:
            return pane
        grid, p, _ = self.result_grid
        v = self.rview
        H, W = self.H, self.W
        n = grid.n
        for i in range(grid.cy + 1):                      # lines of constant page y, warped
            ys = min(i * grid.sy, H - 1)
            xs = np.linspace(0, W - 1, 60)
            q = grid.forward(p[:n], p[n:], np.stack([xs, np.full_like(xs, ys)], 1))
            s = np.array([v.to_scr(t) for t in q]) - [v.x0, 0]
            dashed(pane, s, GRID_COL, dash=4, gap=4, width=1)
        for j in range(grid.cx + 1):                      # lines of constant page x, warped
            xs0 = min(j * grid.sx, W - 1)
            ys = np.linspace(0, H - 1, 80)
            q = grid.forward(p[:n], p[n:], np.stack([np.full_like(ys, xs0), ys], 1))
            s = np.array([v.to_scr(t) for t in q]) - [v.x0, 0]
            dashed(pane, s, GRID_COL, dash=4, gap=4, width=1)
        return pane


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with clean pages (output of step 2), or a single page")
    ap.add_argument("--only", nargs="*", help="only these file stems, e.g. 144 148")
    ap.add_argument("--win", default="1500x950", help="window size, WIDTHxHEIGHT")
    args = ap.parse_args(argv)
    files = list_images(Path(args.src), args.only)
    if not files:
        sys.exit("no images found")
    win = tuple(int(v) for v in args.win.lower().split("x"))
    run(files, win, "grid_tool", GridEditor)
    print("hints saved next to the clean pages (*.grid.json). Run scanclean-grid for pages you did not save with s.")


if __name__ == "__main__":
    main()
