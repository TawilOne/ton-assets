"""Строит схемы этажей главного корпуса для мини-приложения.

    python floorplans/v2/build2.py [папка-для-превью]

Берёт форму этажей (floors.py, общий скелет — building.py) и позиции номеров кабинетов
(../rooms_ocr.json, переводятся в общую систему координат через rectify.py), раскладывает кабинеты
по рядам и пишет docs/plans/main-N.svg, docs/rooms.json, docs/plans.json.
С папкой для превью дополнительно рисует схему поверх выпрямленного фото — для сверки.
"""
import hashlib
import json
import pathlib
import sys

import rectify
from floors import FLOORS

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent.parent / "docs"
PAD = 14            # поля вокруг здания в схеме
MAX_CELL = 110      # кабинет не длиннее этого вдоль ряда (если номер в ряду один)
MIN_CELL = 16       # кабинет не уже этого (ед.), если в ряду хватает места
# ручные поправки: номера, которые распознаватель не прочитал на фото (позиция в каноне)
OVERRIDE = {
    "727": (21, 191), "730": (100, 440), "710Б": (986, 122),
    "440": (100, 15), "441-6": (33, 70), "414": (1154, 358), "422": (980, 40),
    "336Б": (248, 198), "241А": (1203, 362),
    "120": (549, 229), "120А": (591, 202), "118": (663, 202),
}


def seg_key(rect):
    return ",".join(f"{v:.1f}" for v in rect)


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


WALLS_FILE = HERE / "walls.json"
WALLS = json.loads(WALLS_FILE.read_text(encoding="utf-8")) if WALLS_FILE.exists() else {}
SLIVER = 26     # кусок без номера уже этого — не помещение
MIN_PIECE = 6   # помещение уже этого — скорее ошибка распознавания стены, сливаем с соседом


def by_walls(lo, hi, ws, rooms, k, mk):
    """Ряд режется по найденным перегородкам; номер попадает в свой кусок.
    Два номера в одном куске (стену не нашли) — делим кусок между ними."""
    bounds = [lo] + sorted(w for w in ws if lo + MIN_PIECE < w < hi - MIN_PIECE) + [hi]
    pieces = []
    for a, b in zip(bounds, bounds[1:]):
        if pieces and b - a < MIN_PIECE:
            pieces[-1] = (pieces[-1][0], b)
        elif pieces and pieces[-1][1] - pieces[-1][0] < MIN_PIECE:
            pieces[-1] = (pieces[-1][0], b)
        else:
            pieces.append((a, b))
    # узкий кусок без номера — обычно двойная линия стены или значок: присоединяем к соседу
    named = lambda a, b: any(a <= min(max(r[k], lo), hi - 1e-6) < b for r in rooms)
    changed = True
    while changed and len(pieces) > 1:
        changed = False
        for i, (a, b) in enumerate(pieces):
            # крошечный кабинет рядом с помещением без номера — стену нашли не там, объединяем
            if b - a < 25 and named(a, b):
                # узкий кабинет между помещениями без номера — его стены нашлись по значкам,
                # а настоящий кабинет занимает и соседние пустые куски
                nb = [j for j in (i - 1, i + 1) if 0 <= j < len(pieces) and not named(*pieces[j])]
                if nb:
                    j0, j1 = min(nb + [i]), max(nb + [i])
                    pieces[j0:j1 + 1] = [(pieces[j0][0], pieces[j1][1])]
                    changed = True
                    break
            if b - a < SLIVER and not named(a, b):
                # к какому соседу присоединить: к тому, чей номер ближе (номер стоит у своей стены)
                def dist_to(j):
                    if not 0 <= j < len(pieces):
                        return float("inf")
                    ps = [min(max(r[k], lo), hi - 1e-6) for r in rooms if pieces[j][0] <= min(max(r[k], lo), hi - 1e-6) < pieces[j][1]]
                    return min((abs(p - (a + b) / 2) for p in ps), default=1e6)
                j = min((i - 1, i + 1), key=dist_to)
                lo_, hi_ = min(pieces[i][0], pieces[j][0]), max(pieces[i][1], pieces[j][1])
                pieces[min(i, j)] = (lo_, hi_)
                del pieces[max(i, j)]
                changed = True
                break
    out = []
    for a, b in pieces:
        pos = lambda r: min(max(r[k], lo), hi - 1e-6)  # номер чуть за краем ряда — в крайний кусок
        inside_ = [r for r in rooms if a <= pos(r) < b]
        if not inside_:
            out.append((None, mk(a, b)))
            continue
        for (name, *_), (c, d) in zip(inside_, split(a, b, [pos(r) for r in inside_])):
            out.append((name, mk(c, d)))
    return out


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
    walls = WALLS.get(str(floor_no), {})
    for s in segs:
        x0, y0, x1, y1 = s["rect"]
        k = 1 if s["axis"] == "x" else 2
        rooms = sorted(s["rooms"], key=lambda t: t[k])
        lo, hi = (x0, x1) if s["axis"] == "x" else (y0, y1)
        mk = (lambda a, b: (a, y0, b, y1)) if s["axis"] == "x" else (lambda a, b: (x0, a, x1, b))
        found = walls.get(seg_key(s["rect"]))
        if found is not None:  # перегородки, найденные на фото (detect_walls.py), — режем по ним
            cells += by_walls(lo, hi, found["walls"], rooms, k, mk)
            continue
        for (name, *xy), (a, b) in zip(rooms, split(lo, hi, [t[k] for t in rooms])):
            if b - a > MAX_CELL:  # один номер на длинный ряд — не тянем кабинет на весь ряд
                c = min(max(xy[k - 1], a + MAX_CELL / 2), b - MAX_CELL / 2)
                a2, b2 = c - MAX_CELL / 2, c + MAX_CELL / 2
                for fa, fb in ((a, a2), (b2, b)):  # остаток — помещения без номера
                    n = round((fb - fa) / 38)
                    if n == 0:  # узкий остаток отдаём кабинету
                        a2, b2 = (fa, b2) if fa == a else (a2, fb)
                        continue
                    for i in range(n):
                        cells.append((None, mk(fa + (fb - fa) * i / n, fa + (fb - fa) * (i + 1) / n)))
                a, b = a2, b2
            cells.append((name, mk(a, b)))
        if not rooms:  # ряд без номеров на плане — делим на помещения обычного размера, без подписи
            n = max(1, round((hi - lo) / 38))
            for i in range(n):
                a, b = lo + (hi - lo) * i / n, lo + (hi - lo) * (i + 1) / n
                cells.append((None, (a, y0, b, y1) if s["axis"] == "x" else (x0, a, x1, b)))
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


