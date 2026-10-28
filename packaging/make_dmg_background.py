"""Draw the DMG window background (1x + 2x for Retina).

    python packaging/make_dmg_background.py OUT_DIR [--unsigned]

Writes background.png and background@2x.png. Layout must match
dmg_settings.py: a 660x420 window, app icon centred at (170, 215),
Applications at (490, 215).
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 660, 420
APP_X, APPS_X, ICON_Y = 170, 490, 215

FONT_CANDIDATES = {
    "bold": [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def _font(kind: str, size: int):
    for path in FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            font = ImageFont.truetype(path, size)
            if kind == "bold" and path.endswith("SFNS.ttf"):
                try:
                    font.set_variation_by_name("Bold")
                except (OSError, ValueError):
                    pass
            return font
    return ImageFont.load_default(size)


def _centered(draw, y, text, font, fill, scale):
    w = draw.textlength(text, font=font)
    draw.text(((WIDTH * scale - w) / 2, y * scale), text, font=font, fill=fill)


def render(scale: int, unsigned: bool) -> Image.Image:
    img = Image.new("RGB", (WIDTH * scale, HEIGHT * scale))
    top, bottom = (252, 252, 253), (238, 238, 242)
    px = img.load()
    for y in range(HEIGHT * scale):
        t = y / (HEIGHT * scale - 1)
        row = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
        for x in range(WIDTH * scale):
            px[x, y] = row
    draw = ImageDraw.Draw(img)

    _centered(draw, 34, "melograb", _font("bold", 30 * scale), (24, 24, 27), scale)
    _centered(draw, 74, "Drag melograb into Applications to install", _font("regular", 14 * scale),
              (113, 113, 122), scale)

    # Arrow from the app icon to the Applications folder.
    y = ICON_Y * scale
    x0, x1 = (APP_X + 82) * scale, (APPS_X - 82) * scale
    color = (161, 161, 170)
    draw.line([(x0, y), (x1 - 10 * scale, y)], fill=color, width=4 * scale)
    head = 13 * scale
    draw.polygon([(x1, y), (x1 - head, y - head * 0.7), (x1 - head, y + head * 0.7)], fill=color)

    if unsigned:
        hint_font = _font("regular", 12 * scale)
        _centered(draw, 352, "First launch: right-click melograb in Applications and choose Open.",
                  hint_font, (113, 113, 122), scale)
        _centered(draw, 370, "(Newer macOS: System Settings › Privacy & Security › Open Anyway.)",
                  hint_font, (161, 161, 170), scale)
    return img


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "build/dmg"
    unsigned = "--unsigned" in sys.argv
    os.makedirs(out, exist_ok=True)
    render(1, unsigned).save(os.path.join(out, "background.png"))
    render(2, unsigned).save(os.path.join(out, "background@2x.png"))
    print(f"wrote {out}/background.png and background@2x.png")


if __name__ == "__main__":
    main()
