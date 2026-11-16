"""
Step 2b - straighten cleaned pages with a smooth grid warp (no camera model).

  scanclean-grid clean                    # clean/ -> straight/ next to it
  scanclean-grid clean --only 144 148     # selected pages
  scanclean-grid clean --strength 2       # straighten harder (less smoothing)

Step 1 removes perspective and page curl; what is left - a few px of bending, a ripple,
uneven curl near the gutter - is removed here. Text lines, table / figure rules and text-block
margins found on the clean page, plus the hints marked with grid-tool (clean/<name>.grid.json),
are made straight, level / plumb and square by a smooth B-spline grid warp; the page is
resampled once. Automatic guides the warp cannot straighten (a sloping plot line ...) are
dropped. The text height comes from step 1's metadata (flat/meta/<name>.json) when it exists.

Writes straight/<name>.png and straight/meta/<name>.json (guide error before / after, grid,
node shifts). Use straight/ for step 3:  scanclean-pdf straight
"""
import argparse
import json
import sys
from pathlib import Path

import cv2

from .scanlib.grid import load_grid_hints, straighten, text_height_for
from .scanlib.util import imread, imwrite, list_images


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with clean pages (output of step 2), or a single image")
    ap.add_argument("-o", "--out", help="output folder (default: straight/ next to src)")
    ap.add_argument("--only", nargs="*", help="process only these file stems")
    ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--strength", type=float, default=1.0,
                    help="how hard to straighten: higher bends the page more freely, lower keeps it closer "
                         "to the input (a page's grid-tool hints can set their own)")
    ap.add_argument("--cell", type=float, default=4.0, help="grid cell size in text heights")
    args = ap.parse_args(argv)

    src = Path(args.src)
    files = list_images(src, args.only)
    if not files:
        sys.exit("no images found")
    base = src.parent if src.is_file() else src
    out_dir = Path(args.out) if args.out else base.resolve().parent / "straight"
    (out_dir / "meta").mkdir(parents=True, exist_ok=True)

    for p in files:
        gray = imread(p, cv2.IMREAD_GRAYSCALE)
        res = straighten(gray, load_grid_hints(p), text_height_for(p), strength=args.strength, cell=args.cell)
        m = res["meta"]
        dst = out_dir / (p.stem + "." + args.format)
        imwrite(dst, res["image"], [cv2.IMWRITE_PNG_COMPRESSION, 6] if args.format == "png"
                else [cv2.IMWRITE_JPEG_QUALITY, 92])
        other = out_dir / (p.stem + (".jpg" if args.format == "png" else ".png"))
        if other.exists():
            other.unlink()
        (out_dir / "meta" / (p.stem + ".json")).write_text(
            json.dumps(dict(m, page=p.stem, source=p.name)), encoding="utf-8")
        print(f"{p.stem:>8}  {m['guides']} guides ({m['dropped']} dropped)  error {m['rms_before']:.2f} -> "
              f"{m['rms_after']:.2f} px  max shift {m['max_shift']} px  -> {dst}")


if __name__ == "__main__":
    main()
