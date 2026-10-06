#!/usr/bin/env python3
"""Выгрузка всего расписания группы РГУ им. Губкина в файлы (без Telegram).

    python export.py                         # ГМ-26-11, весь текущий семестр
    python export.py --group ГМ-26-11 --from 2026-09-01 --to 2027-01-31 --out out/
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

from gubkin_api import CaptchaRequired, GubkinClient
from schedule import fetch_range, semester_bounds, to_csv, to_ics, to_json, to_text

DEFAULT_GROUP = "ГМ-26-11"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--group", default=DEFAULT_GROUP, help="код группы (по умолчанию %(default)s)")
    p.add_argument("--group-id", help="id группы на сайте, если уже известен (пропускает поиск)")
    p.add_argument("--faculty", default="геолог", help="подстрока названия факультета для поиска")
    p.add_argument("--from", dest="start", type=dt.date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--to", dest="end", type=dt.date.fromisoformat, help="YYYY-MM-DD")
    p.add_argument("--out", default="out", help="папка для файлов")
    a = p.parse_args()

    s0, e0 = semester_bounds()
    start, end = a.start or s0, a.end or e0
    client = GubkinClient()
    try:
        if a.group_id:
            gid, code, fac = a.group_id, a.group, ""
        else:
            f, g = client.find_group(a.group, a.faculty)
            gid, code, fac = g["id"], g.get("code") or a.group, f.get("name", "")
            print(f"Группа {code} (id={gid}), факультет: {fac}", file=sys.stderr)
        lessons = fetch_range(client, gid, code, start, end,
                              progress=lambda i, n: print(f"\rнеделя {i}/{n}", end="", file=sys.stderr))
        print(file=sys.stderr)
    except CaptchaRequired:
        print("Сайт попросил капчу. Откройте https://lk.gubkin.ru/schedule/ в браузере, "
              "введите её и повторите позже (или воспользуйтесь Telegram-ботом — он умеет капчу).",
              file=sys.stderr)
        return 2

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{code}_{start}_{end}"
    meta = {"group": code, "group_id": gid, "faculty": fac, "from": str(start), "to": str(end)}
    (out / f"{stem}.json").write_text(to_json(lessons, meta), encoding="utf-8")
    (out / f"{stem}.csv").write_text(to_csv(lessons), encoding="utf-8")
    (out / f"{stem}.ics").write_text(to_ics(lessons, f"Расписание {code}"), encoding="utf-8", newline="")
    (out / f"{stem}.txt").write_text(to_text(lessons), encoding="utf-8")
    print(f"Занятий: {len(lessons)}. Файлы: {out.resolve()}/{stem}.{{json,csv,ics,txt}}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
