"""Рисует найденные перегородки поверх hr{этаж}.png → vw{этаж}.jpg (для проверки глазами)."""
import json, sys, pathlib
from PIL import Image, ImageDraw
X0, Y0, K = -240, -20, 4
folder = pathlib.Path(sys.argv[1]); f = sys.argv[2]
im = Image.open(folder / f"hr{f}.png").convert("RGB"); d = ImageDraw.Draw(im)
for key, v in json.load(open(pathlib.Path(__file__).parent / "walls.json"))[f].items():
    x0, y0, x1, y1 = map(float, key.split(","))
    P = lambda x, y: ((x - X0) * K, (y - Y0) * K)
    d.rectangle([P(x0, y0), P(x1, y1)], outline=(0, 120, 255), width=2)
    for w in v["walls"]:
        if v["axis"] == "x":
            d.line([P(w, y0), P(w, y1)], fill=(255, 0, 200), width=4)
        else:
            d.line([P(x0, w), P(x1, w)], fill=(255, 0, 200), width=4)
im = im.resize((im.size[0] // 2, im.size[1] // 2))
im.save(folder / f"vw{f}.jpg", quality=85)
