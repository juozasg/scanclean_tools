#!/usr/bin/env python3
"""
hint_tool.py - mark geometric hints on page photos for step1_dewarp.py.

  python hint_tool.py PHOTO_DIR                 # step through all photos
  python hint_tool.py PHOTO_DIR --only 157 164  # only the pages scanclean flagged

What you can mark (press the key, then click on the photo):
  h   line that is HORIZONTAL on the real page (text baseline, table rule, figure frame ...)
  v   line that is VERTICAL on the real page   (column rule, text margin, figure frame ...)
      click 2 or more points ALONG the line - follow its curve if the page is bent -
      then press ENTER (or SPACE) to finish the line.
  r   rectangle: click the 4 corners of a printed rectangle (table / figure frame), any order
  x   ignore box: 2 clicks; automatic guides inside it are thrown away (messy figure)
  c   crop box:   2 clicks; the output page is cropped to this area

Other keys
  g            show / hide the guides step 1 finds automatically (takes a few seconds)
  wheel, + -   zoom at the mouse position        right-drag   pan        f   fit to window
  u / Backspace  undo last point, or last finished item
  DEL (or 0)   remove all hints of this photo
  n / p        next / previous photo (hints are saved automatically)
  q / Esc      save and quit

Hints are stored next to each photo as <name>.hints.json (photo pixel coordinates).
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
COL = {"h": (40, 40, 255), "v": (255, 110, 0), "r": (0, 150, 255), "x": (120, 120, 120),
       "c": (0, 170, 0), "cur": (0, 220, 255)}
NAME = {"h": "HORIZONTAL line", "v": "VERTICAL line", "r": "RECTANGLE (4 corners)",
        "x": "IGNORE box (2 clicks)", "c": "CROP box (2 clicks)"}
NEED = {"r": 4, "x": 2, "c": 2}


def load(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


class Editor:
    """All state and logic of the tool; the window loop below only forwards events."""

    def __init__(self, path, win=(1500, 950)):
        self.path = Path(path)
        self.img = load(self.path)
        self.small = cv2.resize(self.img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        self.H, self.W = self.img.shape[:2]
        self.win = win
        self.mode = "h"
        self.cur = []                      # points of the item being drawn
        self.items = []                    # finished items: (kind, [[x, y], ...])
        self.auto = None                   # automatic guides (list of (kind, pts)) when shown
        self.mouse = (0, 0)
        self.drag = None
        self.msg = ""
        self.fit()
        self.read()

    # ---- file -----------------------------------------------------------------
    @property
    def hint_path(self):
        return self.path.with_suffix(".hints.json")

    def read(self):
        if not self.hint_path.exists():
            return
        d = json.loads(self.hint_path.read_text(encoding="utf-8"))
        for ln in d.get("lines", []):
            self.items.append((ln["type"], [list(map(float, p)) for p in ln["pts"]]))
        for r in d.get("rects", []):
            self.items.append(("r", [list(map(float, p)) for p in r]))
        for b in d.get("ignore", []):
            self.items.append(("x", [[b[0], b[1]], [b[2], b[3]]]))
        if d.get("crop"):
            self.items.append(("c", [list(map(float, p)) for p in d["crop"]]))

    def write(self):
        d = {"image": self.path.name, "size": [self.W, self.H], "lines": [], "rects": [], "ignore": []}
        for kind, pts in self.items:
            pts = [[round(x, 1), round(y, 1)] for x, y in pts]
            if kind in "hv":
                d["lines"].append({"type": kind, "pts": pts})
            elif kind == "r":
                d["rects"].append(pts)
            elif kind == "x":
                d["ignore"].append([pts[0][0], pts[0][1], pts[1][0], pts[1][1]])
            elif kind == "c":
                d["crop"] = pts
        if self.items:
            self.hint_path.write_text(json.dumps(d, indent=1), encoding="utf-8")
        elif self.hint_path.exists():
            self.hint_path.unlink()

    # ---- view -----------------------------------------------------------------
    def fit(self):
        self.z = min(self.win[0] / self.W, (self.win[1] - 30) / self.H)
        self.ox = -(self.win[0] / self.z - self.W) / 2
        self.oy = -30 / self.z

    def to_img(self, sx, sy):
        return sx / self.z + self.ox, sy / self.z + self.oy

    def to_scr(self, p):
        return int(round((p[0] - self.ox) * self.z)), int(round((p[1] - self.oy) * self.z))

    def zoom(self, factor, at=None):
        sx, sy = at or self.mouse
        ix, iy = self.to_img(sx, sy)
        self.z = float(np.clip(self.z * factor, 0.05, 8.0))
        self.ox, self.oy = ix - sx / self.z, iy - sy / self.z

    # ---- editing --------------------------------------------------------------
    def click(self, sx, sy):
        x, y = self.to_img(sx, sy)
        if not (0 <= x < self.W and 0 <= y < self.H):
            return
        self.cur.append([x, y])
        if self.mode in NEED and len(self.cur) == NEED[self.mode]:
            self.finish()

    def finish(self):
        if self.mode in "hv" and len(self.cur) >= 2:
            self.items.append((self.mode, self.cur))
        elif self.mode in NEED and len(self.cur) == NEED[self.mode]:
            if self.mode == "c":
                self.items = [it for it in self.items if it[0] != "c"]   # one crop only
            self.items.append((self.mode, self.cur))
        self.cur = []

    def undo(self):
        if self.cur:
            self.cur.pop()
        elif self.items:
            self.items.pop()

    def toggle_auto(self):
        if self.auto is not None:
            self.auto = None
            return
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from scanlib.guides import find_guides
            guides, _ = find_guides(self.img)
            self.auto = [(g.kind, g.pts) for g in guides]
            self.msg = f"{len(self.auto)} automatic guides (thin lines)"
        except Exception as e:             # the tool must keep working without it
            self.auto = None
            self.msg = f"automatic guides unavailable: {e}"

    def key(self, k):
        """Returns 'next', 'prev', 'quit' or None."""
        ch = chr(k & 0xFF).lower() if 0 <= (k & 0xFF) < 128 else ""
        if ch in ("h", "v", "r", "x", "c"):
            self.finish()
            self.cur, self.mode = [], ch
        elif ch in ("\r", "\n", " "):
            self.finish()
        elif ch in ("u", "\b"):
            self.undo()
        elif ch == "0" or k in (0x2E0000, 65535, 3014656):     # DEL on Windows / X11
            self.items, self.cur = [], []
        elif ch == "g":
            self.toggle_auto()
        elif ch == "f":
            self.fit()
        elif ch in ("+", "="):
            self.zoom(1.3)
        elif ch in ("-", "_"):
            self.zoom(1 / 1.3)
        elif ch == "n":
            self.finish()
            return "next"
        elif ch == "p":
            self.finish()
            return "prev"
        elif ch == "q" or (k & 0xFF) == 27:
            self.finish()
            return "quit"
        return None

    def on_mouse(self, event, x, y, flags, _=None):
        self.mouse = (x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.click(x, y)
        elif event in (cv2.EVENT_RBUTTONDOWN, cv2.EVENT_MBUTTONDOWN):
            self.drag = (x, y, self.ox, self.oy)
        elif event in (cv2.EVENT_RBUTTONUP, cv2.EVENT_MBUTTONUP):
            self.drag = None
        elif event == cv2.EVENT_MOUSEMOVE and self.drag:
            x0, y0, ox, oy = self.drag
            self.ox, self.oy = ox - (x - x0) / self.z, oy - (y - y0) / self.z
        elif event == cv2.EVENT_MOUSEWHEEL:
            self.zoom(1.25 if cv2.getMouseWheelDelta(flags) > 0 else 0.8, (x, y))

    # ---- drawing --------------------------------------------------------------
    def _shape(self, vis, kind, pts, col, closed_ok=True):
        s = [self.to_scr(p) for p in pts]
        if kind in "hv":
            if len(s) > 1:
                cv2.polylines(vis, [np.array(s, np.int32)], False, col, 2, cv2.LINE_AA)
        elif kind == "r" and len(s) == 4 and closed_ok:
            c = np.mean(pts, 0)
            order = np.argsort([np.arctan2(p[1] - c[1], p[0] - c[0]) for p in pts])
            cv2.polylines(vis, [np.array([s[i] for i in order], np.int32)], True, col, 2, cv2.LINE_AA)
        elif kind in "xc" and len(s) == 2:
            cv2.rectangle(vis, s[0], s[1], col, 2, cv2.LINE_AA)
            if kind == "x":
                cv2.line(vis, s[0], s[1], col, 1, cv2.LINE_AA)
        for q in s:
            cv2.circle(vis, q, 4, col, -1, cv2.LINE_AA)
        if s and kind in "hvrxc":
            cv2.putText(vis, kind.upper(), (s[0][0] + 6, s[0][1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1,
                        cv2.LINE_AA)

    def render(self):
        w, h = self.win
        src, k = (self.img, 1.0) if self.z > 0.3 else (self.small, 0.25)
        M = np.float32([[self.z / k, 0, -self.ox * self.z], [0, self.z / k, -self.oy * self.z]])
        vis = cv2.warpAffine(src, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(60, 60, 60))
        if self.auto:
            for kind, pts in self.auto:
                s = np.array([self.to_scr(p) for p in pts], np.int32)
                cv2.polylines(vis, [s], False, COL[kind], 1, cv2.LINE_AA)
        for kind, pts in self.items:
            self._shape(vis, kind, pts, COL[kind])
        if self.cur:
            self._shape(vis, self.mode, self.cur + [list(self.to_img(*self.mouse))], COL["cur"], closed_ok=False)
        cv2.rectangle(vis, (0, 0), (w, 26), (30, 30, 30), -1)
        n = {k: sum(it[0] == k for it in self.items) for k in "hvrxc"}
        txt = (f"{self.path.name}   mode: {NAME[self.mode]}   | hints: {n['h']} H, {n['v']} V, {n['r']} rect, "
               f"{n['x']} ignore, {n['c']} crop |  h v r x c  ENTER=finish line  u=undo  g=auto guides  "
               f"n/p=next/prev  q=quit   {self.msg}")
        cv2.putText(vis, txt, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
        return vis


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with photos, or a single photo")
    ap.add_argument("--only", nargs="*", help="only these file stems, e.g. 157 164")
    ap.add_argument("--win", default="1500x950", help="window size, WIDTHxHEIGHT")
    args = ap.parse_args(argv)
    src = Path(args.src)
    files = [src] if src.is_file() else sorted(p for p in src.iterdir() if p.suffix.lower() in IMG_EXT)
    if args.only:
        files = [p for p in files if p.stem in set(args.only)]
    if not files:
        sys.exit("no images found")
    win = tuple(int(v) for v in args.win.lower().split("x"))

    title = "hint_tool"
    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    i = 0
    while 0 <= i < len(files):
        ed = Editor(files[i], win)
        cv2.setMouseCallback(title, ed.on_mouse)
        act = None
        while act is None:
            cv2.imshow(title, ed.render())
            k = cv2.waitKeyEx(30)
            if k != -1:
                act = ed.key(k)
            if cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                act = "quit"
        ed.write()
        if act == "quit":
            break
        i += 1 if act == "next" else -1
        i = max(i, 0)
    cv2.destroyAllWindows()
    print("hints saved next to the photos (*.hints.json). Now re-run step 1 (or run_all.sh) for these pages.")


if __name__ == "__main__":
    main()
