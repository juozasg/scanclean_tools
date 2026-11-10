#!/usr/bin/env python3
"""
step1_dewarp.py - GEOMETRY ONLY: photo of a page -> flat page, colours untouched.

  python step1_dewarp.py PHOTO_DIR                 # -> PHOTO_DIR/flat/
  python step1_dewarp.py PHOTO_DIR --only 156 164  # redo selected pages (after adding hints)

Writes, per photo
  flat/<name>.jpg            flattened colour page, cropped to the page (or to the crop hint)
  flat/meta/<name>.mask.png  white where the pixel belongs to this page
  flat/meta/<name>.json      text height, fit quality ... (read by step2_tone.py)
  flat/debug/<name>_guides.jpg   guides and fitted page grid drawn on the photo
and flat/report.json. Hints are read from PHOTO_DIR/<name>.hints.json (see hint_tool.py).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2

from scanlib.dewarp import dewarp_page, draw_debug, status
from scanlib.hints import load_hints
from scanlib.util import imwrite, list_images, load_photo


def fmt(d):
    return " ".join(f"{k}={v:.1f}" for k, v in d.items()) or "-"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with photos, or a single photo")
    ap.add_argument("-o", "--out", help="output folder (default: <src>/flat)")
    ap.add_argument("--only", nargs="*", help="process only these file stems (e.g. 157 164)")
    ap.add_argument("--format", choices=["jpg", "png"], default="jpg", help="jpg (quality 95) or lossless png")
    ap.add_argument("--scale", type=float, default=1.0, help="output pixel density relative to the photo")
    ap.add_argument("--focal35", type=float, help="35 mm-equivalent focal length if EXIF lacks it (phones: ~26)")
    ap.add_argument("--work", type=int, default=2000, help="long side used for analysis")
    ap.add_argument("--warn", type=float, default=2.5, help="flag pages whose guide residual exceeds this (px)")
    ap.add_argument("--no-polish", action="store_true", help="sheet model only, no residual field")
    ap.add_argument("--no-debug", action="store_true")
    args = ap.parse_args(argv)

    src = Path(args.src)
    files = list_images(src, args.only)
    if not files:
        sys.exit("no images found")
    out_dir = Path(args.out) if args.out else (src.parent if src.is_file() else src) / "flat"
    (out_dir / "meta").mkdir(parents=True, exist_ok=True)
    if not args.no_debug:
        (out_dir / "debug").mkdir(exist_ok=True)

    rep_path = out_dir / "report.json"
    report = {r["page"]: r for r in json.loads(rep_path.read_text(encoding="utf-8"))} if rep_path.exists() else {}
    print(f"{'page':>8} {'text':>4} {'rule':>4} {'hint':>4}  {'before':<16} {'after':<16} status")
    todo = []
    for p in files:
        t0 = time.time()
        try:
            bgr, f35 = load_photo(p)
            res = dewarp_page(bgr, load_hints(p), args.focal35 or f35 or 26.0, args.scale, args.work,
                              not args.no_polish)
        except Exception as e:                           # keep the batch going
            print(f"{p.stem:>8}  FAILED: {e}")
            report[p.stem] = {"page": p.stem, "status": f"FAILED: {e}"}
            todo.append(p.stem)
            continue
        m = res["meta"]
        m.update(page=p.stem, source=p.name, status=status(m, args.warn), sec=round(time.time() - t0, 1))
        if args.format == "png":
            imwrite(out_dir / (p.stem + ".png"), res["image"], [cv2.IMWRITE_PNG_COMPRESSION, 3])
        else:
            imwrite(out_dir / (p.stem + ".jpg"), res["image"], [cv2.IMWRITE_JPEG_QUALITY, 95])
        other = out_dir / (p.stem + (".jpg" if args.format == "png" else ".png"))
        if other.exists():                               # never leave two versions of a page
            other.unlink()
        imwrite(out_dir / "meta" / (p.stem + ".mask.png"), res["mask"])
        (out_dir / "meta" / (p.stem + ".json")).write_text(json.dumps(m, indent=1), encoding="utf-8")
        if not args.no_debug:
            draw_debug(bgr, res["guides"], res["sheet"], res["polish"], res["box"],
                       out_dir / "debug" / (p.stem + "_guides.jpg"))
        report[p.stem] = m
        if m["status"] != "ok":
            todo.append(p.stem)
        print(f"{p.stem:>8} {m['text']:>4} {m['rules']:>4} {m['hints']:>4}  {fmt(m['raw']):<16} "
              f"{fmt(m['final']):<16} {m['status']}" + ("  (rotated text)" if m["rotated"] else ""))
    rep_path.write_text(json.dumps(list(report.values()), indent=1), encoding="utf-8")
    if todo:
        print("\nlook at", out_dir / "debug", "for:", " ".join(todo),
              "\n-> add hints with:  python hint_tool.py", args.src, "--only", " ".join(todo))


if __name__ == "__main__":
    main()
