#!/usr/bin/env python3
"""
step2_tone.py - COLOUR ONLY: flat colour page -> clean greyscale scan.

  python step2_tone.py PHOTO_DIR/flat                   # -> PHOTO_DIR/clean/
  python step2_tone.py PHOTO_DIR/flat --white 0.82      # remove more show-through
  python step2_tone.py some_folder --no-trim            # any folder of page images works

Per page: flat-field (uneven light, shadows and paper tint -> white), local ink level (faint
print is lifted), greyscale levels, whitening of everything that is not this page, trim to the
printed area. The page mask and text height come from flat/meta/ (written by step1_dewarp.py);
without them they are estimated from the image itself.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2

from scanlib.tone import auto_mask, tone_page
from scanlib.util import imread, imwrite, list_images


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with flat pages (output of step 1), or a single image")
    ap.add_argument("-o", "--out", help="output folder (default: <src>/../clean)")
    ap.add_argument("--only", nargs="*", help="process only these file stems")
    ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--white", type=float, default=0.88, help="paper fraction mapped to pure white (lower = cleaner)")
    ap.add_argument("--black", type=float, default=0.25, help="0..1, higher = bolder ink")
    ap.add_argument("--gamma", type=float, default=1.0)
    ap.add_argument("--sharpen", type=float, default=0.5)
    ap.add_argument("--margin", type=float, default=4.0, help="white border around the print, in text heights")
    ap.add_argument("--no-trim", action="store_true", help="keep the frame of the input image")
    ap.add_argument("--no-mask", action="store_true", help="do not whiten anything outside the page")
    args = ap.parse_args(argv)

    src = Path(args.src)
    files = list_images(src, args.only)
    if not files:
        sys.exit("no images found")
    base = src.parent if src.is_file() else src
    out_dir = Path(args.out) if args.out else base.parent / "clean"
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in files:
        bgr = imread(p)
        meta_p, mask_p = base / "meta" / (p.stem + ".json"), base / "meta" / (p.stem + ".mask.png")
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        if args.no_mask:
            mask = None
        elif mask_p.exists():
            mask = imread(mask_p, cv2.IMREAD_GRAYSCALE)
            if mask.shape != bgr.shape[:2]:              # stale sidecar
                mask = auto_mask(bgr)
        else:
            mask = auto_mask(bgr)
        out = tone_page(bgr, mask, meta.get("text_height"), meta.get("core"),
                        trim=not args.no_trim and meta.get("trim", True), margin=args.margin,
                        white=args.white, black=args.black, gamma=args.gamma, sharpen=args.sharpen)
        dst = out_dir / (p.stem + "." + args.format)
        imwrite(dst, out, [cv2.IMWRITE_PNG_COMPRESSION, 6] if args.format == "png" else [cv2.IMWRITE_JPEG_QUALITY, 90])
        other = out_dir / (p.stem + (".jpg" if args.format == "png" else ".png"))
        if other.exists():
            other.unlink()
        print(f"{p.stem:>8}  {out.shape[1]} x {out.shape[0]}  -> {dst}")


if __name__ == "__main__":
    main()
