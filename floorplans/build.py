"""Собирает планы этажей главного корпуса и координаты кабинетов из результатов OCR."""
import json, math, re, sys
from PIL import Image, ImageDraw
from walls import extract_walls, render_png

U = "/root/.claude/uploads/d3551011-e7dc-5311-b68d-2999c2649b1b/"
FILES = {8: "cb200b47", 7: "ff53c885", 6: "0d142c43", 5: "787e2705", 4: "f6af910b", 3: "76331745", 2: "c7d03166", 1: "0a821348"}
OUT = sys.argv[1]            # папка docs
PREVIEW = sys.argv[2]        # папка для превью с точками
data = json.load(open("ocr_all.json"))

LAT2CYR = str.maketrans({"A": "А", "a": "А", "B": "В", "б": "Б", "E": "Е", "e": "Е"})
FIX = {"4412": "441-2", "2421": "242", "2495": "249Б", "2566": "256Б", "1108A": "108А", "1108А": "108А",
       "115-": None, "323-": None}


def norm(t):
    t = t.strip().replace(" ", "")
    if t in FIX:
        return FIX[t]
    t = t.translate(LAT2CYR).upper()
    return FIX.get(t, t)


# порог «тёмнее фона»: на бледных/дальних фото стены светлее
DELTA = {1: 20, 2: 20, 3: 28}
THIN = {1, 2}  # портретные фото, где план мелкий
rooms_out, plans_out = {}, {}
for floor, f in FILES.items():
    items = data[str(floor)]
    pat = re.compile(rf"^{floor}\d\d([А-Я]|-\d{{1,2}}|/\d)?$")
    # рамка «О пожаре звонить ... 112» — всё рядом с ней не кабинеты
    phone = [i for i in items if any(k in i["t"].upper() for k in ("МОБИЛ", "ПОЖАРЕ", "01,"))]
    cands = {}
    for i in items:
        t = norm(i["t"])
        if not t or not pat.match(t):
            continue
        # «112» стоит прямо под «01, 101» в рамке «О пожаре звонить»
        if any("01," in p["t"] and abs(i["cx"] - p["cx"]) < 90 and 0 < i["cy"] - p["cy"] < 130 for p in phone):
            continue
        cands.setdefault(t, []).append(i)
    # схлопываем повторы: точки одного номера, лежащие рядом, усредняем;
    # если номер найден в разных местах — берём то, что ближе к другим номерам (водяные знаки «200» стоят особняком)
    groups = {}
    for t, lst in cands.items():
        clusters = []
        for i in lst:
            for c in clusters:
                if math.hypot(i["cx"] - c[0]["cx"], i["cy"] - c[0]["cy"]) < 60:
                    c.append(i); break
            else:
                clusters.append([i])
        groups[t] = [(sum(i["cx"] for i in c) / len(c), sum(i["cy"] for i in c) / len(c), len(c)) for c in clusters]
    single = {t: g[0] for t, g in groups.items() if len(g) == 1}
    chosen = {}
    for t, g in groups.items():
        if len(g) == 1:
            chosen[t] = g[0][:2]; continue
        others = [v for k, v in single.items() if k != t]
        def crowd(p):
            d = sorted(math.hypot(p[0] - o[0], p[1] - o[1]) for o in others)[:3]
            return sum(d) / max(len(d), 1) - 40 * p[2]
        chosen[t] = min(g, key=crowd)[:2]
    # БАА (Большая академическая аудитория) — на плане 2 этажа
    if floor == 2:  # подпись «БАА» распознаватель не прочитал — место взято с фото вручную
        chosen["БАА"] = (886, 1513)

    im = Image.open(U + f + "-image.jpg").convert("RGB")
    W, H = im.size
    xs = [p[0] for p in chosen.values()]; ys = [p[1] for p in chosen.values()]
    mx, my = 0.045 * W, 0.05 * H
    box = (max(0, min(xs) - mx), max(0, min(ys) - my), min(W, max(xs) + mx), min(H, max(ys) + my))
    if floor == 2:  # БАА — ниже всех подписей, захватываем весь зал
        box = (box[0], box[1], box[2], min(H, box[3] + 0.06 * H))
    box = tuple(int(v) for v in box)
    crop = im.crop(box)
    cw, ch = crop.size
    # рамки всех надписей (номера, слова) — чтобы стереть текст с карты
    tb = []
    for i in items:
        if "x0" in i:
            tb.append((i["x0"] - box[0], i["y0"] - box[1], i["x1"] - box[0], i["y1"] - box[1]))
        else:
            n = len(i["t"]); w, h = 18 * n + 8, 32
            if i.get("src", "").startswith("rot"):
                w, h = h, w
            cx, cy = i["cx"] - box[0], i["cy"] - box[1]
            tb.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    thin = floor in THIN
    if thin:  # мелкий план: увеличиваем перед обработкой
        crop = crop.resize((cw * 2, ch * 2), Image.LANCZOS)
        tb = [(a * 2, b * 2, c * 2, e * 2) for a, b, c, e in tb]
    if floor == 2:  # внизу по бокам от БАА — таблицы «Действия при пожаре» / «Условные обозначения»
        zw, zh = crop.size
        tb += [(0, zh * 0.76, zw * 0.39, zh), (zw * 0.61, zh * 0.74, zw, zh)]
    walls = render_png(extract_walls(crop, tb, delta=DELTA.get(floor, 32), thin=thin))
    scale = min(1.0, 1600 / cw)
    final = walls.resize((int(cw * scale), int(ch * scale)), Image.LANCZOS)  # cw, ch — размеры до увеличения
    plan_id = f"main-{floor}"
    final.save(f"{OUT}/plans/{plan_id}.png", optimize=True)
    plans_out[plan_id] = f"plans/{plan_id}.png"
    prev = Image.new("RGBA", final.size, (11, 11, 13, 255)); prev.alpha_composite(final); d = ImageDraw.Draw(prev)
    for t, (x, y) in sorted(chosen.items()):
        rx, ry = (x - box[0]) / cw, (y - box[1]) / ch
        rooms_out[t] = {"plan": plan_id, "x": round(rx, 4), "y": round(ry, 4)}
        px, py = rx * final.size[0], ry * final.size[1]
        d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(255, 0, 0))
        d.text((px + 6, py - 6), t, fill=(160, 160, 170))
    prev.convert("RGB").save(f"{PREVIEW}/prev-{floor}.jpg", quality=75)
    print(floor, len(chosen), "rooms", final.size, sorted(chosen))

json.dump(dict(sorted(rooms_out.items())), open(f"{OUT}/rooms.json", "w"), ensure_ascii=False, indent=1)
json.dump(plans_out, open(f"{OUT}/plans.json", "w"), ensure_ascii=False, indent=1)
print("total", len(rooms_out))
