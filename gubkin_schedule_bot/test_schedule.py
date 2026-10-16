"""Тесты без сети: python -m pytest test_schedule.py"""

import datetime as dt
import json

import pytest

from gubkin_api import GubkinClient, GubkinError, normalize_code
from schedule import fetch_range, parse_week, semester_bounds, to_csv, to_ics, to_json, to_text

WEEK = {
    "state": True,
    "rows": {
        "week": {"weekRussia": {"type": "upper", "days": [
            {"date": "05-10-2026", "weekDayNumber": 1},
            {"date": "06-10-2026", "weekDayNumber": 2},
        ]}},
        "organizations": [{
            "lessonsTimeChunks": ["08:45-09:30", "09:30-10:15", "10:30-11:15", "11:15-12:00"],
            "lessons": [
                {"weekDayNumber": 2, "timeChunks": [2, 3], "course": {"name": "Минералогия"},
                 "type": "Лабораторная", "rooms": [{"number": "512"}],
                 "teachers": [{"lastName": "Петров", "firstName": "Пётр", "middleName": "Петрович"}],
                 "groups": [{"id": 777, "code": "ГМ-26-11"}], "subgroup": 2,
                 "changes": {"rooms": [{"number": "514"}]}},
                {"weekDayNumber": 1, "timeChunks": [0, 1], "course": {"name": "Общая геология"},
                 "type": "Лекция", "rooms": [{"number": "1234"}],
                 "teachers": [{"lastName": "Иванов", "firstName": "Иван"}],
                 "groups": [{"id": 777}, {"id": 778}]},
                # тот же поток второй раз — должен схлопнуться
                {"weekDayNumber": 1, "timeChunks": [0, 1], "course": {"name": "Общая геология"},
                 "type": "Лекция", "rooms": [{"number": "1234"}],
                 "teachers": [{"lastName": "Иванов", "firstName": "Иван"}],
                 "groups": [{"id": 777}, {"id": 778}]},
                # чужая группа
                {"weekDayNumber": 1, "timeChunks": [2], "course": {"name": "Чужое"},
                 "groups": [{"id": 999}]},
                {"weekDayNumber": 2, "timeChunks": [0], "course": {"name": "Физкультура"},
                 "isCanceled": True, "groups": [{"id": 777}]},
            ],
        }],
    },
}


def test_normalize_code():
    assert normalize_code("гм - 26 - 11") == "ГМ-26-11"
    assert normalize_code("ГM–26–11") == "ГМ-26-11"  # латинская M и длинное тире


def test_parse_week():
    ls = parse_week(WEEK, 777, "ГМ-26-11")
    assert [l.subject for l in ls] == ["Общая геология", "Физкультура", "Минералогия"]
    geo, pe, mineral = ls
    assert (geo.date, geo.start, geo.end, geo.room, geo.week_type) == \
        ("2026-10-05", "08:45", "10:15", "1234", "верхняя")
    assert pe.cancelled
    assert mineral.room == "514" and mineral.subgroup == 2
    assert mineral.teacher == "Петров Пётр Петрович"
    assert mineral.notes == ["Аудитория: 512 → 514"]


def test_exports():
    ls = parse_week(WEEK, 777)
    assert len(json.loads(to_json(ls))["lessons"]) == 3
    assert to_csv(ls).count("\n") == 4
    ics = to_ics(ls, "ГМ-26-11")
    assert ics.count("BEGIN:VEVENT") == 3 and "DTSTART;TZID=Europe/Moscow:20261005T084500" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    assert "❌ ОТМЕНА" in to_text(ls)


def test_semester_bounds():
    assert semester_bounds(dt.date(2026, 10, 6)) == (dt.date(2026, 9, 1), dt.date(2027, 1, 31))
    assert semester_bounds(dt.date(2027, 1, 15)) == (dt.date(2026, 9, 1), dt.date(2027, 1, 31))
    assert semester_bounds(dt.date(2027, 3, 1)) == (dt.date(2027, 2, 1), dt.date(2027, 6, 30))


