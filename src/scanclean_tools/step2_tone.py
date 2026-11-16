"""
Step 2 - COLOUR ONLY: flat colour page -> clean greyscale scan.

  scanclean-tone flat                   # -> clean/ next to flat/
  scanclean-tone flat --white 0.82      # remove more show-through
  scanclean-tone some_folder --no-trim            # any folder of page images works

Every input image is assumed to be cropped so that it shows only the paper of one page.
Per page: sharpening of out-of-focus regions, flat-field (uneven light, shadows and paper
tint -> white), local ink level (faint
print is lifted), greyscale levels, removal of show-through from the back of the page, trim
to the printed area. The text height and the trim
flag come from flat/meta/<name>.json (written by scanclean-dewarp) when it exists; otherwise
the text height is estimated from the image itself.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2

from .scanlib.tone import tone_page
from .scanlib.util import imread, imwrite, list_images


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
    ap.add_argument("--deblur", type=float, default=2.5, metavar="GAIN",
                    help="sharpen regions that are out of focus; higher is stronger, 0 = off")
    ap.add_argument("--no-adaptive", action="store_true",
                    help="same levels everywhere (no lifting of faded print near the gutters)")
    ap.add_argument("--keep-edges", action="store_true",
                    help="keep dark streaks along the image edges (gutter / page-edge shadow)")
    ap.add_argument("--edge-band", type=float, default=0.15, metavar="FRAC",
                    help="width of the border strip checked for edge shadow, as a fraction of the page (default 0.15)")
    ap.add_argument("--show-through", type=int, default=110, metavar="LEVEL",
                    help="remove faint patches that never get darker than this grey level (0-255): "
                         "show-through from the back of the page. Higher removes more, 0 keeps all")
    args = ap.parse_args(argv)

    src = Path(args.src)
    files = list_images(src, args.only)
    if not files:
        sys.exit("no images found")
    base = src.parent if src.is_file() else src
    out_dir = Path(args.out) if args.out else base.resolve().parent / "clean"
    out_dir.mkdir(parents=True, exist_ok=True)

    for p in files:
        bgr = imread(p)
        meta_p = base / "meta" / (p.stem + ".json")
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
        info = {}
        out = tone_page(bgr, meta.get("text_height"), meta.get("core"),
                        trim=not args.no_trim and meta.get("trim", True), margin=args.margin,
                        white=args.white, black=args.black, gamma=args.gamma, sharpen=args.sharpen,
                        show_through=args.show_through, deblur=args.deblur,
                        adaptive=not args.no_adaptive, edge_shadow=not args.keep_edges,
                        edge_band=args.edge_band, info=info)
        dst = out_dir / (p.stem + "." + args.format)
        imwrite(dst, out, [cv2.IMWRITE_PNG_COMPRESSION, 6] if args.format == "png" else [cv2.IMWRITE_JPEG_QUALITY, 90])
        other = out_dir / (p.stem + (".jpg" if args.format == "png" else ".png"))
        if other.exists():
            other.unlink()
        W = bgr.shape[1]
        zl, zr = info.get("zones", (0, W))
        gut = "" if (zl, zr) == (0, W) else f"  faded gutter: left {100 * zl // W}%, right {100 * (W - zr) // W}%"
        print(f"{p.stem:>8}  {out.shape[1]} x {out.shape[0]}  -> {dst}{gut}")


if __name__ == "__main__":
    main()