def in_poly(px, py, poly):
    inside_ = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
            inside_ = not inside_
    return inside_


DOOR = 9  # ширина дверного проёма, ед.


def doors(cells, spec):
    """Дверь в каждом помещении — на стороне, которая выходит в коридор или холл.
    Рисуется проёмом в стене (отрезок цвета коридора поверх контура)."""
    halls = spec["halls"] + spec["gaps"]
    hpolys = [p for n, p in spec["polys"] if n is None]

    def is_hall(px, py):
        return any(inside(px, py, h) for h in halls) or any(in_poly(px, py, p) for p in hpolys)

    out, leaves = [], []
    for _, r in cells:
        x0, y0, x1, y1 = r
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        # (точка снаружи стороны, ориентация, координата стены, отрезок вдоль, куда «внутрь» комнаты)
        sides = [((cx, y0 - 3), "h", y0, x0, x1, 1), ((cx, y1 + 3), "h", y1, x0, x1, -1),
                 ((x0 - 3, cy), "v", x0, y0, y1, 1), ((x1 + 3, cy), "v", x1, y0, y1, -1)]
        for (px, py), kind, at, a, b, inward in sides:
            if not is_hall(px, py):
                continue
            w = min(DOOR, (b - a) * 0.5)
            m = (a + b) / 2
            p0, p1 = m - w / 2, m + w / 2
            sweep = 1 if inward > 0 else 0
            if kind == "h":
                out.append(f"M{f1(p0)} {f1(at)}H{f1(p1)}")
                leaves.append(f"M{f1(p0)} {f1(at)}V{f1(at + inward * w)}"
                              f"A{f1(w)} {f1(w)} 0 0 {1 - sweep} {f1(p1)} {f1(at)}")
            else:
                out.append(f"M{f1(at)} {f1(p0)}V{f1(p1)}")
                leaves.append(f"M{f1(at)} {f1(p0)}H{f1(at + inward * w)}"
                              f"A{f1(w)} {f1(w)} 0 0 {sweep} {f1(at)} {f1(p1)}")
            break
    for _, r in spec["stairs"] + spec["lifts"]:  # лестницы и лифты тоже открыты в коридор
        x0, y0, x1, y1 = r
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for (px, py), d in (((cx, y0 - 3), f"M{f1(x0 + 2)} {f1(y0)}H{f1(x1 - 2)}"),
                            ((cx, y1 + 3), f"M{f1(x0 + 2)} {f1(y1)}H{f1(x1 - 2)}"),
                            ((x0 - 3, cy), f"M{f1(x0)} {f1(y0 + 2)}V{f1(y1 - 2)}"),
                            ((x1 + 3, cy), f"M{f1(x1)} {f1(y0 + 2)}V{f1(y1 - 2)}")):
            if is_hall(px, py):
                out.append(d)
    return f'<path class="door" d="{"".join(out)}"/><path class="leaf" d="{"".join(leaves)}"/>'


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
           f'<g class="g-doors">{doors(cells + [(None, b) for b in spec["blocks"]], spec)}</g>'
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
        # ?v=хэш — чтобы Telegram не показывал старую схему из своего кэша после обновления
        ver = hashlib.md5(svg.encode()).hexdigest()[:8]
        plans[f"main-{f}"] = f"plans/main-{f}.svg?v={ver}"
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
