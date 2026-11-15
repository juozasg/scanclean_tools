"""
All three steps in one command.

  scanclean PHOTO_DIR                    # geometry -> colour -> PDF; flat/, clean/ and
                                         # clean.pdf are made next to PHOTO_DIR
  scanclean PHOTO_DIR --only 156 164     # redo selected pages, rebuild the PDF from all pages
  scanclean PHOTO_DIR/167.jpg --no-pdf   # one photo, no PDF
  scanclean PHOTO_DIR --steps 2 3 --white 0.82    # new look, geometry is not redone

Only the most used options are offered here; each step has more:
  scanclean-dewarp --help    scanclean-tone --help    scanclean-pdf --help
"""
import argparse
from pathlib import Path

from . import step1_dewarp, step2_tone, step3_pdf


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="folder with photos, or a single photo")
    ap.add_argument("--only", nargs="*", help="process only these file stems")
    ap.add_argument("--steps", nargs="*", type=int, choices=[1, 2, 3], default=[1, 2, 3],
                    help="which steps to run (default: 1 2 3)")
    ap.add_argument("--pdf", help="PDF to write (default: clean.pdf next to PHOTO_DIR)")
    ap.add_argument("--no-pdf", action="store_true", help="skip step 3")
    ap.add_argument("--scale", type=float, help="step 1: output pixel density relative to the photo")
    ap.add_argument("--white", type=float, help="step 2: paper fraction mapped to white (lower = cleaner)")
    ap.add_argument("--black", type=float, help="step 2: 0..1, higher = bolder ink")
    ap.add_argument("--margin", type=float, help="step 2: white border, in text heights")
    args = ap.parse_args(argv)

    def opts(*names):
        out = []
        for n in names:
            if getattr(args, n) is not None:
                out += [f"--{n}", str(getattr(args, n))]
        return out

    src = Path(args.src)
    base = src.parent if src.is_file() else src
    out = base.resolve().parent                    # flat/, clean/ and clean.pdf go next to the photos
    only = ["--only", *args.only] if args.only else (["--only", src.stem] if src.is_file() else [])
    if 1 in args.steps:
        print(f"== step 1/3  geometry   {base} -> {out / 'flat'}")
        step1_dewarp.main([str(base), "-o", str(out / "flat"), *only, *opts("scale")])
    if 2 in args.steps:
        print(f"== step 2/3  colour     {out / 'flat'} -> {out / 'clean'}")
        step2_tone.main([str(out / "flat"), "-o", str(out / "clean"), *only, *opts("white", "black", "margin")])
    if 3 in args.steps and not args.no_pdf:
        pdf = args.pdf or str(out / "clean.pdf")
        print(f"== step 3/3  pdf        {out / 'clean'} -> {pdf}")
        step3_pdf.main([str(out / "clean"), "-o", pdf])


if __name__ == "__main__":
    main()
