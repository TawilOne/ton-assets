"""Сверка: готовые кабинеты (контуры и номера) поверх выпрямленного фото плана.
    python check_overlay.py ПАПКА_С_hr  →  ck{этаж}L.jpg / ck{этаж}R.jpg"""
import sys, pathlib
from PIL import Image, ImageDraw, ImageFont
import build2
from floors import FLOORS
X0, Y0, K = -240, -20, 4
folder = pathlib.Path(sys.argv[1])
labels = build2.load_labels()
try:
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
except OSError:
    font = ImageFont.load_default()
for f, spec in FLOORS.items():
    cells, lost = build2.layout(f, spec, labels.get(f, {}))
    im = Image.open(folder / f"hr{f}.png").convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    P = lambda x, y: ((x - X0) * K, (y - Y0) * K)
    for name, r in cells:
        d.rectangle([P(r[0], r[1]), P(r[2], r[3])], outline=(255, 0, 200, 255), width=4)
        if name:
            cx, cy = P((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
            d.text((cx - 30, cy + 8), name, fill=(200, 0, 160, 255), font=font)
    for name, (x, y) in labels.get(f, {}).items():
        px, py = P(x, y)
        d.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(0, 140, 255, 255))
    w, h = im.size
    for side, box in (("L", (0, 0, w // 2 + 200, h)), ("R", (w // 2 - 200, 0, w, h))):
        c = im.crop(box); c = c.resize((c.size[0] // 2, c.size[1] // 2))
        c.save(folder / f"ck{f}{side}.jpg", quality=82)
    print(f, "lost:", lost)
