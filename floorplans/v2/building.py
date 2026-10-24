"""Схема главного корпуса в общей системе координат («канон», см. rectify.py).

Все этажи — одно и то же здание в форме «Н»: левое и правое крыло (x 0–134 и 866–1000,
y 0–455) и центральная часть между ними (y 184–315). Лестницы и лифты стоят в одних и тех же
местах на всех этажах. У нижних этажей есть пристройки — они описаны в FLOORS.

Обозначения в описании этажа:
  rows   — ряды кабинетов (x0, y0, x1, y1, ось): кабинеты делят ряд вдоль оси по своим номерам
  halls  — коридоры и холлы
  stairs — лестницы, lifts — лифты, blocks — помещения без номеров (тёмные)
  gaps   — проходы, которые прорезают ряды кабинетов (рисуются как коридор)
  polys  — помещения произвольной формы: (номер или None, [(x, y), ...]); None — холл
  cells  — помещения с точными границами: (номер или None, (x0, y0, x1, y1)); ряды их обходят
"""

# ---- общий «скелет» (одинаков на всех этажах)
LW = (0, 134)          # левое крыло: запад, восток
LC = (56, 78)          # коридор левого крыла
RW = (866, 1000)       # правое крыло
RC = (924, 946)        # коридор правого крыла
TOP, BOT = 0, 455      # торцы крыльев
CN, CS = 184, 315      # северная и южная стены центральной части
CC = (238, 262)        # центральный коридор
LOBBY = (214, 300)     # холл у пересечения коридоров (по y)

STAIRS = [
    ("ЛМ-1", (0, 232, LC[0], 276)),
    ("ЛМ-2", (472, CN, 510, CC[0])),
    ("ЛМ-3", (RC[1], 232, RW[1], 276)),
]
LIFTS = [
    ("ЛХ-1", (98, CN, LW[1], LOBBY[0])),
    ("ЛХ-1", (98, LOBBY[1], LW[1], CS)),
    ("ЛХ-2", (RW[0], CN, 902, LOBBY[0])),
    ("ЛХ-2", (RW[0], LOBBY[1], 902, CS)),
]


def core(l_top=TOP, l_bot=BOT, r_top=TOP, r_bot=BOT, l_west=True, l_east=True, r_west=True, r_east=True):
    """Стандартные ряды «Н». *_top/*_bot — докуда идут ряды кабинетов вдоль коридора крыла."""
    rows = [
        (LW[1], CN, RW[0], CC[0], "x"),           # центр, северный ряд
        (LW[1], CC[1], RW[0], CS, "x"),           # центр, южный ряд
    ]
    halls = [
        (LW[1], CC[0], RW[0], CC[1]),             # центральный коридор
        (LC[0], l_top, LC[1], l_bot),             # коридор левого крыла
        (RC[0], r_top, RC[1], r_bot),             # коридор правого крыла
        (LC[1], LOBBY[0], LW[1], LOBBY[1]),       # холл слева
        (RW[0], LOBBY[0], RC[0], LOBBY[1]),       # холл справа
        (LC[1], CN, 98, LOBBY[0]), (LC[1], LOBBY[1], 98, CS),          # проходы к лифтам
        (902, CN, RC[0], LOBBY[0]), (902, LOBBY[1], RC[0], CS),
    ]
    if l_west:
        rows += [(LW[0], l_top, LC[0], 232, "y"), (LW[0], 276, LC[0], l_bot, "y")]
    if l_east:
        rows += [(LC[1], l_top, LW[1], CN, "y"), (LC[1], CS, LW[1], l_bot, "y")]
    if r_west:
        rows += [(RW[0], r_top, RC[0], CN, "y"), (RW[0], CS, RC[0], r_bot, "y")]
    if r_east:
        rows += [(RC[1], r_top, RW[1], 232, "y"), (RC[1], 276, RW[1], r_bot, "y")]
    return dict(rows=rows, halls=halls, stairs=list(STAIRS), lifts=list(LIFTS), blocks=[], cells=[], gaps=[], polys=[], doors=[])


def floor(**kw):
    """core() + дополнения: rows/halls/stairs/lifts/blocks добавляются, drop — убрать ряды/холлы,
    пересекающие указанные прямоугольники (под торцевые залы и т.п.)."""
    extra = {k: kw.pop(k, []) for k in ("rows", "halls", "stairs", "lifts", "blocks", "cells", "gaps", "polys", "doors", "drop")}
    spec = core(**kw)

    def hit(r, d):
        return r[0] < d[2] and d[0] < r[2] and r[1] < d[3] and d[1] < r[3]

    for d in extra["drop"]:
        spec["rows"] = [r for r in spec["rows"] if not hit(r, d)]
        spec["halls"] = [h for h in spec["halls"] if not hit(h, d)]
    for k in ("rows", "halls", "stairs", "lifts", "blocks", "cells", "gaps", "polys", "doors"):
        spec[k] += extra[k]
    return spec
