"""Shrink oversized images in place (run via Docker):

    docker compose run --rm tools tools/optimize_images.py            # content/ and static/
    docker compose run --rm tools tools/optimize_images.py content/my-post

Images wider than --max-width (or taller than --max-height) are resized and
re-encoded; smaller files are left untouched, so running it twice is safe.
File names never change, so Markdown references stay valid.
"""
import argparse
import io
from pathlib import Path

from PIL import Image, ImageOps

EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def optimise(path: Path, max_w: int, max_h: int, min_bytes: int) -> str | None:
    data = path.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        too_big = im.width > max_w or im.height > max_h
        if not too_big and len(data) < min_bytes:
            return None
        before = (im.width, im.height, len(data))
        if too_big:
            im.thumbnail((max_w, max_h), Image.LANCZOS)
        out = io.BytesIO()
        ext = path.suffix.lower()
        if ext in (".jpg", ".jpeg"):
            im.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
        elif ext == ".webp":
            im.save(out, "WEBP", quality=82)
        else:
            im.save(out, "PNG", optimize=True)
    if out.tell() >= len(data) and not too_big:
        return None
    path.write_bytes(out.getvalue())
    return f"{path}: {before[0]}x{before[1]} {before[2] // 1024} KB -> {im.width}x{im.height} {out.tell() // 1024} KB"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=["content", "static"])
    ap.add_argument("--max-width", type=int, default=1600)
    ap.add_argument("--max-height", type=int, default=2200)
    ap.add_argument("--min-bytes", type=int, default=600_000,
                    help="re-encode files above this size even if dimensions are fine")
    args = ap.parse_args()
    changed = 0
    for root in args.paths:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.suffix.lower() in EXTS)
        for f in files:
            msg = optimise(f, args.max_width, args.max_height, args.min_bytes)
            if msg:
                changed += 1
                print(msg)
    print(f"{changed} image(s) optimised")


if __name__ == "__main__":
    main()
