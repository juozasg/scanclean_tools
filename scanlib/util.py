"""Small shared helpers: file listing, image reading / writing, greyscale conversion."""
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def list_images(src, only=None):
    """Images of a folder (not of its sub-folders) in natural page order, or the single file."""
    src = Path(src)
    files = [src] if src.is_file() else sorted((p for p in src.iterdir() if p.suffix.lower() in IMG_EXT),
                                               key=natural)
    if only:
        files = [p for p in files if p.stem in set(only)]
    return files


def imread(path, flags=cv2.IMREAD_COLOR):
    """Image reader that also works with non-ASCII paths on Windows."""
    return cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), flags)


def imwrite(path, img, params=()):
    """Image writer that also works with non-ASCII paths on Windows."""
    ok, buf = cv2.imencode(Path(path).suffix, img, list(params))
    if not ok:
        raise IOError(f"cannot encode {path}")
    Path(path).write_bytes(buf.tobytes())


def natural(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).stem)]


def odd(x, lo=3):
    x = max(int(round(x)), lo)
    return x | 1


def load_photo(path):
    """BGR uint8 image with EXIF rotation applied + 35 mm-equivalent focal length (or None)."""
    im = Image.open(path)
    f35 = None
    try:
        ifd = im.getexif().get_ifd(0x8769)
        v = float(ifd.get(41989, 0) or 0)          # FocalLengthIn35mmFilm
        if v > 5:
            f35 = v
    except Exception:
        pass
    im = ImageOps.exif_transpose(im).convert("RGB")
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR), f35


def to_gray(bgr):
    """Greyscale weighted towards red/green: yellowed paper stays bright, stains fade."""
    b, g, r = cv2.split(bgr.astype(np.float32))
    return 0.45 * r + 0.45 * g + 0.10 * b

