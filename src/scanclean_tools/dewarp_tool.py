"""
dewarp-tool - mark geometric hints on page photos for step 1 (scanclean-dewarp).

    dewarp-tool PHOTO_DIR                 # step through all photos
    dewarp-tool PHOTO_DIR --only 157 164  # only the pages step 1 flagged

Two panes: the photo with your hints on the left, the dewarped page (step 1 run with the
current hints) on the right. The right pane follows every change of the hints after a moment.

What you can mark (press the key, then click on the photo):
    h   line that is HORIZONTAL on the real page (text baseline, table rule, figure frame ...)
    v   line that is VERTICAL on the real page   (column rule, text margin, figure frame ...)
        click 2 or more points ALONG the line - follow its curve if the page is bent -
        then press ENTER (or SPACE), or double-click the last point, to finish the line.
    r   rectangle: click two opposite corners of a printed rectangle (table / figure frame);
        the second click finishes it
    x   ignore box: 2 clicks; automatic guides inside it are thrown away (messy figure)
    c   crop: click the 4 corners of the page, any order; the page is cut to this quad
    e   edit: click a point of any hint to pick it up, move the mouse, click again to place it
        (Esc puts it back where it was)

Other keys
    g            show / hide the guides step 1 finds automatically: thin solid = used,
                 dark dashed = removed by an ignore box, magenta dashed = dropped by the fit
                 because the page model could not straighten it (a mis-detection)
    wheel, + -   zoom at the mouse position        right-drag   pan        f   fit to the pane
                 (each pane has its own zoom and pan; these act on the pane under the mouse)
    s            save the dewarped page exactly as step 1 would, in flat/ next to PHOTO_DIR:
                 flat/<name>.jpg plus flat/meta/<name>.json and flat/meta/<name>.mask.png (the mask
                 is the crop quad when one is marked), all overwritten; the hints are saved too
    u / Backspace  undo last point, or last finished item
    DEL (or 0)   remove all hints of this photo
    n / p        next / previous photo (hints are saved automatically); also Right / Left arrow
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
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from .scanlib.dewarp import dewarp_page, status
from .scanlib.guides import Guide, find_guides
from .scanlib.hints import apply_hints, crop_points, order_quad
from .scanlib.util import imwrite, load_photo

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
COL = {"h": (40, 40, 255), "v": (255, 110, 0), "r": (0, 150, 255), "x": (120, 120, 120),
       "c": (0, 170, 0), "e": (255, 255, 255), "cur": (0, 220, 255)}
NAME = {"h": "HORIZONTAL line", "v": "VERTICAL line", "r": "RECTANGLE (2 corners)",
        "x": "IGNORE box (2 clicks)", "c": "CROP quad (4 corners)", "e": "EDIT (move points)"}
NEED = {"r": 2, "x": 2, "c": 4}
DOUBLE_S, DOUBLE_PX = 0.4, 6       # a second click this soon and this close is a double click
ARROW_LEFT = (0x250000, 65361, 63234, 2424832)      # Windows, Linux (GTK / Qt), macOS
ARROW_RIGHT = (0x270000, 65363, 63235, 2555904)
PICK_PX = 10                       # edit mode: how close (window px) a click must be to a point
EDIT_COL = (255, 255, 255)
OPS = [("Horizontal", "h", "h"), ("Vertical", "v", "v"), ("Rectangle", "r", "r"), ("Ignore", "x", "x"),
       ("Crop", "c", "c"), ("Edit", "e", "e"), ("Finish", "Enter", None), ("Cancel", "Esc", None), ("Undo", "u", None),
       ("Guides", "g", None), ("List", "l", None), ("Fit", "f", None), ("Zoom", "+/-", None),
       ("Clear", "0", None), ("Save", "s", None), ("Next", "n/->", None), ("Prev", "p/<-", None),
       ("Quit", "q", None)]
MAC = sys.platform == "darwin"
BAR_H = 26                                 # status bar at the top of the window
IGNORED_COL, UNFIT_COL = (40, 40, 40), (255, 0, 255)    # guides dropped by an ignore box / by the fit


def guide_key(g):
    """Identifies an automatic guide after an ignore box may have trimmed some of its points."""
    return g.kind, len(g.pts), tuple(np.round(g.pts[0], 1)), tuple(np.round(g.pts[-1], 1))


def dashed(vis, pts, col, dash=7, gap=5, width=2):
    """Dashed polyline through window points."""
    pts = np.asarray(pts, np.float64)
    seg = np.diff(pts, axis=0)
    ln = np.hypot(seg[:, 0], seg[:, 1])
    pos, on = 0.0, True
    for a, d, n in zip(pts[:-1], seg, ln):
        t = 0.0
        while t < n:
            step = min((dash if on else gap) - pos, n - t)
            if on:
                p, q = a + d * t / n, a + d * (t + step) / n
                cv2.line(vis, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])), col, width, cv2.LINE_AA)
            t += step
            pos += step
            if pos >= (dash if on else gap):
                pos, on = 0.0, not on
PANEL_W, HEAD_H, ROW_H, DEL_W = 190, 24, 22, 26     # list of hints: width, title, row, x button


def wheel_delta(flags):
    """Scroll amount of a mouse-wheel event on Windows / Linux: a signed 16-bit number in the
    upper half of `flags`, +-120 per notch. (OpenCV 5 no longer offers cv2.getMouseWheelDelta.)"""
    d = (int(flags) >> 16) & 0xFFFF
    return d - 0x10000 if d >= 0x8000 else d


class View:
    """Zoom and pan of one image pane. The pane is `w` window pixels wide and starts at window
    x = `x0`; it is as high as the window (the top bar covers its first BAR_H rows)."""

    def __init__(self, x0, w, h):
        self.x0, self.w, self.h = x0, w, h
        self.z, self.ox, self.oy = 1.0, 0.0, 0.0       # zoom, image point shown at the pane's top left

    def fit(self, W, H):
        """Show a whole W x H image."""
        self.z = min(self.w / W, (self.h - BAR_H - 4) / H)
        self.ox = -(self.w / self.z - W) / 2
        self.oy = -(BAR_H + 2) / self.z

    def to_img(self, sx, sy):
        """Window position -> image point."""
        return (sx - self.x0) / self.z + self.ox, sy / self.z + self.oy

    def to_scr(self, p):
        """Image point -> window position."""
        return int(round((p[0] - self.ox) * self.z)) + self.x0, int(round((p[1] - self.oy) * self.z))

    def zoom(self, factor, at):
        """Zoom so that the image point at window position `at` stays where it is."""
        ix, iy = self.to_img(*at)
        self.z = float(np.clip(self.z * factor, 0.05, 8.0))
        self.ox, self.oy = ix - (at[0] - self.x0) / self.z, iy - at[1] / self.z

    def draw(self, img, small):
        """The pane as an image; `small` is a quarter-size copy of `img` for zoomed-out views."""
        src, k = (img, 1.0) if self.z > 0.3 else (small, 0.25)
        M = np.array([[self.z / k, 0, -self.ox * self.z], [0, self.z / k, -self.oy * self.z]], dtype=np.float32)
        return cv2.warpAffine(src, M, (self.w, self.h), flags=cv2.INTER_LINEAR, borderValue=(60, 60, 60))


class Editor:
    """All state and logic of the tool; the window loop below only forwards events."""

    list_open = True                       # list of hints unfolded? (kept from photo to photo)

    def __init__(self, path, win=(1500, 950)):
        self.path = Path(path)
        self.img, f35 = load_photo(self.path)
        self.f35 = f35 or 26.0             # same focal length as step 1 uses
        self.small = cv2.resize(self.img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        self.H, self.W = self.img.shape[:2]
        self.win = win
        pw = win[0] // 2
        self.view = View(0, pw, win[1])                    # left pane: the photo with the hints
        self.rview = View(pw, win[0] - pw, win[1])         # right pane: the dewarped page
        self.mode = "h"
        self.cur = []                      # points of the item being drawn
        self.items = []                    # finished items: (kind, [[x, y], ...])
        self.auto = None                   # True while the automatic guides are shown
        self.mouse = (0, 0)
        self.last_click = (0.0, -99, -99)  # time and window position of the previous click
        self.grab = None                   # edit mode: (item index, point index, original point)
        self.drag = None
        self.hover = None                  # index of the hint whose list line is under the mouse
        self.scroll = 0.0                  # first visible line of the list
        self.msg = ""
        # dewarped page, computed in a background thread
        self.found = None                  # result of find_guides for this photo (hints do not change it)
        self.job = None                    # the running thread
        self.done = None                   # its result: (hints key, image or None, error or None)
        self.result = None                 # dewarped page shown on the right ...
        self.result_small = None
        self.result_mask = None            # ... its page mask and metadata, as step 1 makes them
        self.result_used = None            # keys of the automatic guides its fit kept
        self.result_meta = None
        self.result_key = None             # ... and the hints it was made with
        self.error = None
        self.fresh = False                 # does it match the current hints?
        self.want_save = False             # s was pressed: save as soon as it does
        self.saved_key = None
        self.view.fit(self.W, self.H)
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
        crop = crop_points(d)
        if crop is not None:
            self.items.append(("c", crop.tolist()))

    def hints(self):
        """The finished hints, as they are stored in <name>.hints.json."""
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
        return d

    def write(self):
        if self.items:
            self.hint_path.write_text(json.dumps(self.hints(), indent=1), encoding="utf-8")
        elif self.hint_path.exists():
            self.hint_path.unlink()

    # ---- dewarped page ----------------------------------------------------------
    def _dewarp(self, key, hints):
        """Step 1 for this photo with `hints`; runs in a background thread."""
        try:
            if self.found is None:
                self.found = find_guides(self.img)
            res = dewarp_page(self.img, hints, self.f35, found=self.found)
            res["used"] = {guide_key(g) for g in res["guides"] if g.src != "hint"}
            self.done = (key, res, None)
        except Exception as e:             # the tool must keep working
            self.done = (key, None, str(e))

    def poll(self):
        """Keep the dewarped page in step with the hints; called for every frame."""
        hints = self.hints()
        key = json.dumps(hints, sort_keys=True)
        if self.done is not None:
            (self.result_key, res, self.error), self.done = self.done, None
            if res is not None:
                first = self.result is None
                image = self.result = res["image"]
                self.result_mask, self.result_meta = res["mask"], res["meta"]
                self.result_used = res["used"]
                self.result_small = cv2.resize(image, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
                if first:
                    self.rview.fit(image.shape[1], image.shape[0])
        if key != self.result_key and self.grab is None and not (self.job is not None and self.job.is_alive()):
            self.job = threading.Thread(target=self._dewarp, args=(key, hints), daemon=True)
            self.job.start()
        self.fresh = key == self.result_key
        if self.want_save and self.fresh:
            self.save_flat()

    def wait(self):
        """Block until the dewarped page matches the current hints."""
        self.poll()
        while not self.fresh:
            if self.job is not None:
                self.job.join()
            self.poll()

    def save_flat(self):
        """Write the dewarped page the way step 1 does, in flat/ next to the photo folder -
        flat/<name>.jpg (or over an existing .png), flat/meta/<name>.mask.png and
        flat/meta/<name>.json - and save the hints it was made with next to the photo."""
        self.want_save = False
        if self.error or self.result is None or self.result_mask is None or self.result_meta is None:
            self.msg = f"not saved, dewarp failed: {self.error}"
            return
        flat = self.path.resolve().parent.parent / "flat"
        flat.mkdir(exist_ok=True)
        png = flat / (self.path.stem + ".png")
        dst = png if png.exists() else flat / (self.path.stem + ".jpg")
        imwrite(dst, self.result, [cv2.IMWRITE_PNG_COMPRESSION, 3] if dst == png else [cv2.IMWRITE_JPEG_QUALITY, 95])
        (flat / "meta").mkdir(exist_ok=True)
        meta = dict(self.result_meta, page=self.path.stem, source=self.path.name)
        meta["status"] = status(meta)
        imwrite(flat / "meta" / (self.path.stem + ".mask.png"), self.result_mask)
        (flat / "meta" / (self.path.stem + ".json")).write_text(json.dumps(meta, indent=1), encoding="utf-8")
        self.write()
        self.saved_key = self.result_key
        self.msg = f"saved {flat.name}/{dst.name} + meta"

    # ---- view -----------------------------------------------------------------
    def pane(self, sx):
        """The view of the pane at window x."""
        return self.rview if sx >= self.rview.x0 else self.view

    def fit(self, view=None):
        """Fit the image of a pane (default: the pane under the mouse) to that pane."""
        view = view or self.pane(self.mouse[0])
        if view is self.view:
            view.fit(self.W, self.H)
        elif self.result is not None:
            view.fit(self.result.shape[1], self.result.shape[0])

    def to_img(self, sx, sy):
        """Window position -> photo pixel (left pane)."""
        return self.view.to_img(sx, sy)

    def to_scr(self, p):
        """Photo pixel -> window position (left pane)."""
        return self.view.to_scr(p)

    def zoom(self, factor, at=None):
        """Zoom the pane at window position `at` (default: under the mouse)."""
        at = at or self.mouse
        self.pane(at[0]).zoom(factor, at)

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
    def point_at(self, sx, sy):
        """(item index, point index) of the hint point nearest to a window position, or None."""
        best, pick = PICK_PX ** 2 + 1, None
        for i, (_, pts) in enumerate(self.items):
            for j, p in enumerate(pts):
                qx, qy = self.to_scr(p)
                d = (qx - sx) ** 2 + (qy - sy) ** 2
                if d < best:
                    best, pick = d, (i, j)
        return pick

    def drop_grab(self, restore=True):
        """Stop moving a point; put it back where it was unless restore is False."""
        if self.grab is not None and restore:
            i, j, orig = self.grab
            self.items[i][1][j] = orig
        self.grab = None

    def click(self, sx, sy):
        if self.mode == "e":                                # edit: pick up / place a point
            if self.pane(sx) is not self.view:
                return
            if self.grab is not None:
                x, y = self.to_img(sx, sy)
                i, j, _ = self.grab
                self.items[i][1][j] = [float(np.clip(x, 0, self.W - 1)), float(np.clip(y, 0, self.H - 1))]
                self.drop_grab(restore=False)
            else:
                hit = self.point_at(sx, sy)
                if hit is not None:
                    i, j = hit
                    self.grab = (i, j, list(self.items[i][1][j]))
            return
        x, y = self.to_img(sx, sy)
        if self.pane(sx) is not self.view or not (0 <= x < self.W and 0 <= y < self.H):
            return                         # marks go on the photo only
        now, (t0, x0, y0) = time.time(), self.last_click
        self.last_click = (now, sx, sy)
        if (self.mode in "hv" and self.cur and now - t0 < DOUBLE_S
                and abs(sx - x0) <= DOUBLE_PX and abs(sy - y0) <= DOUBLE_PX):
            self.last_click = (0.0, -99, -99)
            if len(self.cur) >= 2:         # double click: like Enter; the first click set the point
                self.finish()
            return                         # on the first point it only keeps that one point
        self.cur.append([x, y])
        if self.mode in NEED and len(self.cur) == NEED[self.mode]:
            self.finish()

    def finish(self):
        if self.mode in "hv" and len(self.cur) >= 2:
            self.items.append((self.mode, self.cur))
        elif self.mode in NEED and len(self.cur) == NEED[self.mode]:
            if self.mode == "c":
                self.items = [it for it in self.items if it[0] != "c"]   # one crop only
                self.cur = order_quad(self.cur).tolist()                 # TL, TR, BR, BL
            elif self.mode == "r":                                       # 2 opposite corners -> 4
                (xa, ya), (xb, yb) = self.cur
                self.cur = order_quad([[xa, ya], [xb, ya], [xb, yb], [xa, yb]]).tolist()
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
            self.msg = ""
            return
        try:
            if self.found is None:
                self.found = find_guides(self.img)
            self.auto = True
        except Exception as e:             # the tool must keep working without it
            self.auto = None
            self.msg = f"automatic guides unavailable: {e}"

    def key(self, k):
        """Returns 'next', 'prev', 'quit' or None."""
        if k in ARROW_LEFT:
            k = ord("p")
        elif k in ARROW_RIGHT:
            k = ord("n")
        elif k > 0xFF and (k & 0xFF) not in (8, 127):  # other special keys: their low byte is not a letter
            k = 0 if k not in (0x2E0000, 65535, 3014656) else k
        ch = chr(k & 0xFF).lower() if 0 <= (k & 0xFF) < 128 else ""
        if ch in ("h", "v", "r", "x", "c", "e"):
            self.drop_grab()
            self.finish()
            self.cur, self.mode = [], ch
        elif ch in ("\r", "\n", " "):
            self.finish()
        elif ch in ("u", "\b", "\x7f"):
            if self.grab is not None:
                self.drop_grab()
            else:
                self.undo()
        elif ch == "0" or k in (0x2E0000, 65535, 3014656):     # DEL on Windows / X11
            self.grab = None
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
        elif ch == "s":                          # save the dewarped page once it is up to date
            self.drop_grab()
            self.finish()
            self.want_save = True
            self.msg = "saving ..."
            self.poll()
        elif (k & 0xFF) == 27:                   # Esc: drop the item being marked / put the point back
            self.drop_grab()
            self.cur = []
        elif ch in ("n", "p", "q"):
            self.drop_grab()
            self.finish()
            if self.want_save:                   # a save is still waiting for the dewarp
                self.wait()
            return {"n": "next", "p": "prev", "q": "quit"}[ch]
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
                elif amount:                                 # zoom the pane under the mouse
                    self.zoom(math.exp(0.006 * amount if MAC else 0.22 * amount / 120), at)
            return
        self.mouse = (x, y)
        if self.grab is not None and event == cv2.EVENT_MOUSEMOVE and not self.drag:
            i, j, _ = self.grab
            self.items[i][1][j] = list(self.to_img(x, y))
        hit = self.list_hit(x, y)
        if event in (cv2.EVENT_LBUTTONDOWN, cv2.EVENT_LBUTTONDBLCLK):   # Windows sends the 2nd click as DBLCLK
            if hit == "title":
                Editor.list_open = not Editor.list_open
            elif isinstance(hit, tuple) and hit[0] == "del":
                self.drop_grab()
                del self.items[hit[1]]
            elif hit is None:
                self.click(x, y)
        elif event in (cv2.EVENT_RBUTTONDOWN, cv2.EVENT_MBUTTONDOWN):
            if hit is None:                                  # pan the pane the drag starts in
                view = self.pane(x)
                self.drag = (view, x, y, view.ox, view.oy)
        elif event in (cv2.EVENT_RBUTTONUP, cv2.EVENT_MBUTTONUP):
            self.drag = None
        elif event == cv2.EVENT_MOUSEMOVE and self.drag:
            view, x0, y0, ox, oy = self.drag
            view.ox, view.oy = ox - (x - x0) / view.z, oy - (y - y0) / view.z

    # ---- drawing --------------------------------------------------------------
    def _shape(self, vis, kind, pts, col, closed_ok=True, width=2):
        s = [self.to_scr(p) for p in pts]
        if kind in "hv":
            if len(s) > 1:
                cv2.polylines(vis, [np.array(s, np.int32)], False, col, width, cv2.LINE_AA)
        elif kind in "rc" and len(s) == 4 and closed_ok:
            c = np.mean(pts, 0)
            order = np.argsort([np.arctan2(p[1] - c[1], p[0] - c[0]) for p in pts])
            cv2.polylines(vis, [np.array([s[i] for i in order], np.int32)], True, col, width, cv2.LINE_AA)
        elif kind == "r" and len(s) == 2:                    # rectangle still being marked
            cv2.rectangle(vis, s[0], s[1], col, width, cv2.LINE_AA)
        elif kind == "c" and len(s) > 1:                     # crop quad still being marked
            cv2.polylines(vis, [np.array(s, np.int32)], False, col, width, cv2.LINE_AA)
        elif kind == "x" and len(s) == 2:
            cv2.rectangle(vis, s[0], s[1], col, width, cv2.LINE_AA)
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
        self.poll()
        vis = self.view.draw(self.img, self.small)           # left pane; the right one is added below
        if self.auto:
            self._draw_auto(vis)
        hit = self.list_hit(*self.mouse)
        self.hover = hit[1] if isinstance(hit, tuple) else None
        for i, (kind, pts) in enumerate(self.items):
            if i != self.hover:
                self._shape(vis, kind, pts, COL[kind])
        if self.hover is not None:                           # highlighted hint: on top, with a halo
            kind, pts = self.items[self.hover]
            self._shape(vis, kind, pts, (255, 255, 255), width=8)
            self._shape(vis, kind, pts, COL[kind], width=4)
        if self.mode == "e":                                 # edit: ring on the point to pick / being moved
            ij = self.grab[:2] if self.grab is not None else self.point_at(*self.mouse)
            if ij is not None:
                q = self.to_scr(self.items[ij[0]][1][ij[1]])
                cv2.circle(vis, q, 9, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.circle(vis, q, 9, EDIT_COL, 2 if self.grab is None else 3, cv2.LINE_AA)
        if self.cur:
            self._shape(vis, self.mode, self.cur + [list(self.to_img(*self.mouse))], COL["cur"], closed_ok=False)
        vis = np.hstack([vis, self._draw_result()])
        cv2.line(vis, (self.rview.x0, BAR_H), (self.rview.x0, self.win[1]), (30, 30, 30), 2)
        self._draw_bar(vis)
        self._draw_list(vis, hit)
        self._draw_status(vis)
        return vis

    def _draw_auto(self, vis):
        """Automatic guides: used ones thin and solid, dropped ones dashed (see the help text)."""
        if self.found is None:
            return
        hints = {"ignore": self.hints()["ignore"]}
        kept, _ = apply_hints([Guide(g.kind, g.pts.copy(), g.src, g.w) for g in self.found[0]], hints)
        kept_pts = {id(g): g for g in kept}
        keys = {guide_key(g) for g in kept}
        # which of the kept guides the fit dropped; only known when the right pane is up to date
        used = self.result_used if self.fresh else None
        n_ign = n_fit = 0
        boxes = [np.array(b, float).reshape(2, 2) for b in hints["ignore"]]
        for g in self.found[0]:                       # parts removed by an ignore box: dark dashed
            cut = np.zeros(len(g.pts), bool)
            for b in boxes:
                cut |= np.all((g.pts >= b.min(0)) & (g.pts <= b.max(0)), axis=1)
            if (~cut).sum() < 3:                      # too little left: step 1 drops the whole guide
                cut[:] = True
            s = [self.to_scr(p) for p in g.pts]
            for i in range(len(s) - 1):
                if cut[i] or cut[i + 1]:
                    dashed(vis, s[i:i + 2], IGNORED_COL)
        for g in kept_pts.values():
            s = [self.to_scr(p) for p in g.pts]
            if used is not None and guide_key(g) not in used:
                dashed(vis, s, UNFIT_COL)
                n_fit += 1
            else:
                cv2.polylines(vis, [np.array(s, np.int32)], False, COL[g.kind], 1, cv2.LINE_AA)
        total = sum(len(g.pts) for g in self.found[0])
        n_ign = total - sum(len(g.pts) for g in kept)
        n_lost = len(self.found[0]) - len(keys)
        fit = f", {n_fit} dropped by the fit (magenta)" if used is not None else ", fit: updating"
        self.msg = (f"{len(self.found[0])} automatic guides: {n_lost} removed and {n_ign} points cut "
                    f"by ignore boxes (dark dashed){fit}")

    def _draw_result(self):
        """Right pane: the dewarped page for the current hints, and what state it is in."""
        v = self.rview
        if self.result is None:
            pane = np.full((v.h, v.w, 3), 60, np.uint8)
        else:
            pane = v.draw(self.result, self.result_small)
        dots = "." * (1 + int(time.time() * 2) % 3)
        if self.error and self.fresh:
            txt = f"dewarp failed: {self.error}"
        elif self.result is None:
            txt = "dewarping " + dots
        elif not self.fresh:
            txt = "updating " + dots
        else:
            txt = f"dewarped  {self.result.shape[1]} x {self.result.shape[0]}"
            if self.saved_key == self.result_key:
                txt += "   saved"
        font, fs = cv2.FONT_HERSHEY_SIMPLEX, 0.45
        (tw, th), _ = cv2.getTextSize(txt, font, fs, 1)
        cv2.rectangle(pane, (0, v.h - th - 12), (tw + 16, v.h), (30, 30, 30), -1)
        cv2.putText(pane, txt, (8, v.h - 7), font, fs, (235, 235, 235), 1, cv2.LINE_AA)
        return pane

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

    title = "dewarp_tool"
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
    print("hints saved next to the photos (*.hints.json). Pages you did not save with s still need step 1.")


if __name__ == "__main__":
    main()
