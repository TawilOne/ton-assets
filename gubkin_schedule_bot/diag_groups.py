"""Проверка подсчёта «Факультеты» в /users: запускать на сервере.

    python3 diag_groups.py

Печатает только цифры и коды групп — без имён и id пользователей."""
import collections
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent


def load(name):
    try:
        return json.loads((HERE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"{name}: не прочитать ({e})")
        return {}


users, chosen, groups = load("users.json"), load("user_groups.json"), load("groups.json")
fac_of = {}
for f in groups.get("faculties") or []:
    for g in f.get("groups") or []:
        fac_of.setdefault(str(g.get("code")), f.get("key"))
print("пользователей:", len(users))
print("выбрали группу (всего записей):", len(chosen), "| из них есть в users.json:", sum(1 for u in chosen if u in users))
print("факультетов в groups.json:", len(groups.get("faculties") or []), "| групп:", len(fac_of))
by = collections.Counter(fac_of.get(chosen.get(u, ""), "НЕТ") if chosen.get(u) else "без группы" for u in users)
print("по факультетам:", dict(by))
lost = collections.Counter(c for u, c in chosen.items() if u in users and c not in fac_of)
print("выбранные группы, которых нет в groups.json:", dict(lost.most_common(15)))