def test_fetch_range_and_find_group(monkeypatch):
    calls = []

    def fake_get(self, params):
        calls.append(params)
        if params.get("method") == "getFaculties":
            return {"rows": [{"id": 1, "name": "Факультет разработки"},
                             {"id": 2, "name": "Факультет геологии и геофизики нефти и газа"}]}
        if params.get("method") == "getFacultyGroups":
            return {"rows": [{"id": 777, "code": "ГМ-26-11"}] if params["facultyId"] == 2 else []}
        return WEEK if params["date"] == "5-10-2026" else {"state": True, "rows": {}}

    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    c = GubkinClient(delay=0)
    f, g = c.find_group("гм-26-11")
    assert g["id"] == 777 and f["id"] == 2
    assert calls[1]["facultyId"] == 2  # факультет геологии проверяется первым

    calls.clear()
    ls = fetch_range(c, 777, "ГМ-26-11", dt.date(2026, 10, 6), dt.date(2026, 10, 18))
    assert [p["date"] for p in calls] == ["5-10-2026", "12-10-2026"]
    assert [l.date for l in ls] == ["2026-10-06", "2026-10-06"]  # 05.10 вне диапазона


def test_week_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(GubkinClient, "_get", lambda self, p: calls.append(p) or WEEK)
    c = GubkinClient(delay=0)
    mon = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    c.week(mon, 777)
    c.week(mon + dt.timedelta(days=2), 777)  # та же неделя — из кэша
    assert len(calls) == 1
    c.cache_ttl = 0                          # текущая неделя устарела — качаем заново
    c.week(mon + dt.timedelta(days=2), 777)
    assert len(calls) == 2
    c.week(mon - dt.timedelta(days=7), 777)  # прошлая неделя
    c.week(mon - dt.timedelta(days=7), 777)  # ...помнится дольше, даже при cache_ttl=0
    assert len(calls) == 3


def test_cookies_survive_restart(tmp_path):
    f = tmp_path / "cookies.json"
    c = GubkinClient(cookie_file=str(f))
    c.s.cookies.set("PHPSESSID", "abc", domain="lk.gubkin.ru", path="/")
    c._save_cookies()
    c2 = GubkinClient(cookie_file=str(f))
    assert c2.s.cookies.get("PHPSESSID") == "abc"


def test_breaks_in_text():
    from schedule import Lesson, break_text
    day = dict(date="2026-10-05", weekday="Понедельник")
    ls = [Lesson(start="08:45", end="10:15", subject="А", **day),
          Lesson(start="10:30", end="12:00", subject="Б", **day),
          Lesson(start="14:00", end="15:30", subject="В", **day)]
    t = to_text(ls)
    assert "☕ перемена 15 мин" in t
    assert "🕐 окно 2 ч" in t
    assert break_text(90) == "  🕐 окно 1 ч 30 мин"
    # подгруппы в одно время — перемены нет
    same = [Lesson(start="08:45", end="10:15", subject="А", subgroup=1, **day),
            Lesson(start="08:45", end="10:15", subject="Б", subgroup=2, **day)]
    assert "перемена" not in to_text(same)


def test_html_view():
    import re
    from schedule import Lesson, short_name, to_html
    assert short_name("Петров Пётр Петрович, Иванов Иван") == "Петров П. П., Иванов И."
    day = dict(date="2026-10-12", weekday="Понедельник", week_type="верхняя")
    ls = [Lesson(start="08:45", end="10:15", subject="Геология <b>&", kind="Лекция", **day),
          Lesson(start="10:30", end="12:00", subject="Минералогия", kind="Лабораторная", **day)]
    h = to_html(ls, today=dt.date(2026, 10, 12))
    assert "сегодня" in h and "☕ перемена 15 мин" in h and "🔬" in h
    assert "Геология &lt;b&gt;&amp;" in h          # спецсимволы экранированы
    for line in h.split("\n"):                     # теги не переходят через перенос строки
        assert len(re.findall(r"<(\w+)>", line)) == len(re.findall(r"</(\w+)>", line)), line


