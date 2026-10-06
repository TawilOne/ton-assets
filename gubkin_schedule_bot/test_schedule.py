"""Тесты без сети: python -m pytest test_schedule.py"""

import datetime as dt
import json

from gubkin_api import GubkinClient, normalize_code
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
