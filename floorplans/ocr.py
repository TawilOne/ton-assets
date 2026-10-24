import json, re, sys
from rapidocr_onnxruntime import RapidOCR
U = "/root/.claude/uploads/d3551011-e7dc-5311-b68d-2999c2649b1b/"
FILES = {8: "cb200b47", 7: "ff53c885", 6: "0d142c43", 5: "787e2705", 4: "f6af910b", 3: "76331745", 2: "c7d03166", 1: "0a821348"}
ocr = RapidOCR()
out = {}
for floor, f in FILES.items():
    res, _ = ocr(U + f + "-image.jpg")
    items = []
    for box, text, score in res or []:
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        items.append({"t": text, "s": round(float(score), 2), "cx": sum(xs) / 4, "cy": sum(ys) / 4,
                      "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys)})
    out[floor] = items
    rooms = [i["t"] for i in items if re.fullmatch(rf"{floor}\d\d\s*[-/]?\s*[0-9А-ЯA-Zа-яa-zБб]*", i["t"].strip())]
    print(floor, len(items), "rooms:", len(rooms), sorted(rooms))
json.dump(out, open("ocr.json", "w"), ensure_ascii=False)
