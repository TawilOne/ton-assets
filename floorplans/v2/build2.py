"""Строит схемы этажей главного корпуса для мини-приложения.

    python floorplans/v2/build2.py [папка-для-превью]

Берёт форму этажей (floors.py, общий скелет — building.py) и позиции номеров кабинетов
(../rooms_ocr.json, переводятся в общую систему координат через rectify.py), раскладывает кабинеты
по рядам и пишет docs/plans/main-N.svg, docs/rooms.json, docs/plans.json.
С папкой для превью дополнительно рисует схему поверх выпрямленного фото — для сверки.
"""
import json
import pathlib
import sys

import rectify
from floors import FLOORS

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"
PAD = 14            # поля вокруг здания в схеме
MIN_CELL = 16       # кабинет не уже этого (ед.), если в ряду хватает места
OVERRIDE = {}       # ручные поправки позиций номеров: {"343": (x, y)} в каноне


def isotonic(t):
    """Неубывающая последовательность, ближайшая к t (алгоритм PAVA)."""
    blocks = []
    for v in t:
        blocks.append([v, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, n2 = blocks.pop()
            v1, n1 = blocks.pop()
            blocks.append([(v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2])
    out = []
    for v, n in blocks:
        out += [v] * n
    return out


def split(lo, hi, pos):
    """Границы ячеек на отрезке [lo, hi] для номеров в точках pos (по возрастанию):
    посередине между соседями, но каждая ячейка не уже min(MIN_CELL, длина/количество)."""
    n = len(pos)
    if n == 0:
        return []
    w = min(MIN_CELL, (hi - lo) / n)
    mids = [(pos[i] + pos[i + 1]) / 2 for i in range(n - 1)]
    t = [m - lo - (i + 1) * w for i, m in enumerate(mids)]
    c = [min(max(v, 0), hi - lo - n * w) for v in isotonic(t)]
    b = [lo] + [lo + (i + 1) * w + v for i, v in enumerate(c)] + [hi]
    return [(b[i], b[i + 1]) for i in range(n)]


def inside(px, py, r, m=0):
    return r[0] - m <= px <= r[2] + m and r[1] - m <= py <= r[3] + m


def dist(px, py, r):
    dx = max(r[0] - px, 0, px - r[2])
    dy = max(r[1] - py, 0, py - r[3])
    return (dx * dx + dy * dy) ** 0.5


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def free_segments(row, fixed):
    """Части ряда вдоль оси, не занятые лестницами/лифтами."""
    x0, y0, x1, y1, axis = row[:5]
    lo, hi = (x0, x1) if axis == "x" else (y0, y1)
    cuts = []
    for f in fixed:
        if overlaps(row, f):
            cuts.append((f[0], f[2]) if axis == "x" else (f[1], f[3]))
    segs, cur = [], lo
    for a, b in sorted(cuts):
        if a > cur:
            segs.append((cur, a))
        cur = max(cur, b)
    if cur < hi:
        segs.append((cur, hi))
    return [s for s in segs if s[1] - s[0] > 1]


def layout(floor_no, spec, labels):
    """Раскладка: [(номер, (x0, y0, x1, y1))] + список номеров, которые никуда не попали."""
    fixed = [s[1] for s in spec["stairs"]] + [s[1] for s in spec["lifts"]] + [c[1] for c in spec["cells"]] \
        + spec["blocks"] + spec["gaps"]
    # сегменты рядов
    segs = []
    for row in spec["rows"]:
        x0, y0, x1, y1, axis = row[:5]
        for a, b in free_segments(row, fixed):
            rect = (a, y0, b, y1) if axis == "x" else (x0, a, x1, b)
            segs.append(dict(rect=rect, axis=axis, rooms=[]))
    lost = []
    fixed_names = {c[0] for c in spec["cells"] if c[0]} | {p[0] for p in spec["polys"] if p[0]}
    for name, (px, py) in labels.items():
        if name in fixed_names:
            continue
        hits = [s for s in segs if inside(px, py, s["rect"])]
        if not hits:
            hits = [s for s in segs if inside(px, py, s["rect"], 10)]
        if not hits:
            near = min(segs, key=lambda s: dist(px, py, s["rect"]))
            if dist(px, py, near["rect"]) > 40:
                lost.append(name)
                continue
            hits = [near]
        s = min(hits, key=lambda s: dist(px, py, s["rect"]))
        s["rooms"].append((name, px, py))
    cells = list(spec["cells"])
    for s in segs:
        x0, y0, x1, y1 = s["rect"]
        k = 1 if s["axis"] == "x" else 2
        rooms = sorted(s["rooms"], key=lambda t: t[k])
        lo, hi = (x0, x1) if s["axis"] == "x" else (y0, y1)
        for (name, _, _), (a, b) in zip(rooms, split(lo, hi, [t[k] for t in rooms])):
            cells.append((name, (a, y0, b, y1) if s["axis"] == "x" else (x0, a, x1, b)))
        if not rooms:  # ряд без номеров — просто помещения без подписи
            cells.append((None, s["rect"]))
    return cells, lost


def f1(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def rect(r, cls, extra=""):
    x0, y0, x1, y1 = r
    return f'<rect class="{cls}" x="{f1(x0)}" y="{f1(y0)}" width="{f1(x1 - x0)}" height="{f1(y1 - y0)}"{extra}/>'


def stair_svg(r):
    """Лестница: прямоугольник со ступеньками поперёк длинной стороны."""
    x0, y0, x1, y1 = r
    out = [rect(r, "stair")]
    lines = []
    if x1 - x0 >= y1 - y0:
        n = max(3, int((x1 - x0) / 5))
        for i in range(1, n):
            x = x0 + (x1 - x0) * i / n
            lines.append(f"M{f1(x)} {f1(y0 + 2)}V{f1(y1 - 2)}")
    else:
        n = max(3, int((y1 - y0) / 5))
        for i in range(1, n):
            y = y0 + (y1 - y0) * i / n
            lines.append(f"M{f1(x0 + 2)} {f1(y)}H{f1(x1 - 2)}")
    out.append(f'<path class="steps" d="{"".join(lines)}"/>')
    return "".join(out)


def lift_svg(r):
    x0, y0, x1, y1 = r
    return rect(r, "lift") + f'<path class="steps" d="M{f1(x0 + 3)} {f1(y0 + 3)}L{f1(x1 - 3)} {f1(y1 - 3)}M{f1(x1 - 3)} {f1(y0 + 3)}L{f1(x0 + 3)} {f1(y1 - 3)}"/>'


def build(floor_no, spec, labels):
    cells, lost = layout(floor_no, spec, labels)
    shapes = [r[:4] for r in spec["rows"]] + spec["halls"] + [s[1] for s in spec["stairs"]] + \
        [s[1] for s in spec["lifts"]] + spec["blocks"] + [c[1] for c in spec["cells"]] + spec["gaps"] + \
        [(min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p))
         for _, p in spec["polys"]]
    xs = [v for r in shapes for v in (r[0], r[2])]
    ys = [v for r in shapes for v in (r[1], r[3])]
    vx, vy = min(xs) - PAD, min(ys) - PAD
    vw, vh = max(xs) + PAD - vx, max(ys) + PAD - vy
    # контур здания: каждый прямоугольник обводится толстой линией под заливкой — снаружи остаётся
    # единая внешняя стена без щелей и перекрытий
    rect_shapes = [r[:4] for r in spec["rows"]] + spec["halls"] + spec["gaps"] + [s[1] for s in spec["stairs"]] + \
        [s[1] for s in spec["lifts"]] + spec["blocks"] + [c[1] for c in spec["cells"]]
    pts = lambda p: " ".join(f"{f1(x)},{f1(y)}" for x, y in p)
    outline = "".join(rect(r, "wall") for r in rect_shapes) + \
        "".join(f'<polygon class="wall" points="{pts(p)}"/>' for _, p in spec["polys"])
    floor_fill = "".join(rect(r, "base") for r in rect_shapes) + \
        "".join(f'<polygon class="base" points="{pts(p)}"/>' for _, p in spec["polys"])
    rooms_svg, nums, out = [], [], {}
    for name, p in spec["polys"]:
        if name is None:  # холл
            rooms_svg.append(f'<polygon class="hall" points="{pts(p)}"/>')
            continue
        rooms_svg.append(f'<polygon class="room" data-room="{name}" points="{pts(p)}"/>')
        cx = sum(x for x, _ in p) / len(p)
        cy = sum(y for _, y in p) / len(p)
        nums.append(f'<text class="num" data-room="{name}" x="{f1(cx)}" y="{f1(cy)}" font-size="9">{name}</text>')
        out[name] = {"plan": f"main-{floor_no}", "x": round((cx - vx) / vw, 4), "y": round((cy - vy) / vh, 4)}
    for name, r in cells:
        if name is None:
            rooms_svg.append(rect(r, "room nolabel"))
            continue
        rooms_svg.append(rect(r, "room", f' data-room="{name}"'))
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        w, h = r[2] - r[0], r[3] - r[1]
        size = max(5.0, min(9.0, (max(w, h) - 2) / max(len(name), 2) * 1.55, min(w, h) * 0.62))
        rot = f' transform="rotate(-90 {f1(cx)} {f1(cy)})"' if h > w * 1.4 and w < len(name) * size * 0.62 else ""
        nums.append(f'<text class="num" data-room="{name}" x="{f1(cx)}" y="{f1(cy)}" '
                    f'font-size="{f1(size)}"{rot}>{name}</text>')
        out[name] = {"plan": f"main-{floor_no}", "x": round((cx - vx) / vw, 4), "y": round((cy - vy) / vh, 4)}
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f1(vx)} {f1(vy)} {f1(vw)} {f1(vh)}" '
           f'class="floor-svg">'
           f'<g class="g-wall">{outline}</g><g class="g-base">{floor_fill}</g>'
           f'<g class="g-halls">{"".join(rect(h, "hall") for h in spec["halls"] + spec["gaps"])}</g>'
           f'<g class="g-blocks">{"".join(rect(b, "block") for b in spec["blocks"])}</g>'
           f'<g class="g-rooms">{"".join(rooms_svg)}</g>'
           f'<g class="g-stairs">{"".join(stair_svg(s[1]) for s in spec["stairs"])}'
           f'{"".join(lift_svg(s[1]) for s in spec["lifts"])}</g>'
           f'<g class="g-nums">{"".join(nums)}</g></svg>')
    return svg, out, lost, (vx, vy, vw, vh), cells


def load_labels():
    ocr = json.loads((HERE.parent / "rooms_ocr.json").read_text(encoding="utf-8"))
    by = {}
    for name, m in ocr.items():
        f = int(m["plan"].split("-")[1])
        by.setdefault(f, {})[name] = rectify.ocr_to_canon(f, m["x"], m["y"])
    for name, xy in OVERRIDE.items():
        f = int(name[0]) if name[0].isdigit() else 2
        by.setdefault(f, {})[name] = xy
    return by


def main():
    preview = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    labels = load_labels()
    rooms, plans = {}, {}
    for f, spec in FLOORS.items():
        svg, out, lost, box, cells = build(f, spec, labels.get(f, {}))
        (DOCS / "plans" / f"main-{f}.svg").write_text(svg, encoding="utf-8")
        plans[f"main-{f}"] = f"plans/main-{f}.svg"
        rooms.update(out)
        print(f"этаж {f}: кабинетов {len(out)}" + (f", не размещены: {', '.join(lost)}" if lost else ""))
        if preview:
            overlay(f, spec, cells, preview)
    (DOCS / "rooms.json").write_text(json.dumps(dict(sorted(rooms.items())), ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    (DOCS / "plans.json").write_text(json.dumps(dict(sorted(plans.items())), ensure_ascii=False, indent=1),
                                     encoding="utf-8")


def overlay(f, spec, cells, folder):
    """Схема поверх выпрямленного фото r{f}.jpg (см. rectify.py) — для сверки глазами."""
    from PIL import Image, ImageDraw
    X0, Y0, S = -260, -60, 1.6
    im = Image.open(folder / f"r{f}.jpg").convert("RGB")
    d = ImageDraw.Draw(im, "RGBA")
    P = lambda x, y: ((x - X0) * S, (y - Y0) * S)
    for _, p in spec["polys"]:
        d.polygon([P(x, y) for x, y in p], outline=(255, 0, 255, 255))
    for h in spec["halls"] + spec["gaps"]:
        d.rectangle([P(h[0], h[1]), P(h[2], h[3])], fill=(0, 200, 0, 40))
    for name, r in cells:
        d.rectangle([P(r[0], r[1]), P(r[2], r[3])], outline=(255, 0, 255, 255), width=2)
        if name:
            d.text(P((r[0] + r[2]) / 2 - 6, (r[1] + r[3]) / 2 + 4), name, fill=(160, 0, 160, 255))
    for _, r in spec["stairs"]:
        d.rectangle([P(r[0], r[1]), P(r[2], r[3])], outline=(255, 120, 0, 255), width=3)
    for _, r in spec["lifts"]:
        d.rectangle([P(r[0], r[1]), P(r[2], r[3])], outline=(0, 160, 255, 255), width=3)
    for b in spec["blocks"]:
        d.rectangle([P(b[0], b[1]), P(b[2], b[3])], outline=(90, 90, 90, 255), width=2)
    im.save(folder / f"o{f}.jpg", quality=85)


if __name__ == "__main__":
    main()
