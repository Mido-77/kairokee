"""Draw the Kairokee icon (an amber microphone on the app's night purple) and save
app/static/icon.png (512 px, browser tab / phone home screen) and app/static/icon.ico (Windows shortcut).

    python tools/make_icon.py        (needs Pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
NIGHT, STAGE, AMBER, INK = (21, 18, 38), (55, 44, 99), (255, 184, 77), (42, 27, 0)


def draw(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(220 * s), fill=NIGHT)
    d.ellipse([int(150 * s), int(150 * s), int(874 * s), int(874 * s)], fill=STAGE)
    # microphone head
    d.rounded_rectangle([int(392 * s), int(220 * s), int(632 * s), int(580 * s)], radius=int(120 * s), fill=AMBER)
    for y in (330, 400, 470):   # grille lines
        d.line([int(430 * s), int(y * s), int(594 * s), int(y * s)], fill=INK, width=max(1, int(14 * s)))
    # holder arc, stem and base
    w = max(2, int(36 * s))
    d.arc([int(320 * s), int(380 * s), int(704 * s), int(700 * s)], start=0, end=180, fill=AMBER, width=w)
    d.rectangle([int(494 * s), int(690 * s), int(530 * s), int(790 * s)], fill=AMBER)
    d.rounded_rectangle([int(400 * s), int(780 * s), int(624 * s), int(820 * s)], radius=int(20 * s), fill=AMBER)
    return img


if __name__ == "__main__":
    big = draw()
    out = ROOT / "app" / "static"
    big.resize((512, 512), Image.LANCZOS).save(out / "icon.png")
    big.save(out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved", out / "icon.png", out / "icon.ico")
