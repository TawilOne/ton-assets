"""Для ноутбука (sync.py): какие выбранные группы «живые» — их кто-то открывал за последние ACTIVE_DAYS дней.
Запускается на сервере, печатает только коды групп (без имён и id пользователей).

    python3 active_groups.py"""
import datetime as dt
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ACTIVE_DAYS = 14


def load(name):
    try:
        return json.loads((HERE / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


users, chosen = load("users.json"), load("user_groups.json")
since = (dt.datetime.now() - dt.timedelta(days=ACTIVE_DAYS)).strftime("%Y-%m-%d")
print(json.dumps(sorted({str(code) for uid, code in chosen.items()
                         if code and str((users.get(uid) or {}).get("last", "")) >= since
                         and not (users.get(uid) or {}).get("blocked")}), ensure_ascii=False))
