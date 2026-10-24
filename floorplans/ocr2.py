import json, re
from PIL import Image
import numpy as np
from rapidocr_onnxruntime import RapidOCR
U = "/root/.claude/uploads/d3551011-e7dc-5311-b68d-2999c2649b1b/"
FILES = {8: "cb200b47", 7: "ff53c885", 6: "0d142c43", 5: "787e2705", 4: "f6af910b", 3: "76331745", 2: "c7d03166", 1: "0a821348"}
ocr = RapidOCR()
data = json.load(open("ocr.json"))
for floor, f in FILES.items():
    im = Image.open(U + f + "-image.jpg").convert("RGB")
    W, H = im.size
    extra = []
    # 1) тайлы 3x3 с перекрытием, увеличенные в 2 раза
    for i in range(3):
        for j in range(3):
            x0, y0 = int(W * i / 3.6), int(H * j / 3.6)
            x1, y1 = min(W, x0 + int(W / 2.2)), min(H, y0 + int(H / 2.2))
            tile = im.crop((x0, y0, x1, y1)).resize(((x1 - x0) * 2, (y1 - y0) * 2))
            for box, text, sc in ocr(np.array(tile)[:, :, ::-1])[0] or []:
                cx = x0 + sum(p[0] for p in box) / 8; cy = y0 + sum(p[1] for p in box) / 8
                extra.append({"t": text, "s": round(float(sc), 2), "cx": cx, "cy": cy, "src": "tile"})
    # 2) повернутое на 90° (вертикальные подписи), в обе стороны
    for ang in (90, 270):
        r = im.rotate(ang, expand=True)
        for box, text, sc in ocr(np.array(r)[:, :, ::-1])[0] or []:
            rx = sum(p[0] for p in box) / 4; ry = sum(p[1] for p in box) / 4
            # обратно в координаты исходника
            if ang == 90:   # PIL rotate 90 = против часовой: (x,y)->(y, W-x)
                cx, cy = W - ry, rx
            else:
                cx, cy = ry, H - rx
            extra.append({"t": text, "s": round(float(sc), 2), "cx": cx, "cy": cy, "src": f"rot{ang}"})
    data[str(floor)] += extra
    print(floor, "extra", len(extra))
json.dump(data, open("ocr_all.json", "w"), ensure_ascii=False)
