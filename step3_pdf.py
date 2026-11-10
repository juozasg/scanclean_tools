#!/usr/bin/env python3
"""
step3_pdf.py - bundle a folder of page images into one PDF (pages in natural file-name order).

  python step3_pdf.py PHOTO_DIR/clean                  # -> PHOTO_DIR/clean.pdf
  python step3_pdf.py PHOTO_DIR/clean -o article.pdf --dpi 300

Images are embedded as they are (no recompression). Requires: img2pdf
"""
import argparse
import sys
from pathlib import Path

from scanlib.util import list_images


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with page images")
    ap.add_argument("-o", "--out", help="PDF to write (default: <src>.pdf next to the folder)")
    ap.add_argument("--only", nargs="*", help="include only these file stems")
    ap.add_argument("--dpi", type=float, default=300, help="resolution tag: sets the physical page size")
    args = ap.parse_args(argv)
    import img2pdf

    src = Path(args.src)
    files = list_images(src, args.only)
    if not files:
        sys.exit("no images found")
    out = Path(args.out) if args.out else src.with_suffix(".pdf")
    lay = img2pdf.get_fixed_dpi_layout_fun((args.dpi, args.dpi))
    out.write_bytes(img2pdf.convert([p.read_bytes() for p in files], layout_fun=lay))
    print(f"{len(files)} pages -> {out}")


if __name__ == "__main__":
    main()
