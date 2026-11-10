#!/usr/bin/env python3
"""
scanclean.py - all three steps in one command (same as run_all.sh, for shells without bash).

  python scanclean.py PHOTO_DIR                    # geometry -> colour, results in PHOTO_DIR/clean
  python scanclean.py PHOTO_DIR --pdf article.pdf  # ... and bundle all cleaned pages
  python scanclean.py PHOTO_DIR --only 156 164     # redo selected pages

For step-specific options run the steps yourself:
  step1_dewarp.py (geometry)   step2_tone.py (colour)   step3_pdf.py (PDF)
"""
import argparse
from pathlib import Path

import step1_dewarp
import step2_tone
import step3_pdf


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with photos, or a single photo")
    ap.add_argument("--only", nargs="*", help="process only these file stems")
    ap.add_argument("--pdf", help="also write all cleaned pages into this PDF")
    args = ap.parse_args(argv)
    src = Path(args.src)
    base = src.parent if src.is_file() else src
    only = ["--only", *args.only] if args.only else (["--only", src.stem] if src.is_file() else [])
    step1_dewarp.main([str(base), "-o", str(base / "flat"), *only])
    step2_tone.main([str(base / "flat"), "-o", str(base / "clean"), *only])
    if args.pdf:
        step3_pdf.main([str(base / "clean"), "-o", args.pdf])


if __name__ == "__main__":
    main()
