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
    Esc          cancel the item being marked (its points are dropped)
    q            save and quit

List of hints (top left, newest first; each line = type letter + first point)
    hover a line   highlights that hint on the page
    click its x    deletes that hint
    click the title, or press l, to fold / unfold the list; the wheel scrolls a long list

Hints are stored next to each photo as <name>.hints.json (photo pixel coordinates).
"""
import argparse
import json
import math
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
OPS = [("Horizontal", "h", "h"), ("Vertical", "v", "v"), ("Rectangle", "r", "r"), ("Ignore", "x", "x"),
       ("Crop", "c", "c"), ("Finish", "Enter", None), ("Cancel", "Esc", None), ("Undo", "u", None),
       ("Guides", "g", None), ("List", "l", None), ("Fit", "f", None), ("Zoom", "+/-", None),
       ("Clear", "0", None), ("Next", "n", None), ("Prev", "p", None), ("Quit", "q", None)]
MAC = sys.platform == "darwin"
BAR_H = 26                                 # status bar at the top of the window
PANEL_W, HEAD_H, ROW_H, DEL_W = 190, 24, 22, 26     # list of hints: width, title, row, x button


def wheel_delta(flags):
    """Scroll amount of a mouse-wheel event on Windows / Linux: a signed 16-bit number in the
    upper half of `flags`, +-120 per notch. (OpenCV 5 no longer offers cv2.getMouseWheelDelta.)"""
    d = (int(flags) >> 16) & 0xFFFF
    return d - 0x10000 if d >= 0x8000 else d


def load(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


class Editor:
    """All state and logic of the tool; the window loop below only forwards events."""

    list_open = True                       # list of hints unfolded? (kept from photo to photo)

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
        self.hover = None                  # index of the hint whose list line is under the mouse
        self.scroll = 0.0                  # first visible line of the list
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

    # ---- list of hints ----------------------------------------------------------
    def list_rows(self):
        """Visible lines of the list as (index into self.items, top y), newest hint first,
        plus the number of hints that do not fit."""
        room = max((self.win[1] - BAR_H - HEAD_H) // ROW_H, 2)
        n = len(self.items)
        if n > room:
            room -= 1                                        # last line says how many are hidden
        self.scroll = min(max(self.scroll, 0.0), float(max(n - room, 0)))
        first = int(round(self.scroll))
        order = list(range(n - 1, -1, -1))[first:first + room]
        return [(i, BAR_H + HEAD_H + r * ROW_H) for r, i in enumerate(order)], n - len(order)

    def list_hit(self, sx, sy):
        """What part of the list is at this window position:
        'title', ('row', i), ('del', i), 'more' (the "... more" line) or None (not on the list)."""
        if sx >= PANEL_W or sy < BAR_H:
            return None
        if sy < BAR_H + HEAD_H:
            return "title"
        if not Editor.list_open:
            return None
        rows, hidden = self.list_rows()
        for i, y in rows:
            if y <= sy < y + ROW_H:
                return ("del", i) if sx >= PANEL_W - DEL_W else ("row", i)
        end = BAR_H + HEAD_H + len(rows) * ROW_H
        return "more" if hidden and end <= sy < end + ROW_H else None

    @staticmethod
    def label(kind, pts):
        """Type letter + first point, e.g. 'H  1234, 567'."""
        return f"{kind.upper()}  {pts[0][0]:.0f}, {pts[0][1]:.0f}"

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
        elif ch in ("u", "\b", "\x7f"):
            self.undo()
        elif ch == "0" or k in (0x2E0000, 65535, 3014656):     # DEL on Windows / X11
            self.items, self.cur = [], []
        elif ch == "g":
            self.toggle_auto()
        elif ch == "l":
            Editor.list_open = not Editor.list_open
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
        elif (k & 0xFF) == 27:                   # Esc: drop the item being marked
            self.cur = []
        elif ch == "q":
            self.finish()
            return "quit"
        return None

    def on_mouse(self, event, x, y, flags, _=None):
        if event in (cv2.EVENT_MOUSEWHEEL, cv2.EVENT_MOUSEHWHEEL):
            if event == cv2.EVENT_MOUSEWHEEL:
                # macOS: x, y are the scroll amount (pixels), not the cursor position;
                # a trackpad sends many small steps. Elsewhere: +-120 per notch in `flags`.
                at = self.mouse if MAC else (x, y)
                amount = max(-60, min(60, y)) if MAC else max(-240, min(240, wheel_delta(flags)))
                if self.list_hit(*at) is not None:           # over the list: scroll it
                    self.scroll -= amount / ROW_H if MAC else amount / 120
                elif amount:
                    self.zoom(math.exp(0.006 * amount if MAC else 0.22 * amount / 120), at)
            return
        self.mouse = (x, y)
        hit = self.list_hit(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            if hit == "title":
                Editor.list_open = not Editor.list_open
            elif isinstance(hit, tuple) and hit[0] == "del":
                del self.items[hit[1]]
            elif hit is None:
                self.click(x, y)
        elif event in (cv2.EVENT_RBUTTONDOWN, cv2.EVENT_MBUTTONDOWN):
            if hit is None:
                self.drag = (x, y, self.ox, self.oy)
        elif event in (cv2.EVENT_RBUTTONUP, cv2.EVENT_MBUTTONUP):
            self.drag = None
        elif event == cv2.EVENT_MOUSEMOVE and self.drag:
            x0, y0, ox, oy = self.drag
            self.ox, self.oy = ox - (x - x0) / self.z, oy - (y - y0) / self.z

    # ---- drawing --------------------------------------------------------------
    def _shape(self, vis, kind, pts, col, closed_ok=True, width=2):
        s = [self.to_scr(p) for p in pts]
        if kind in "hv":
            if len(s) > 1:
                cv2.polylines(vis, [np.array(s, np.int32)], False, col, width, cv2.LINE_AA)
        elif kind == "r" and len(s) == 4 and closed_ok:
            c = np.mean(pts, 0)
            order = np.argsort([np.arctan2(p[1] - c[1], p[0] - c[0]) for p in pts])
            cv2.polylines(vis, [np.array([s[i] for i in order], np.int32)], True, col, width, cv2.LINE_AA)
        elif kind in "xc" and len(s) == 2:
            cv2.rectangle(vis, s[0], s[1], col, width, cv2.LINE_AA)
            if kind == "x":
                cv2.line(vis, s[0], s[1], col, max(width - 1, 1), cv2.LINE_AA)
        for q in s:
            cv2.circle(vis, q, width + 2, col, -1, cv2.LINE_AA)
        if s and kind in "hvrxc":
            cv2.putText(vis, kind.upper(), (s[0][0] + 6, s[0][1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1,
                        cv2.LINE_AA)

    def _draw_list(self, vis, hit):
        x0, w, font = 0, PANEL_W, cv2.FONT_HERSHEY_SIMPLEX     # left and right edge of the list
        rows, hidden = self.list_rows() if Editor.list_open else ([], 0)
        end = BAR_H + HEAD_H + (len(rows) + bool(hidden)) * ROW_H
        box = vis[BAR_H:end, x0:w]
        box[:] = (0.22 * box + 0.78 * np.array((38, 38, 38))).astype(np.uint8)      # see-through dark
        # title with a fold mark: triangle pointing down (open) or right (folded)
        cv2.rectangle(vis, (x0, BAR_H), (w - 1, BAR_H + HEAD_H - 1), (70, 70, 70) if hit == "title" else (55, 55, 55), -1)
        cx, cy = x0 + 13, BAR_H + HEAD_H // 2
        tri = [(cx - 5, cy - 3), (cx + 5, cy - 3), (cx, cy + 4)] if Editor.list_open else \
              [(cx - 3, cy - 5), (cx - 3, cy + 5), (cx + 4, cy)]
        cv2.fillPoly(vis, [np.array(tri, np.int32)], (230, 230, 230), cv2.LINE_AA)
        cv2.putText(vis, f"Hints ({len(self.items)})", (x0 + 26, BAR_H + 17), font, 0.48, (240, 240, 240), 1, cv2.LINE_AA)
        for i, y in rows:
            kind, pts = self.items[i]
            if i == self.hover:
                cv2.rectangle(vis, (x0, y), (w - 1, y + ROW_H - 1), (90, 90, 90), -1)
            col = tuple(min(int(c) + 60, 255) for c in COL[kind])                   # readable on dark
            text = self.label(kind, pts)
            cv2.putText(vis, text[0], (x0 + 9, y + 16), font, 0.5, col, 2, cv2.LINE_AA)
            cv2.putText(vis, text[1:], (x0 + 20, y + 16), font, 0.45, (235, 235, 235), 1, cv2.LINE_AA)
            on_x = hit == ("del", i)                                                # the x button
            bx, by = w - DEL_W + 5, y + 4
            cv2.rectangle(vis, (bx, by), (bx + 14, by + 14), (60, 60, 230) if on_x else (75, 75, 75), -1)
            xc = (255, 255, 255) if on_x else (130, 130, 255)
            cv2.line(vis, (bx + 4, by + 4), (bx + 10, by + 10), xc, 2, cv2.LINE_AA)
            cv2.line(vis, (bx + 10, by + 4), (bx + 4, by + 10), xc, 2, cv2.LINE_AA)
        if hidden:
            cv2.putText(vis, f"... {hidden} more (scroll)", (x0 + 9, end - 7), font, 0.42, (170, 170, 170), 1, cv2.LINE_AA)

    def render(self):
        w, h = self.win
        src, k = (self.img, 1.0) if self.z > 0.3 else (self.small, 0.25)
        M = np.array([[self.z / k, 0, -self.ox * self.z], [0, self.z / k, -self.oy * self.z]], dtype=np.float32)
        vis = cv2.warpAffine(src, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=(60, 60, 60))
        if self.auto:
            for kind, pts in self.auto:
                s = np.array([self.to_scr(p) for p in pts], np.int32)
                cv2.polylines(vis, [s], False, COL[kind], 1, cv2.LINE_AA)
        hit = self.list_hit(*self.mouse)
        self.hover = hit[1] if isinstance(hit, tuple) else None
        for i, (kind, pts) in enumerate(self.items):
            if i != self.hover:
                self._shape(vis, kind, pts, COL[kind])
        if self.hover is not None:                           # highlighted hint: on top, with a halo
            kind, pts = self.items[self.hover]
            self._shape(vis, kind, pts, (255, 255, 255), width=8)
            self._shape(vis, kind, pts, COL[kind], width=4)
        if self.cur:
            self._shape(vis, self.mode, self.cur + [list(self.to_img(*self.mouse))], COL["cur"], closed_ok=False)
        self._draw_bar(vis)
        self._draw_list(vis, hit)
        self._draw_status(vis)
        return vis

    def _draw_bar(self, vis):
        """Top bar: every operation as 'Word key', key in bold green, current mode highlighted."""
        w, font, fs = self.win[0], cv2.FONT_HERSHEY_SIMPLEX, 0.45
        cv2.rectangle(vis, (0, 0), (w, BAR_H), (30, 30, 30), -1)
        size = [(cv2.getTextSize(word + " ", font, fs, 1)[0][0], cv2.getTextSize(key, font, fs, 2)[0][0])
                for word, key, _ in OPS]
        used = sum(a + b for a, b in size)
        gap = max(8, min(28, (w - 16 - used) // max(len(OPS) - 1, 1)))      # spread over the bar
        x, base = 8, BAR_H // 2 + 5
        for (word, key, mode), (ww, kw) in zip(OPS, size):
            if mode is not None and mode == self.mode:                     # active operation
                cv2.rectangle(vis, (x - 5, 2), (x + ww + kw + 5, BAR_H - 3), (85, 85, 85), -1)
                cv2.line(vis, (x - 5, BAR_H - 3), (x + ww + kw + 5, BAR_H - 3), COL[mode], 2)
            cv2.putText(vis, word, (x, base), font, fs, (235, 235, 235), 1, cv2.LINE_AA)
            cv2.putText(vis, key, (x + ww, base), font, fs, (60, 220, 60), 2, cv2.LINE_AA)
            x += ww + kw + gap

    def _draw_status(self, vis):
        """File name and the latest message, bottom left."""
        txt = self.path.name + (f"   {self.msg}" if self.msg else "")
        font, fs = cv2.FONT_HERSHEY_SIMPLEX, 0.45
        (tw, th), _ = cv2.getTextSize(txt, font, fs, 1)
        h = self.win[1]
        cv2.rectangle(vis, (0, h - th - 12), (tw + 16, h), (30, 30, 30), -1)
        cv2.putText(vis, txt, (8, h - 7), font, fs, (235, 235, 235), 1, cv2.LINE_AA)


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
