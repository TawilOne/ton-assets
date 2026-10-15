"""Поиск пар по номеру кабинета в сохранённом расписании (cache.json) — без запросов к сайту.

    python find_room.py 17      — все пары в кабинетах 17xx (2 корпус, 7 этаж)
    python find_room.py 1715    — пары в кабинете 1715
    python find_room.py         — сводка: сколько пар в каком корпусе и на каком этаже
"""
import collections
import os
import sys

os.environ["GUBKIN_OFFLINE"] = "1"   # только сохранённое расписание, на сайт не ходим

import bot  # noqa: E402
from schedule import locate_room, semester_bounds  # noqa: E402


def main() -> None:
    start, end = semester_bounds(bot._today())
    lessons, _ = bot._fetch(start, end)
    prefix = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    if not prefix:
        places = collections.Counter(locate_room(l.room) or (l.room or "без кабинета") for l in lessons)
        print(f"Семестр {start:%d.%m.%Y}–{end:%d.%m.%Y}, пар: {len(lessons)}\n")
        for place, n in places.most_common():
            print(f"  {n:4}  {place}")
        return
    found = [l for l in lessons if (l.room or "").strip().startswith(prefix)]
    if not found:
        print(f"Пар в кабинетах «{prefix}…» нет (семестр {start:%d.%m.%Y}–{end:%d.%m.%Y}).")
        return
    print(f"Пар в кабинетах «{prefix}…»: {len(found)}\n")
    for l in sorted(found, key=lambda l: (l.date, l.start)):
        print(f"  {l.date} {l.start}  {l.room:8} {l.subject} ({l.kind})  — {locate_room(l.room)}")


if __name__ == "__main__":
    main()
