"""Из фото плана эвакуации делает «чистую» карту: только стены, светлым на прозрачном фоне.
Цветные значки (стрелки, огнетушители) и текст убираются."""
from PIL import Image, ImageFilter, ImageChops, ImageDraw
import numpy as np


def extract_walls(im: Image.Image, text_boxes, delta=38, sat_max=34, thin=False):
    rgb = np.asarray(im.convert("RGB")).astype(np.int16)
    gray = rgb.mean(axis=2)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    # «тёмнее окрестности» — устойчиво к бликам и неравномерному свету
    local = np.asarray(Image.fromarray(gray.astype(np.uint8)).filter(ImageFilter.BoxBlur(25))).astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    greenish = (g - r > 10) | (g - b > 18)      # стрелки и знаки эвакуации
    reddish = (r - g > 22)                        # огнетушители, извещатели
    bluish = (b - g > 12)                         # «связь с охраной», точка «вы здесь»
    dark = (local - gray > delta) & (sat < sat_max) & ~greenish & ~reddish & ~bluish
    mask = Image.fromarray((dark * 255).astype(np.uint8))
    # убираем текст (номера, надписи) по рамкам от OCR
    d = ImageDraw.Draw(mask)
    for (x0, y0, x1, y1) in text_boxes:
        d.rectangle((x0 - 3, y0 - 3, x1 + 3, y1 + 3), fill=0)
    # убираем мелкий мусор: точки, остатки букв и контуры значков
    if thin:  # тонкие линии (план снят издалека): не «съедаем» их, а наоборот чуть утолщаем
        mask = mask.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.MaxFilter(3))
    else:
        mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    arr = np.asarray(mask) > 0
    arr = remove_small(drop_border(arr), min_size=int(arr.size * 0.00004))
    return arr


def drop_border(arr, pad=4):
    """Убирает всё, что касается края кадра (рамка картины, обрезанные таблицы)."""
    h, w = arr.shape
    seen = np.zeros_like(arr, dtype=bool)
    out = arr.copy()
    edge = [(y, x) for y in range(h) for x in (0, w - 1)] + [(y, x) for x in range(w) for y in (0, h - 1)]
    border = np.zeros_like(arr, dtype=bool)
    border[:pad, :] = border[-pad:, :] = border[:, :pad] = border[:, -pad:] = True
    ys, xs = np.nonzero(arr & border)
    stack = list(zip(ys, xs))
    for y, x in stack:
        seen[y, x] = True
    while stack:
        y, x = stack.pop(); out[y, x] = False
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and arr[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True; stack.append((ny, nx))
    return out


def remove_small(arr, min_size):
    """Удаляет связные области меньше min_size пикселей (BFS по 8-соседям, без scipy)."""
    h, w = arr.shape
    seen = np.zeros_like(arr, dtype=bool)
    out = np.zeros_like(arr, dtype=bool)
    ys, xs = np.nonzero(arr)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]; seen[y0, x0] = True; comp = []
        while stack:
            y, x = stack.pop(); comp.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and arr[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True; stack.append((ny, nx))
        if len(comp) >= min_size:
            for y, x in comp:
                out[y, x] = True
    return out


def render_png(walls: np.ndarray, color=(232, 232, 238)):
    """Стены — светлым, всё остальное прозрачное (фон рисует страница)."""
    h, w = walls.shape
    a = (walls * 255).astype(np.uint8)
    alpha = Image.fromarray(a).filter(ImageFilter.GaussianBlur(0.6))
    img = Image.new("RGBA", (w, h), color + (0,))
    img.putalpha(alpha)
    return img
