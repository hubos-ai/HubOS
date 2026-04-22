"""Clean up the user-supplied AI assets so they're actually usable.

Both ``icon_1.png`` and ``logo_1.png`` arrived 100 % opaque — the AI baked
a fake "transparency checkerboard" into the area outside the artwork.
Putting either file straight into the .icns / web header would carry that
checker into the Dock and the page header.

Strategy:
    Flood-fill from each of the four corners with a moderate colour
    tolerance. The fake checker is a low-saturation light-grey field, so a
    flood from any corner stops cleanly at the dark squircle edge (icon)
    or the dark wordmark glyph edges (logo).

Outputs (overwriting any previous versions):
    icon.png  — 1024x1024 RGBA, transparent corners, ready for iconutil.
    logo.png  — full-resolution wordmark on a transparent background.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent

# Tolerance used by ImageDraw.floodfill when matching neighbour pixels to
# the seed colour. The AI checker is consistently within ~30 of pure white
# in any channel, so 60 is comfortably tolerant without bleeding into the
# squircle (where R drops below ~80).
FLOOD_THRESH = 60

TRANSPARENT = (0, 0, 0, 0)


def make_corners_transparent(im: Image.Image, thresh: int = FLOOD_THRESH) -> Image.Image:
    """Return a copy of ``im`` with the background flood-filled to alpha 0
    starting from each corner. Operates in-place on the returned copy.
    """
    out = im.convert("RGBA").copy()
    w, h = out.size
    for seed in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        # ImageDraw.floodfill replaces the contiguous matching region with the
        # given value. Passing fully-transparent black gives us the alpha cut
        # we want.
        ImageDraw.floodfill(out, seed, TRANSPARENT, thresh=thresh)
    return out


def finalize_icon() -> Path:
    src = HERE / "icon_1.png"
    out_path = HERE / "icon.png"
    im = Image.open(src)
    cleaned = make_corners_transparent(im)
    # Downsample to 1024 with LANCZOS — the input is 2048 so we get one full
    # halving step which keeps the squircle edges crisp.
    cleaned = cleaned.resize((1024, 1024), Image.LANCZOS)
    cleaned.save(out_path)
    return out_path


def finalize_logo() -> Path:
    src = HERE / "logo_1.png"
    out_path = HERE / "logo.png"
    im = Image.open(src)
    cleaned = make_corners_transparent(im)
    # Trim away any fully-transparent border that's left over so callers can
    # size the header img by height without padding.
    bbox = cleaned.getbbox()
    if bbox:
        cleaned = cleaned.crop(bbox)
    cleaned.save(out_path)
    return out_path


def report(path: Path) -> None:
    im = Image.open(path).convert("RGBA")
    a = im.split()[-1]
    hist = a.histogram()
    pct_transparent = 100 * hist[0] / (im.size[0] * im.size[1])
    print(
        f"  {path.name:14s}  size={im.size}  "
        f"corner=({im.getpixel((0, 0))})  alpha0={pct_transparent:5.1f}%",
    )


def main() -> None:
    print("processed:")
    report(finalize_icon())
    report(finalize_logo())


if __name__ == "__main__":
    main()
