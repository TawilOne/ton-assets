"""Находит перегородки между кабинетами на выпрямленных фото планов (hr{этаж}.png, см. rectify.py)
и сохраняет их в walls.json — дальше build2.py режет ряды кабинетов по настоящим стенам.

    python detect_walls.py ПАПКА_С_hr-ФАЙЛАМИ

Перегородка — тёмная (чёрная/серая, не цветная) линия поперёк ряда, проходящая через бо́льшую
часть его глубины. Значки на плане цветные или короткие, поэтому не мешают.
"""
import json
import pathlib
import sys

import numpy as np
from PIL import Image

import build2
from floors import FLOORS

X0, Y0, K = -240, -20, 4          # как в рендере hr{этаж}.png
HERE = pathlib.Path(__file__).resolve().parent


def dark_mask(img):
    a = np.asarray(img).astype(int)
    mx, mn = a.max(axis=2), a.min(axis=2)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    greenish = (g > r + 10) & (g > b + 10)  # зелёные стрелки эвакуации — не стены
    return (mx < 95) & (mx - mn < 35) & ~greenish


def walls_in(mask, rect, axis):
    x0, y0, x1, y1 = rect
    px = lambda v: int(round((v - X0) * K))
    py = lambda v: int(round((v - Y0) * K))
    if axis == "x":
        d0, d1 = py(y0 + (y1 - y0) * .22), py(y1 - (y1 - y0) * .22)
        a0, a1 = px(x0) + 8, px(x1) - 8
        prof = mask[d0:d1, a0:a1].mean(axis=0)
    else:
        d0, d1 = px(x0 + (x1 - x0) * .22), px(x1 - (x1 - x0) * .22)
        a0, a1 = py(y0) + 8, py(y1) - 8
        prof = mask[a0:a1, d0:d1].mean(axis=1)
    hit = prof > .6
    out, i = [], 0
    while i < len(hit):
        if hit[i]:
            j = i
            while j < len(hit) and hit[j]:
                j += 1
            if j - i <= 22:  # стена тонкая; широкое тёмное пятно — значок
                c = a0 + (i + j - 1) / 2
                out.append(round(c / K + (X0 if axis == "x" else Y0), 1))
            i = j
        else:
            i += 1
    merged = []  # две стороны одной толстой стены — одна перегородка
    for v in out:
        if merged and v - merged[-1] < 4:
            merged[-1] = round((merged[-1] + v) / 2, 1)
        else:
            merged.append(v)
    return merged


def main(folder):
    folder = pathlib.Path(folder)
    res = {}
    for f, spec in FLOORS.items():
        mask = dark_mask(Image.open(folder / f"hr{f}.png").convert("RGB"))
        fixed = [s[1] for s in spec["stairs"]] + [s[1] for s in spec["lifts"]] + \
            [c[1] for c in spec["cells"]] + spec["blocks"] + spec["gaps"]
        floor_walls = {}
        for row in spec["rows"]:
            for a, b in build2.free_segments(row, fixed):
                x0, y0, x1, y1, axis = row[:5]
                rect = (a, y0, b, y1) if axis == "x" else (x0, a, x1, b)
                floor_walls[build2.seg_key(rect)] = {"axis": axis, "walls": walls_in(mask, rect, axis)}
        res[str(f)] = floor_walls
        print(f, sum(len(v["walls"]) for v in floor_walls.values()), "перегородок")
    (HERE / "walls.json").write_text(json.dumps(res, ensure_ascii=False, indent=0), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
