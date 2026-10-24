"""Карта территории (двор) Губкинского университета → docs/plans/campus.svg

    python floorplans/campus.py

Контуры сняты со спутникового снимка (координаты — пиксели снимка 1179×860, север сверху).
Главный корпус рисуется по плану 2 этажа (v2/floors.py) — так он совпадает с поэтажными картами.
"""
import json
import pathlib
import sys

from shapely.geometry import Polygon, box  # pip install shapely (нужно только для сборки карты)
from shapely.ops import unary_union

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "v2"))
from floors import FLOORS  # noqa: E402

DOCS = HERE.parent / "docs"
VIEW = (330, 70, 1030, 840)   # видимая часть снимка: x0, y0, x1, y1

# план главного корпуса (канон 0–1000) → снимок
MX, MSX, MY, MSY = 427, 0.383, 465, 0.33


def main_outline():
    s = FLOORS[2]
    rects = [r[:4] for r in s["rows"]] + s["halls"] + s["gaps"] + [c[1] for c in s["cells"]] + \
        [x[1] for x in s["lifts"]] + s["blocks"]
    t = lambda x, y: (MX + x * MSX, MY + y * MSY)
    out = [(t(r[0], r[1]), t(r[2], r[3])) for r in rects]
    polys = [[t(x, y) for x, y in p] for _, p in s["polys"]]
    return out, polys


# здания: id → (подпись, номер на схеме, контуры-прямоугольники (x0, y0, x1, y1))
BUILDINGS = {
    "k2": ("2 корпус", "2", [(505, 177, 895, 235), (620, 82, 665, 177), (712, 82, 760, 177),
                             (810, 82, 860, 177)]),   # спортзалы — по плану цокольного этажа
    "dk": ("ДК", "4", [(760, 300, 910, 400), (790, 290, 910, 300)]),
    "lib": ("Библиотека", "5", [(944, 300, 1012, 428)]),
}
# крытые переходы между зданиями
GALLERIES = [
    (779, 400, 793, 465),    # ДК → главный корпус
    (828, 235, 842, 290),    # ДК → 2 корпус
    (779, 414, 946, 428),    # библиотека → переход ДК — главный корпус (входит в боковую стену библиотеки)
]
PARKING = [(500, 262, 618, 445), (500, 445, 776, 462)]
GARDEN = [(624, 250, 756, 440)]
LAWNS = [(330, 640, 600, 800), (720, 640, 1000, 800), (920, 440, 1025, 560)]   # газоны и деревья
TREES = [(640, 270), (735, 275), (650, 320), (720, 330), (660, 370), (742, 380), (635, 425), (705, 420),
         (360, 700), (420, 740), (520, 720), (575, 770), (780, 700), (850, 760), (930, 690), (970, 760),
         (955, 500), (1000, 520)]
PATHS = [  # дорожки: ломаные (x, y), ширина
    ([(662, 700), (662, 808)], 9),              # от главного входа к Ленинскому
    ([(624, 345), (690, 300), (756, 345)], 3),  # дорожки сквера
    ([(690, 250), (690, 440)], 3),
    ([(624, 400), (756, 400)], 3),
    ([(910, 470), (1020, 470)], 5),
    ([(500, 250), (895, 250)], 7),              # проезд вдоль 2 корпуса
]
ROADS = [((300, 812, 1030, 834), "Ленинский проспект")]
ENTRANCE = (662, 668)


def r(x0, y0, x1, y1, cls, extra=""):
    return (f'<rect class="{cls}" x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" '
            f'height="{y1 - y0:.1f}"{extra}/>')


