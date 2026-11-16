"""
Step 3 - bundle a folder of page images into one PDF (pages in natural file-name order).

  scanclean-pdf clean                  # -> clean.pdf next to clean/
  scanclean-pdf clean -o article.pdf --dpi 300
  scanclean-pdf clean --as-is          # images exactly as they are, each its own size

By default every page gets the same paper size and margins: pages are scaled slightly so the
printed area has the same size throughout, and the printed area is centred left to right at the same
distance from the top (a partial last page sits at the top with empty paper below). The
images in the folder are not changed. Requires: img2pdf
"""
import argparse
import sys
from pathlib import Path

import cv2

from .scanlib.pages import layout
from .scanlib.util import imread, list_images


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with page images")
    ap.add_argument("-o", "--out", help="PDF to write (default: <src>.pdf next to the folder)")
    ap.add_argument("--only", nargs="*", help="include only these file stems")
    ap.add_argument("--dpi", type=float, default=300, help="resolution tag: sets the physical page size")
    ap.add_argument("--margin", type=float, default=0.08,
                    help="white border on every side, as a fraction of the widest printed area")
    ap.add_argument("--max-scale", type=float, default=0.15,
                    help="largest scaling used to even out page sizes (0.15 = 85-115%%)")
    ap.add_argument("--as-is", action="store_true", help="embed the images unchanged, each at its own size")
    args = ap.parse_args(argv)
    import img2pdf

    src = Path(args.src)
    files = list_images(src, args.only)
    if not files:
        sys.exit("no images found")
    out = Path(args.out) if args.out else src.with_suffix(".pdf")
    lay = img2pdf.get_fixed_dpi_layout_fun((args.dpi, args.dpi))
    if args.as_is:
        data = [p.read_bytes() for p in files]
    else:
        pages = layout([imread(p, cv2.IMREAD_GRAYSCALE) for p in files], args.margin, args.max_scale)
        data = [cv2.imencode(".png", pg, [cv2.IMWRITE_PNG_COMPRESSION, 6])[1].tobytes() for pg in pages]
    with open(out, "wb") as f:
        img2pdf.convert(data, layout_fun=lay, outputstream=f)
    print(f"{len(files)} pages -> {out}")


if __name__ == "__main__":
    main()