def test_stale_cache_when_site_down(monkeypatch, tmp_path):
    from gubkin_api import GubkinError
    mon = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    f = tmp_path / "cache.json"
    monkeypatch.setattr(GubkinClient, "_get", lambda self, p: WEEK)
    GubkinClient(delay=0, cache_file=str(f)).week(mon, 777)   # сохранили на диск

    def down(self, p):
        raise GubkinError("Read timed out")
    monkeypatch.setattr(GubkinClient, "_get", down)
    c = GubkinClient(delay=0, cache_file=str(f), cache_ttl=0)  # «перезапуск», сайт лежит
    assert c.week(mon, 777) == WEEK and c.stale
    import pytest
    with pytest.raises(GubkinError):                           # без кэша — честная ошибка
        GubkinClient(delay=0).week(mon, 777)


def test_offline_reads_uploaded_cache(tmp_path):
    """Сервер в offline-режиме не ходит на сайт и перечитывает cache.json, когда ноутбук его обновил."""
    import os
    import time
    cf = tmp_path / "cache.json"
    mon = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    cf.write_text(json.dumps({f"{mon.isoformat()}|1": [time.time(), WEEK]}), encoding="utf-8")
    c = GubkinClient(cache_file=str(cf), offline=True)
    c._get = lambda *a, **k: pytest.fail("offline-клиент не должен ходить на сайт")
    assert c.week(dt.date.today(), 1) == WEEK and not c.stale
    with pytest.raises(GubkinError):
        c.week(mon + dt.timedelta(weeks=3), 1)
    cf.write_text(json.dumps({f"{(mon + dt.timedelta(weeks=3)).isoformat()}|1": [time.time(), WEEK]}),
                  encoding="utf-8")
    os.utime(cf, (time.time() - 40 * 3600,) * 2)  # прислано давно — бот предупредит
    assert c.week(mon + dt.timedelta(weeks=3), 1) == WEEK and c.stale


def test_times_without_leading_zero_sorted():
    week = json.loads(json.dumps(WEEK))
    org = week["rows"]["organizations"][0]
    org["lessonsTimeChunks"] = ["8:30-9:15", "9:15-10:00", "10:15-11:00", "11:00-11:45"]
    ls = parse_week(week, 777, "ГМ-26-11")
    assert [(l.date, l.start, l.end) for l in ls] == sorted((l.date, l.start, l.end) for l in ls)
    assert ls[0].start == "08:30" and ls[0].end == "10:00"
    tue = [l for l in ls if l.date == "2026-10-06"]
    assert [l.start for l in tue] == ["08:30", "10:15"]  # раньше «10:15» шло раньше «8:30»: сортировка была по тексту


def test_cache_survives_power_loss_during_write(tmp_path, monkeypatch):
    """Ноутбук выключился посреди записи cache.json — остаётся прежний целый файл."""
    import gubkin_api
    cache = tmp_path / "cache.json"
    c = GubkinClient(cache_file=str(cache))
    c._cache[(dt.date(2026, 10, 5), "777")] = (1.0, WEEK)
    c._save_cache()
    before = cache.read_text(encoding="utf-8")

    def crash(*a, **kw):
        raise OSError("питание пропало")
    monkeypatch.setattr(gubkin_api.os, "replace", crash)
    c._cache[(dt.date(2026, 10, 12), "777")] = (2.0, WEEK)
    c._save_cache()  # запись «оборвалась» до подмены файла
    assert cache.read_text(encoding="utf-8") == before
    assert len(GubkinClient(cache_file=str(cache), offline=True)._cache) == 1