def build():
    x0, y0, x1, y1 = VIEW
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {x1 - x0} {y1 - y0}" class="campus-svg">']
    for rr, name in ROADS:
        parts.append(r(*rr, "road"))
        parts.append(f'<text class="road-lbl" x="{(rr[0] + rr[2]) / 2:.0f}" y="{(rr[1] + rr[3]) / 2:.0f}">{name}</text>')
    for p in PARKING:
        parts.append(r(*p, "parking"))
    # разметка парковки
    stripes = []
    for xx in range(510, 615, 9):
        stripes.append(f"M{xx} 270V300M{xx} 318V348M{xx} 366V396M{xx} 410V438")
    parts.append(f'<path class="stall" d="{"".join(stripes)}"/>')
    for g in GARDEN + LAWNS:
        parts.append(r(*g, "garden"))
    parts.append("".join(f'<circle class="tree" cx="{x}" cy="{y}" r="7"/>' for x, y in TREES))
    for pts, w in PATHS:
        d = "M" + "L".join(f"{x} {y}" for x, y in pts)
        parts.append(f'<path class="walk" stroke-width="{w}" d="{d}"/>')
    rects, polys = main_outline()

    def outline(shapes):
        """Один сплошной контур здания (объединение всех частей) — без внутренних линий."""
        u = unary_union(shapes).buffer(0.6).buffer(-0.6)
        geoms = getattr(u, "geoms", [u])
        d = ""
        for g in geoms:
            for ring in [g.exterior, *g.interiors]:
                d += "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in ring.coords[:-1]) + "Z"
        return d

    shapes = {b: [box(*q) for q in rs] for b, (_, _, rs) in BUILDINGS.items()}
    shapes["main"] = [box(a[0], a[1], b[0], b[1]) for a, b in rects] + [Polygon(p) for p in polys]
    for b, sh in shapes.items():
        parts.append(f'<path class="bld" data-b="{b}" fill-rule="evenodd" d="{outline(sh)}"/>')
    # переходы — одним контуром без швов; там, где переход входит в здание, стена здания открыта
    gal = [box(*g) for g in GALLERIES]
    parts.append(f'<path class="gallery" fill-rule="evenodd" d="{outline(gal)}"/>')
    inside = unary_union(gal).buffer(-1.2, join_style=2)  # заливка без обводки поверх стен зданий
    for g in getattr(inside, "geoms", [inside]):
        d = "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in g.exterior.coords[:-1]) + "Z"
        parts.append(f'<path class="gallery-open" d="{d}"/>')
    ends = []
    for g in GALLERIES:  # концы переходов заходят в здания на 1.5 — эти кусочки перекрывают стену
        for b, sh in shapes.items():
            hit = box(*g).intersection(unary_union(sh))
            if not hit.is_empty and hit.area > 0:
                x0_, y0_, x1_, y1_ = hit.bounds
                ends.append(r(x0_, y0_, x1_, y1_, "gallery-open"))
    parts.extend(ends)
    # вход
    ex, ey = ENTRANCE
    parts.append(f'<path class="entry" d="M{ex - 7} {ey + 22}L{ex} {ey + 8}L{ex + 7} {ey + 22}"/>')
    parts.append(f'<text class="lbl small" x="{ex + 12}" y="{ey + 40}">главный вход</text>')
    # подписи
    labels = {"main": ("Главный корпус", "1", 660, 548), "sachok": ("Сачок", "7", 662, 630)}
    centers = {"main": (660, 518)}
    for b, (name, num, rs) in BUILDINGS.items():


        big = max(rs, key=lambda q: (q[2] - q[0]) * (q[3] - q[1]))
        cx, cy = (big[0] + big[2]) / 2, (big[1] + big[3]) / 2
        centers[b] = (cx, cy)
        labels[b] = (name, num, cx, cy)
    for b, (name, num, cx, cy) in labels.items():
        vertical = b == "lib"
        rot = f' transform="rotate(-90 {cx:.0f} {cy:.0f})"' if vertical else ""
        parts.append(f'<text class="lbl" data-b="{b}" x="{cx:.0f}" y="{cy:.0f}"{rot}>{name}</text>')
    parts.append("</svg>")
    (DOCS / "plans" / "campus.svg").write_text("".join(parts), encoding="utf-8")
    # центры зданий в долях картинки — для красной точки в мини-приложении
    w, h = x1 - x0, y1 - y0
    out = {b: {"x": round((cx - x0) / w, 4), "y": round((cy - y0) / h, 4)} for b, (cx, cy) in centers.items()}
    (DOCS / "campus.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("campus.svg:", len(parts), "элементов")


if __name__ == "__main__":
    build()
