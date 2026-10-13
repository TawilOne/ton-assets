"""Строит векторные схемы этажей (docs/plans/main-N.svg) из зон (zones.py) и позиций
номеров кабинетов, распознанных на фото (rooms_ocr.json).

    python floorplans/build_svg.py

Результат: docs/plans/main-N.svg, docs/rooms.json (центр ячейки каждого кабинета), docs/plans.json.
"""
import json
import pathlib

from zones import FLOORS

HERE = pathlib.Path(__file__).resolve().parent
DOCS = HERE.parent / "docs"
ASPECT = {8: 595 / 1600, 7: 776 / 1600, 6: 734 / 1600, 5: 772 / 1600,
          4: 592 / 1600, 3: 599 / 1600, 2: 847 / 1600, 1: 663 / 1600}  # высота/ширина плана

ocr = json.loads((HERE / "rooms_ocr.json").read_text(encoding="utf-8"))


def inside(px, py, z):
    return z[0] <= px <= z[2] and z[1] <= py <= z[3]


def dist(px, py, z):
    dx = max(z[0] - px, 0, px - z[2]); dy = max(z[1] - py, 0, py - z[3])
    return (dx * dx + dy * dy) ** 0.5


rooms_out, plans_out = {}, {}
for floor, spec in FLOORS.items():
    plan = f"main-{floor}"
    H = 1000 * ASPECT[floor]
    sy = H / 1000  # перевод y из «0–1000» в координаты SVG
    zones = spec["rooms"]
    members = {i: [] for i in range(len(zones))}
    for room, m in ocr.items():
        if m["plan"] != plan:
            continue
        px, py = m["x"] * 1000, m["y"] * 1000
        hits = [i for i, z in enumerate(zones) if inside(px, py, z)]
        i = hits[0] if hits else min(range(len(zones)), key=lambda k: dist(px, py, zones[k]))
        members[i].append((room, px, py))

    defs, shapes, cells, labels = [], [], [], []
    R = lambda x0, y0, x1, y1, cls, extra="": (
        f'<rect class="{cls}" x="{x0:.1f}" y="{y0 * sy:.1f}" width="{x1 - x0:.1f}" '
        f'height="{(y1 - y0) * sy:.1f}"{extra}/>')
    for b in spec.get("blocks", []):
        shapes.append(R(*b, "block"))
    for zi, z in enumerate(zones):
        x0, y0, x1, y1, axis = z[:5]
        poly = z[5] if len(z) > 5 else None
        clip = ""
        if poly:
            pts = " ".join(f"{x:.1f},{y * sy:.1f}" for x, y in poly)
            defs.append(f'<clipPath id="c{zi}"><polygon points="{pts}"/></clipPath>')
            shapes.append(f'<polygon class="zone" points="{pts}"/>')
            clip = f' clip-path="url(#c{zi})"'
        else:
            shapes.append(R(x0, y0, x1, y1, "zone"))
        ms = sorted(members[zi], key=lambda t: t[1] if axis == "x" else t[2])
        lo, hi = (x0, x1) if axis == "x" else (y0, y1)
        pos = [t[1] if axis == "x" else t[2] for t in ms]
        for k, (room, px, py) in enumerate(ms):
            a = lo if k == 0 else (pos[k - 1] + pos[k]) / 2
            b = hi if k == len(ms) - 1 else (pos[k] + pos[k + 1]) / 2
            if b - a < 1:
                a, b = pos[k] - 4, pos[k] + 4
            cx0, cy0, cx1, cy1 = (a, y0, b, y1) if axis == "x" else (x0, a, x1, b)
            cells.append(R(cx0, cy0, cx1, cy1, "room", f' data-room="{room}"{clip}'))
            mx, my = (cx0 + cx1) / 2, (cy0 + cy1) / 2
            vertical = axis == "x" and (cx1 - cx0) < 22 and (cy1 - cy0) > 40
            rot = f' transform="rotate(-90 {mx:.1f} {my * sy:.1f})"' if vertical else ""
            labels.append(f'<text class="num" data-room="{room}" x="{mx:.1f}" y="{my * sy:.1f}"{rot}>{room}</text>')
            rooms_out[room] = {"plan": plan, "x": round(mx / 1000, 4), "y": round(my / 1000, 4)}
    halls = [R(*h, "hall") for h in spec.get("halls", [])]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 {H:.1f}" class="floor-svg">'
           f'<defs>{"".join(defs)}</defs>'
           f'<g class="g-blocks">{"".join(s for s in shapes if "block" in s)}</g>'
           f'<g class="g-zones">{"".join(s for s in shapes if "zone" in s)}</g>'
           f'<g class="g-rooms">{"".join(cells)}</g>'
           f'<g class="g-halls">{"".join(halls)}</g>'
           f'<g class="g-nums">{"".join(labels)}</g></svg>')
    (DOCS / "plans" / f"{plan}.svg").write_text(svg, encoding="utf-8")
    plans_out[plan] = f"plans/{plan}.svg"
    print(plan, sum(len(v) for v in members.values()), "кабинетов")

(DOCS / "rooms.json").write_text(json.dumps(dict(sorted(rooms_out.items())), ensure_ascii=False, indent=1),
                                 encoding="utf-8")
(DOCS / "plans.json").write_text(json.dumps(plans_out, ensure_ascii=False, indent=1), encoding="utf-8")
