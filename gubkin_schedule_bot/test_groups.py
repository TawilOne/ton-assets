"""Тесты выбора группы и расписания всего факультета, без сети и без Telegram:
python -m pytest test_groups.py"""

import asyncio
import os
import base64
import copy
import datetime as dt
import json
import pathlib
import time
import zlib

import pytest

import bot
import i18n
import sync
from gubkin_api import GubkinClient
from schedule import fetch_range, parse_week, slim_week
from test_schedule import WEEK

TODAY = bot._today()
MONDAY = TODAY - dt.timedelta(days=TODAY.weekday())


def week_for(gid: str, code: str, subject: str) -> dict:
    """Неделя с одной парой сегодня, как отвечает сайт (с лишними полями)."""
    days = [{"date": (MONDAY + dt.timedelta(days=i)).strftime("%d-%m-%Y"), "weekDayNumber": i + 1, "extra": "x"}
            for i in range(7)]
    lesson = {"weekDayNumber": TODAY.weekday() + 1, "timeChunks": [0, 1], "course": {"name": subject, "id": 5},
              "type": "Лекция", "rooms": [{"number": "421", "building": {"name": "x" * 50}}],
              "teachers": [{"lastName": "Иванов", "firstName": "Иван", "email": "a@b.c", "photo": "y" * 80}],
              "groups": [{"id": int(gid), "code": code, "faculty": {"name": "z" * 60}}], "subgroup": 0,
              "isCanceled": False, "comment": None}
    return {"state": True, "rows": {"week": {"weekRussia": {"type": "upper", "days": days}, "number": 7},
                                    "organizations": [{"lessonsTimeChunks": ["10:45-11:30", "11:30-12:15"],
                                                       "lessons": [lesson], "name": "РГУ"}]}}


GROUPS = [{"id": "10706", "code": "ГМ-26-11"}, {"id": "10707", "code": "ГМ-26-12"},
          {"id": "10600", "code": "ГГ-25-01"}, {"id": "10500", "code": "ГИ-22-03"},
          {"id": "10800", "code": "Аспиранты"}]
AIVT = [{"id": "20001", "code": "АА-26-01"}, {"id": "20002", "code": "АА-26-02"}, {"id": "20003", "code": "АС-25-01"}]
GROUPS_JSON = {"faculties": [
    {"key": "geo", "id": 7, "name": "Факультет геологии и геофизики нефти и газа", "groups": GROUPS},
    {"key": "aivt", "id": 9, "name": "Факультет автоматики и вычислительной техники", "groups": AIVT}],
    "time": time.time(), "tried": ["geo", "aivt", "chem", "econ"]}


class Msg:
    def __init__(self, text=""):
        self.text = text
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append((text, kw))
        return self

    async def edit_text(self, text, **kw):
        self.sent.append((text, kw))


class Query:
    def __init__(self, data):
        self.data = data
        self.edited = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edited.append((text, reply_markup))


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def _noop(*a, **kw):
    pass


def update(uid=1, text="", data=None):
    user = Obj(id=uid, full_name="Тест", username="", is_bot=False)
    return Obj(effective_user=user, effective_message=Msg(text), effective_chat=Obj(id=uid, send_action=_noop),
               callback_query=Query(data) if data else None)


def ctx(args=None):
    return Obj(args=args or [], user_data={}, bot_data={})


def run(coro):
    return asyncio.run(coro)


def buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Сервер в offline-режиме: groups.json и cache.json прислал ноутбук; есть расписание двух групп."""
    cache = {f"{MONDAY.isoformat()}|10706": [time.time(), slim_week(week_for("10706", "ГМ-26-11", "Геология"))],
             f"{MONDAY.isoformat()}|10707": [time.time(), slim_week(week_for("10707", "ГМ-26-12", "Физика"))],
             f"{MONDAY.isoformat()}|20001": [time.time(), slim_week(week_for("20001", "АА-26-01", "Программирование"))]}
    (tmp_path / "cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "groups.json").write_text(json.dumps(GROUPS_JSON, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "group.json").write_text(json.dumps({"id": "10706", "code": "ГМ-26-11", "code_wanted": "ГМ-26-11"},
                                                    ensure_ascii=False), encoding="utf-8")
    for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE", "USERS_FILE", "SUBSCRIBERS_FILE"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    monkeypatch.setattr(i18n, "LANG_FILE", tmp_path / "lang.json")
    monkeypatch.setattr(i18n, "_langs", None)
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    monkeypatch.setattr(bot, "_group", {"id": None, "code": bot.GROUP_CODE, "faculty": ""})
    client = GubkinClient(cache_file=str(tmp_path / "cache.json"), offline=True)
    client.slim = slim_week
    monkeypatch.setattr(bot, "client", client)
    return tmp_path


def test_slim_week_keeps_everything_parse_week_needs():
    raw = copy.deepcopy(WEEK)
    raw["rows"]["organizations"][0]["lessons"][0]["teachers"][0]["photo"] = "x" * 500
    slim = slim_week(raw)
    for gid, code in ((777, "ГМ-26-11"), (778, ""), (999, "")):
        assert parse_week(slim, gid, code) == parse_week(raw, gid, code)
    assert slim_week(slim) == slim  # повторное ужатие ничего не меняет
    assert len(json.dumps(slim)) < len(json.dumps(raw))
    big = week_for("10706", "ГМ-26-11", "Геология")
    assert parse_week(slim_week(big), "10706", "ГМ-26-11") == parse_week(big, "10706", "ГМ-26-11")
    assert len(json.dumps(slim_week(big))) < len(json.dumps(big)) * 0.7


def test_long_range_skips_missing_weeks(tmp_path):
    client = GubkinClient(cache_file=str(tmp_path / "c.json"), offline=True)
    client._cache[(MONDAY, "10706")] = (time.time(), week_for("10706", "ГМ-26-11", "Геология"))
    client._save_cache()
    got = fetch_range(client, "10706", "ГМ-26-11", MONDAY - dt.timedelta(weeks=5), MONDAY + dt.timedelta(days=6))
    assert [l.subject for l in got] == ["Геология"]
    with pytest.raises(Exception):  # один день без данных — по-прежнему ошибка
        fetch_range(client, "10706", "ГМ-26-11", MONDAY + dt.timedelta(weeks=1), MONDAY + dt.timedelta(weeks=1))


def test_groups_list_and_years(env):
    codes = [g["code"] for g in bot._faculty_groups()]
    assert codes == sorted(codes) and "ГМ-26-12" in codes
    assert bot._find_group("гм - 26 - 12")["id"] == "10707"
    assert bot._find_group("ГM-26-12")["id"] == "10707"  # латинская M
    assert bot._find_group("ГМ-99-99") is None
    first = (TODAY.year if TODAY.month >= 8 else TODAY.year - 1) % 100
    assert bot._year_label(first, "ru") == "1 курс"
    assert bot._year_label(first - 1, "en") == "Year 2"
    assert bot._year_label(first, "fr") == "1re année" and bot._year_label(first - 2, "fr") == "3e année"
    assert bot._year_label(None, "ru") == "Другие"
    text, markup = bot._picker("ru")
    assert text.startswith("🏛 Выберите факультет") and buttons(markup) == [
        ("⛏ Геологии и геофизики", "grp:f:geo"), ("💻 Автоматики и ВТ", "grp:f:aivt")]
    text, markup = bot._picker("ru", "geo")
    labels = [t for t, _ in buttons(markup)]
    assert text == "⛏ Геологии и геофизики\n👥 Выберите курс:" and labels[-2:] == ["Другие", "⬅️ Назад"]
    labels = labels[:-1]
    assert labels == sorted(labels[:-1], key=lambda s: int(s.split()[0]) if s[0].isdigit() else 99) + ["Другие"]
    # группы одного факультета не попадают в другой
    text, markup = bot._picker("ru", "aivt", str(first))
    assert [t for t, c in buttons(markup) if c.startswith("grp:s:")] == ["АА-26-01", "АА-26-02"]
    assert ("⬅️ Назад", "grp:f:aivt") in buttons(markup)


def test_old_groups_file_one_faculty(env):
    bot.GROUPS_FILE.write_text(json.dumps({"groups": GROUPS, "time": time.time()}, ensure_ascii=False),
                               encoding="utf-8")
    assert {g["fac"] for g in bot._faculty_groups()} == {"geo"}
    text, markup = bot._picker("ru")  # факультет один — сразу курсы, без кнопки «Назад»
    assert text.startswith("👥 Выберите курс") and "⬅️ Назад" not in [t for t, _ in buttons(markup)]
    u = update(uid=5, data="grp:y:26")  # кнопка из сообщения, отправленного до появления факультетов
    run(bot.on_group(u, ctx()))
    assert "ГМ-26-12" in [t for t, _ in buttons(u.callback_query.edited[0][1])]


def test_default_group_without_groups_list(env):
    bot.GROUPS_FILE.unlink()
    groups = bot._faculty_groups()
    assert groups == [{"id": "10706", "code": "ГМ-26-11", "fac": "geo"}]
    text, markup = bot._picker("ru")
    assert "ещё загружается" in text and buttons(markup) == [("ГМ-26-11", "grp:s:ГМ-26-11")]


def test_new_user_picks_group_and_gets_schedule(env):
    u = update(uid=5)
    run(bot.cmd_today(u, ctx()))  # группа не выбрана — бот просит выбрать
    assert "Сначала выберите свою группу" in u.effective_message.sent[0][0]

    u = update(uid=5)
    run(bot.cmd_start(u, ctx()))
    assert "Группа не выбрана" in u.effective_message.sent[0][0]
    assert "/games — мини-игры" in u.effective_message.sent[0][0]
    fac_buttons = buttons(u.effective_message.sent[1][1]["reply_markup"])
    assert "Выберите факультет" in u.effective_message.sent[1][0]
    cb = next(c for t, c in fac_buttons if "Геологии" in t)

    u = update(uid=5, data=cb)
    run(bot.on_group(u, ctx()))
    text, markup = u.callback_query.edited[0]
    assert "Выберите курс" in text
    cb = next(c for t, c in buttons(markup) if t == "1 курс")

    u = update(uid=5, data=cb)
    run(bot.on_group(u, ctx()))
    text, markup = u.callback_query.edited[0]
    assert ("ГМ-26-12", "grp:s:ГМ-26-12") in buttons(markup) and ("⬅️ Назад", "grp:f:geo") in buttons(markup)
    assert all(t.startswith("ГМ-26") for t, c in buttons(markup) if c.startswith("grp:s:"))
    u = update(uid=5, data="grp:b")  # назад к факультетам
    run(bot.on_group(u, ctx()))
    assert "Выберите факультет" in u.callback_query.edited[0][0]

    u = update(uid=5, data="grp:s:ГМ-26-12")
    run(bot.on_group(u, ctx()))
    assert bot._load_user_groups() == {"5": "ГМ-26-12"}
    reply, kw = u.effective_message.sent[0]
    assert "Ваша группа: ГМ-26-12" in reply and "ещё загружается" not in reply
    assert "👥 Моя группа" in [b.text for row in kw["reply_markup"].keyboard for b in row]

    u = update(uid=5)
    run(bot.cmd_today(u, ctx()))
    assert "Физика" in u.effective_message.sent[-1][0] and "Геология" not in u.effective_message.sent[-1][0]


def test_typed_code_and_pending_group(env):
    u = update(uid=6, text="гг-25-01")
    run(bot.on_text(u, ctx()))
    assert bot._load_user_groups()["6"] == "ГГ-25-01"
    assert "ещё загружается" in u.effective_message.sent[0][0]  # ноутбук ещё не прислал её расписание
    u = update(uid=6)
    run(bot.cmd_today(u, ctx()))
    assert "Расписание группы ГГ-25-01 ещё загружается" in u.effective_message.sent[0][0]

    u = update(uid=6)
    run(bot.cmd_group(u, ctx(["ГМ-99-99"])))
    assert "Не нашёл группу ГМ-99-99" in u.effective_message.sent[0][0]
    assert bot._load_user_groups()["6"] == "ГГ-25-01"
    u = update(uid=6)
    run(bot.cmd_group(u, ctx(["ГМ-26-11"])))
    assert bot._load_user_groups()["6"] == "ГМ-26-11"


def test_old_users_keep_their_group(env):
    bot.USERS_FILE.write_text(json.dumps({"1": {"name": "a"}, "2": {"name": "b"}}), encoding="utf-8")
    bot.SUBSCRIBERS_FILE.write_text(json.dumps({"3": {"time": "07:00"}}), encoding="utf-8")
    bot._migrate_user_groups()
    assert bot._load_user_groups() == {"1": "ГМ-26-11", "2": "ГМ-26-11", "3": "ГМ-26-11"}
    bot._set_user_group(2, "ГМ-26-12")
    bot._migrate_user_groups()  # второй запуск ничего не трогает
    assert bot._load_user_groups()["2"] == "ГМ-26-12"
    u = update(uid=1)
    run(bot.cmd_today(u, ctx()))
    assert "Геология" in u.effective_message.sent[-1][0]


def test_buttons_and_settings(env):
    assert bot.BUTTON_ACTIONS["👥 Моя группа"] == "cmd_group"
    assert bot.BUTTON_ACTIONS["👥 My group"] == bot.BUTTON_ACTIONS["👥 Mon groupe"] == "cmd_group"
    assert bot.BUTTON_ACTIONS["⏰ Рассылка расписания"] == "cmd_digest"  # старая панель кнопок
    rows = [[b.text for b in row] for row in bot.keyboard("ru").keyboard]
    assert rows[-1] == ["🗺 Где пара"] and ["⏰ Рассылка", "👥 Моя группа"] in rows
    bot._set_user_group(7, "ГМ-26-12")
    assert "👥 Группа: ГМ-26-12" in bot._settings_text("ru", 7)
    assert ("👥 Сменить группу", "grp:menu") in buttons(bot._settings_markup("ru", 7))
    for lang in i18n.LANGS:
        assert "/group" in bot.help_text(lang, None)
        assert all(t in i18n.TEXT[k] for k in i18n.TEXT if k.startswith("group") for t in i18n.LANGS)


def test_digest_per_group(env, monkeypatch):
    monkeypatch.setattr(bot, "EVENING", 24 * 60 + 1)  # в тестовом кэше только сегодняшние пары
    now = dt.datetime.now(bot.MSK)
    hhmm = f"{now:%H:%M}"
    bot._set_user_group(1, "ГМ-26-11")
    bot._set_user_group(2, "ГМ-26-12")
    bot._set_user_group(3, "ГГ-25-01")  # расписания ещё нет — не падаем, остальным присылаем
    bot._save_subs({"1": {"time": hhmm}, "2": {"time": hhmm}, "3": {"time": hhmm}, "4": {"time": hhmm}})
    sent = {}

    async def send_message(chat, text, **kw):
        sent[chat] = text
    run(bot.digest_job(Obj(bot=Obj(send_message=send_message))))
    assert "Геология" in sent[1] and "Физика" in sent[2] and 3 not in sent and 4 not in sent
    subs = bot._load_subs()
    assert subs["1"]["last"] == subs["2"]["last"] == now.date().isoformat()
    assert not subs["3"].get("last")  # повторим, когда ноутбук пришлёт расписание


def test_sync_plan_order(monkeypatch):
    monkeypatch.setattr(bot, "client", GubkinClient())
    weeks = sync.mondays(TODAY)
    groups = [{"id": str(i), "code": c} for i, c in enumerate(["А-26-01", "Б-26-02", "В-25-01"])]
    tuesday, saturday = MONDAY + dt.timedelta(days=1), MONDAY + dt.timedelta(days=5)
    tasks = sync.plan(groups, {"Б-26-02"}, weeks, MONDAY, today=tuesday)
    first = [(m, g["code"]) for m, g, _ in tasks[:3]]
    assert first == [(MONDAY + dt.timedelta(weeks=k), "Б-26-02") for k in range(3)]
    # остальные группы — одна неделя: в будни текущая...
    others = [(m, g["code"]) for m, g, chosen in tasks if not chosen]
    assert others == [(MONDAY, "А-26-01"), (MONDAY, "В-25-01")]
    # ...а с пятницы — следующая (её и будут смотреть)
    tasks_sat = sync.plan(groups, {"Б-26-02"}, weeks, MONDAY, today=saturday)
    nxt = MONDAY + dt.timedelta(weeks=1)
    assert [(m, g["code"]) for m, g, c in tasks_sat if not c] == [(nxt, "А-26-01"), (nxt, "В-25-01")]
    rest = [m for m, g, chosen in tasks[3:] if chosen]  # дальше — только будущие недели, прошедшие не качаем
    assert rest == [m for m in weeks if m > MONDAY + dt.timedelta(weeks=2)] and all(g["code"] == "Б-26-02" for _, g, c in tasks if c)
    # всё свежее — остальные группы не качаем, у выбранной — только ближние недели
    for m in weeks:
        for g in groups:
            bot.client._cache[(m, g["id"])] = (time.time() - 2 * 3600, {})
    tasks = sync.plan(groups, {"Б-26-02"}, weeks, MONDAY, today=tuesday)
    assert [(m, g["code"]) for m, g, _ in tasks] == [(MONDAY + dt.timedelta(weeks=k), "Б-26-02") for k in range(3)]
    # только что скачанные ближние недели повторный запуск не перекачивает
    for k in range(3):
        bot.client._cache[(MONDAY + dt.timedelta(weeks=k), "1")] = (time.time() - 60, {})
    assert sync.plan(groups, {"Б-26-02"}, weeks, MONDAY, today=tuesday) == []
    for m in weeks:
        for g in groups:
            bot.client._cache[(m, g["id"])] = (time.time(), {})
    # дальние недели выбранной группы — раз в 3 дня: через сутки ещё не качаем, через 3 дня — качаем
    far = [m for m in weeks if m > MONDAY + dt.timedelta(weeks=2)]
    for m in far:
        bot.client._cache[(m, "1")] = (time.time() - 30 * 3600, {})
    assert not [m for m, g, _ in sync.plan(groups, {"Б-26-02"}, weeks, MONDAY, today=tuesday) if m in far]
    for m in far:
        bot.client._cache[(m, "1")] = (time.time() - 3 * 86400, {})
    soon = [m for m in far if m <= MONDAY + dt.timedelta(weeks=sync.FAR_WEEKS)]
    assert [m for m, g, _ in sync.plan(groups, {"Б-26-02"}, weeks, MONDAY, today=tuesday) if m in far] == soon


def test_sync_plan_limits_other_groups(monkeypatch):
    monkeypatch.setattr(bot, "client", GubkinClient())
    weeks = sync.mondays(TODAY)
    groups = [{"id": str(i), "code": f"Г-26-{i:02d}"} for i in range(5)]
    bot.client._cache[(MONDAY, "0")] = (time.time() - 30 * 3600, {})  # есть, но старая
    bot.client._cache[(MONDAY, "1")] = (time.time() - 50 * 3600, {})  # ещё старее
    bot.client._cache[(MONDAY, "2")] = (time.time(), {})              # свежая — не нужна
    tuesday = MONDAY + dt.timedelta(days=1)
    tasks = sync.plan(groups, set(), weeks, MONDAY, others_limit=3, today=tuesday)
    assert [(m, g["code"]) for m, g, _ in tasks] == [
        (MONDAY, "Г-26-03"), (MONDAY, "Г-26-04"),  # сначала тех, которых нет совсем
        (MONDAY, "Г-26-01")]                         # потом самую старую
    tasks = sync.plan(groups, set(), weeks, MONDAY, today=tuesday)
    assert [(m, g["code"]) for m, g, _ in tasks][-2:] == [(MONDAY, "Г-26-01"), (MONDAY, "Г-26-00")]


def test_sync_end_to_end(tmp_path, monkeypatch):
    """Весь sync с поддельным сайтом и сервером: список групп, расписание, ужатый кэш, отправка."""
    from gubkin_api import CaptchaRequired
    calls = []

    def fake_get(self, params):
        calls.append(params)
        if params.get("method") == "getFaculties":
            return {"state": True, "rows": [{"id": 1, "name": "Факультет разработки"},
                                            {"id": 7, "name": "Факультет геологии и геофизики нефти и газа"},
                                            {"id": 9, "name": "Факультет автоматики и вычислительной техники"}]}
        if params.get("method") == "getFacultyGroups":
            rows = {7: GROUPS, 9: AIVT}.get(params["facultyId"], [])
            return {"state": True, "rows": [{"id": int(g["id"]), "code": g["code"]} for g in rows]}
        if captcha_after[0] and sum(1 for c in calls if c.get("act") == "schedule") > captcha_after[0]:
            raise CaptchaRequired("капча")
        gid = str(params["groupId"])
        code = next(g["code"] for g in GROUPS + AIVT if g["id"] == gid)
        return week_for(gid, code, "Предмет " + code)

    captcha_after = [0]
    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    client = GubkinClient(cache_file=str(tmp_path / "cache.json"), cookie_file=str(tmp_path / "cookies.json"))
    client.slim = slim_week
    monkeypatch.setattr(bot, "client", client)
    monkeypatch.setattr(bot, "_group", {"id": None, "code": bot.GROUP_CODE, "faculty": ""})
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    monkeypatch.setattr(sync, "STATUS", tmp_path / "sync_status.json")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    monkeypatch.setattr(sync, "FAILED", tmp_path / "sync_failed.json")
    monkeypatch.setattr(sync, "LOCK", tmp_path / "sync.lock")
    monkeypatch.setattr(sync, "server", lambda: "root@test")
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    pushed = {}
    monkeypatch.setattr(sync, "put", lambda srv, name, data: pushed.__setitem__(name, data))

    class R:
        returncode = 0
        stdout = json.dumps({"1": "ГМ-26-11", "2": "ГМ-26-12", "3": "АА-26-01"}, ensure_ascii=False).encode()
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())

    assert sync.run(interactive=False) == 0
    groups = json.loads(pushed["groups.json"])
    assert [(f["key"], f["id"], len(f["groups"])) for f in groups["faculties"]] == [
        ("geo", 7, len(GROUPS)), ("aivt", 9, len(AIVT))]
    cache = json.loads(pushed["cache.json"])
    weeks = sync.mondays(dt.date.today())
    chosen = {k for k in cache if k.split("|")[1] in ("10706", "10707", "20001")}
    assert len(chosen) == 3 * len([m for m in weeks if m >= MONDAY])  # выбранные группы — семестр с этой недели
    others = {k.split("|")[1] for k in cache} - {"10706", "10707", "20001"}
    assert others == {"10600", "10500", "10800", "20002", "20003"}
    assert all(len([k for k in cache if k.endswith("|" + g)]) == 1 for g in others)  # остальные — 1 неделя
    sample = cache[f"{MONDAY.isoformat()}|10707"][1]
    assert "extra" not in json.dumps(sample) and "photo" not in json.dumps(sample)
    assert json.loads(pushed["sync_status.json"])["ok"]

    # следующий запуск через 3 часа: список групп не запрашиваем, остальные группы ещё свежие
    for k, (ts, data) in list(bot.client._cache.items()):
        bot.client._cache[k] = (ts - 3 * 3600, data)
    calls.clear()
    assert sync.run(interactive=False) == 0
    assert not [c for c in calls if c.get("act") == "list"]
    assert {str(c["groupId"]) for c in calls} == {"10706", "10707", "20001"} and len(calls) == 9

    # капча посреди запуска: что успели — сохранено и отправлено, статус «captcha»
    bot.client._cache.clear()
    calls.clear()
    captcha_after[0] = 3
    assert sync.run(interactive=False) == 1
    assert json.loads(pushed["sync_status.json"])["error"] == "captcha"
    assert len(json.loads(pushed["cache.json"])) == 3


def test_blocked_users_counted(env, monkeypatch):
    from telegram.error import Forbidden
    bot.USERS_FILE.write_text(json.dumps({"1": {"name": "a", "count": 3}, "2": {"name": "b", "count": 1}}),
                              encoding="utf-8")

    def member_update(uid, status):
        u = update(uid=uid)
        u.my_chat_member = Obj(chat=Obj(type="private"), new_chat_member=Obj(status=status))
        return u
    run(bot.track_user(member_update(1, "kicked"), ctx()))  # заблокировал
    users = bot._load_users()
    assert users["1"]["blocked"] and users["1"]["count"] == 3 and not users["2"].get("blocked")

    async def send_message(chat, text, **kw):
        if chat == 2:
            raise Forbidden("bot was blocked by the user")
    q = update(uid=99, data="bc:send")
    q.my_chat_member = None
    monkey_ctx = ctx()
    monkey_ctx.bot = Obj(send_message=send_message)
    monkey_ctx.bot_data["broadcast"] = {"ru": "Привет"}
    monkeypatch.setattr(bot, "_admin_id", lambda: 99)
    run(bot.on_broadcast(q, monkey_ctx))
    assert bot._load_users()["2"]["blocked"]  # рассылка не дошла — тоже отмечаем
    payload = bot._admin_payload(bot._load_users())
    rows = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)), -15))["u"]
    assert sum(1 for r in rows if r[10]) == 2

    run(bot.track_user(member_update(1, "member"), ctx()))  # снова запустил бота
    assert not bot._load_users()["1"].get("blocked")
    u = update(uid=2, text="/today")
    u.my_chat_member = None
    u.callback_query = None
    run(bot.track_user(u, ctx()))  # написал боту — значит, разблокировал
    assert not bot._load_users()["2"].get("blocked")


def test_group_name_above_schedule(env):
    bot._set_user_group(8, "ГМ-26-12")
    u = update(uid=8)
    run(bot.cmd_today(u, ctx()))
    text = u.effective_message.sent[-1][0]
    assert text.startswith("👥 <b>ГМ-26-12</b>\n\n📅 ") and "Физика" in text


def test_aivt_student(env):
    u = update(uid=11, data="grp:f:aivt")
    run(bot.on_group(u, ctx()))
    text, markup = u.callback_query.edited[0]
    assert text.startswith("💻 Автоматики и ВТ\n👥 Выберите курс")
    u = update(uid=11, data="grp:s:АА-26-01")
    run(bot.on_group(u, ctx()))
    assert bot._load_user_groups()["11"] == "АА-26-01"
    u = update(uid=11)
    run(bot.cmd_today(u, ctx()))
    text = u.effective_message.sent[-1][0]
    assert text.startswith("👥 <b>АА-26-01</b>") and "Программирование" in text and "Геология" not in text
    for lang in ("en", "fr"):
        t, m = bot._picker(lang)
        assert [b for b, _ in buttons(m)] == [f[lang] for f in bot.FACULTIES if f["key"] in ("geo", "aivt")]


def test_sync_faculty_missing_on_site(tmp_path, monkeypatch):
    """На сайте нет факультета АиВТ под таким названием — геология работает, в журнале список факультетов."""
    def fake_get(self, params):
        if params.get("method") == "getFaculties":
            return {"state": True, "rows": [{"id": 7, "name": "Факультет геологии и геофизики нефти и газа"},
                                            {"id": 9, "name": "Институт кибернетики"}]}
        return {"state": True, "rows": [{"id": int(g["id"]), "code": g["code"]} for g in GROUPS]}
    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    monkeypatch.setattr(bot, "client", GubkinClient())
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    monkeypatch.setattr(bot, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    groups = sync.refresh_groups(interactive=False)
    assert {g["fac"] for g in groups} == {"geo"}
    log = (tmp_path / "sync.log").read_text(encoding="utf-8")
    assert "не найден факультет «автоматик + вычислительн»" in log and "Институт кибернетики" in log


def test_sync_skips_weeks_the_site_refuses(monkeypatch):
    """Неделя, на которую сайт ответил ошибкой 500, не спрашивается при каждом запуске (сутки);
    прошедшие недели не качаются вовсе — они нужны только для /stats и /all."""
    monkeypatch.setattr(bot, "client", GubkinClient())
    weeks = sync.mondays(TODAY)
    g = {"id": "1", "code": "А-26-01"}
    past, future = weeks[0], weeks[-1]
    all_tasks = [m for m, _, _ in sync.plan([g], {"А-26-01"}, weeks, MONDAY)]
    assert past not in all_tasks and future in all_tasks
    failed = {f"{future.isoformat()}|1": time.time() - 3600}
    tasks = [m for m, _, _ in sync.plan([g], {"А-26-01"}, weeks, MONDAY, failed=failed)]
    assert future not in tasks and len(tasks) == len(all_tasks) - 1
    failed = {f"{future.isoformat()}|1": time.time() - 2 * 86400}
    tasks = [m for m, _, _ in sync.plan([g], {"А-26-01"}, weeks, MONDAY, failed=failed)]
    assert future in tasks  # прошло время — пробуем снова


def test_sync_far_weeks_only_for_active_groups(monkeypatch):
    """Дальние недели — только группам, которые кто-то открывал недавно; ближние — всем выбранным."""
    monkeypatch.setattr(bot, "client", GubkinClient())
    weeks = sync.mondays(TODAY)
    groups = [{"id": "1", "code": "А-26-01"}, {"id": "2", "code": "Б-26-02"}]
    tasks = sync.plan(groups, {"А-26-01", "Б-26-02"}, weeks, MONDAY, active={"А-26-01"})
    near = {MONDAY + dt.timedelta(weeks=k) for k in range(3)}
    by = {c: [m for m, g, _ in tasks if g["code"] == c] for c in ("А-26-01", "Б-26-02")}
    assert set(by["Б-26-02"]) == near & set(weeks)
    assert len(by["А-26-01"]) > len(by["Б-26-02"])


def test_sync_retries_when_site_did_not_answer(tmp_path, monkeypatch):
    """Сайт не ответил на первый запрос (а старая копия есть) — в конце запуска пробуем ещё раз."""
    from gubkin_api import GubkinError
    calls = []

    def fake_get(self, params):
        calls.append(params)
        if params.get("act") == "list":
            raise AssertionError("список групп свежий — не должен запрашиваться")
        if len(calls) == 1:
            raise GubkinError("Сайт вернул страницу вместо данных")
        gid = str(params["groupId"])
        return week_for(gid, "ГМ-26-11", "Геология")
    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    client = GubkinClient(cache_file=str(tmp_path / "cache.json"))
    client.slim = slim_week
    weeks = sync.mondays(dt.date.today())
    for m in weeks:  # всё уже скачано, но давно — обновляем только ближние недели
        client._cache[(m, "10706")] = (time.time() - 2 * 86400, {})
    monkeypatch.setattr(bot, "client", client)
    monkeypatch.setattr(bot, "_group", {"id": "10706", "code": bot.GROUP_CODE, "faculty": ""})
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    bot.GROUPS_FILE.write_text(json.dumps({"faculties": [{"key": "geo", "groups": GROUPS[:1]}], "time": time.time(),
                                           "tried": [f["key"] for f in bot.FACULTIES]}), encoding="utf-8")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    monkeypatch.setattr(sync, "FAILED", tmp_path / "sync_failed.json")
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    class R:
        returncode = 0
        stdout = b"{}"
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())
    sync.download(False, "root@test")  # не падает: текущая неделя скачалась со второй попытки
    assert [c["groupId"] for c in calls].count("10706") == len(calls)


def test_sync_stops_without_connection_and_ignores_system_proxy(tmp_path, monkeypatch):
    """VPN выключен, а в Windows остался его прокси: sync идёт мимо него; если связи нет совсем —
    останавливается после нескольких неудач и пишет коротко, без простыни ошибок."""
    import requests
    calls = []

    def fake_req(self, method, url, **kw):
        calls.append(url)
        raise requests.exceptions.ProxyError("Unable to connect to proxy 127.0.0.1:12334 " + "x" * 500)
    monkeypatch.setattr(GubkinClient, "_req", fake_req)
    client = GubkinClient(cache_file=str(tmp_path / "cache.json"))
    monkeypatch.setattr(bot, "client", client)
    monkeypatch.setattr(bot, "_group", {"id": "10706", "code": bot.GROUP_CODE, "faculty": ""})
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    bot.GROUPS_FILE.write_text(json.dumps(GROUPS_JSON, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    monkeypatch.setattr(sync, "FAILED", tmp_path / "sync_failed.json")
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    class R:
        returncode = 0
        stdout = b"{}"
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())
    with pytest.raises(Exception):
        sync.download(False, "root@test")
    assert client.s.trust_env is False  # системный прокси Windows не используется
    log = (tmp_path / "sync.log").read_text(encoding="utf-8")
    assert "похоже, нет связи с сайтом" in log and "не удалось подключиться через прокси/VPN" in log
    assert "x" * 50 not in log and len(calls) < 30


def test_only_one_sync_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "LOCK", tmp_path / "sync.lock")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    ran = []
    monkeypatch.setattr(sync, "_run", lambda interactive: ran.append(1) or 0)
    assert sync.run(True) == 0 and not sync.LOCK.exists()  # замок снят после работы
    sync.LOCK.write_text(str(os.getppid()))  # другая копия ещё работает (её процесс жив)
    assert sync.run(True) == 1 and ran == [1]
    sync.LOCK.write_text("999999999")  # окно закрыли крестиком: замок остался, а программы уже нет
    assert sync.run(True) == 0 and ran == [1, 1]
    sync.LOCK.write_text(str(os.getppid()))
    assert "уже работает в другом окне" in (tmp_path / "sync.log").read_text(encoding="utf-8")
    old = time.time() - 3600
    os.utime(sync.LOCK, (old, old))  # замок старше 40 минут — тоже брошенный
    assert sync.run(True) == 0 and ran == [1, 1, 1]


def test_captcha_new_picture_each_try_and_keyboard_layout(tmp_path, monkeypatch):
    shown, checked = [], []

    class C:
        def captcha_image(self):
            shown.append(1)
            return b"png"

        def validate_captcha(self, code):
            checked.append(code)
            return code == "цэмэдк"
    monkeypatch.setattr(bot, "client", C())
    monkeypatch.setattr(sync, "HERE", tmp_path)
    monkeypatch.setattr(sync.os, "name", "posix")
    answers = iter(["неверно", "w'v'lr"])  # второй раз — в английской раскладке
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    sync.solve_captcha()
    assert len(shown) == 2 and checked == ["неверно", "цэмэдк"]


def test_captcha_survives_unwritable_folder_and_viewer_error(tmp_path, monkeypatch):
    """Windows иногда не даёт записать/открыть картинку ([Errno 22]). Это не должно ронять загрузку:
    пишем во временную папку, а если просмотрщик не открылся — просто показываем путь."""
    class C:
        def captcha_image(self):
            return b"png"

        def validate_captcha(self, code):
            return True
    monkeypatch.setattr(bot, "client", C())

    locked = tmp_path / "locked"  # папка с ботом не существует — запись в неё не удастся ([Errno 22]/no dir)
    monkeypatch.setattr(sync, "HERE", locked)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(sync.tempfile, "gettempdir", lambda: str(tmpdir))
    monkeypatch.setattr(sync.os, "name", "posix")
    monkeypatch.setattr("builtins.input", lambda prompt="": "код")
    sync.solve_captcha()  # не падает
    assert (tmpdir / "captcha.png").read_bytes() == b"png"  # ушло во временную папку


def test_start_mentions_feedback_contact(env):
    u = update(uid=12)
    run(bot.cmd_start(u, ctx()))
    assert u.effective_message.sent[0][0].endswith("✉️ Если заметили ошибку в расписании — напишите @TawiOner")


def test_sync_finds_chemistry_and_economics_not_lookalikes(tmp_path, monkeypatch):
    """Химия и экономика находятся по ВСЕМ словам названия; похожие названия не путаются."""
    site = [
        {"id": 7, "name": "Факультет геологии и геофизики нефти и газа"},
        {"id": 3, "name": "Кафедра нефтехимической технологии"},           # «химическ» есть, но это не факультет химии
        {"id": 4, "name": "Факультет химической технологии и экологии"},
        {"id": 5, "name": "Факультет экономической безопасности"},          # «экономик» нет — только «экономическ»
        {"id": 6, "name": "Факультет экономики и управления"},
        {"id": 9, "name": "Факультет автоматики и вычислительной техники"},
        {"id": 11, "name": "Кафедра сооружения и ремонта трубопроводов"},    # «трубопровод» есть, но это кафедра
        {"id": 12, "name": "Факультет разработки нефтяных и газовых месторождений"},
        {"id": 13, "name": "Факультет проектирования, сооружения и эксплуатации систем трубопроводного транспорта"},
        {"id": 14, "name": "Кафедра инженерной графики"},                   # «инженерн» есть, «механик» нет
        {"id": 15, "name": "Факультет инженерной механики"},
        {"id": 16, "name": "Факультет комплексной безопасности топливно-энергетического комплекса"},
        {"id": 17, "name": "Кафедра международного права"},                 # «международн» есть, «бизнес» нет
        {"id": 18, "name": "Факультет международного энергетического бизнеса"},
        {"id": 19, "name": "Факультет гуманитарного образования"},
    ]
    rows = {7: GROUPS, 9: AIVT, 3: [{"id": "1", "code": "НХ-26-01"}],
            11: [{"id": "3", "code": "ТР-26-99"}],
            12: [{"id": "120001", "code": "РН-26-01"}], 13: [{"id": "130001", "code": "ТТ-26-01"}],
            14: [{"id": "4", "code": "ИГ-26-99"}],
            15: [{"id": "150001", "code": "МБ-26-01"}], 16: [{"id": "160001", "code": "БТ-26-01"}],
            17: [{"id": "5", "code": "МП-26-99"}],
            18: [{"id": "180001", "code": "МЭ-26-01"}], 19: [{"id": "190001", "code": "ГО-26-01"}],
            4: [{"id": "40001", "code": "ХТ-26-01"}, {"id": "40002", "code": "ХЭ-25-02"}],
            5: [{"id": "2", "code": "ЭБ-26-01"}],
            6: [{"id": "60001", "code": "ЭМ-26-01"}, {"id": "60002", "code": "ЭФ-24-03"}]}

    def fake_get(self, params):
        if params.get("method") == "getFaculties":
            return {"state": True, "rows": site}
        return {"state": True, "rows": [{"id": int(g["id"]), "code": g["code"]} for g in rows[params["facultyId"]]]}
    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    monkeypatch.setattr(bot, "client", GubkinClient())
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    monkeypatch.setattr(bot, "GROUPS_FILE", tmp_path / "groups.json")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    groups = sync.refresh_groups(interactive=False)
    data = json.loads((tmp_path / "groups.json").read_text(encoding="utf-8"))
    assert [(f["key"], f["id"]) for f in data["faculties"]] == [("geo", 7), ("aivt", 9), ("chem", 4), ("econ", 6),
                                                                 ("oil", 12), ("pipe", 13), ("mech", 15), ("safety", 16),
                                                                 ("intl", 18), ("hum", 19)]
    by_fac = {}
    for g in groups:
        by_fac.setdefault(g["fac"], []).append(g["code"])
    assert by_fac["chem"] == ["ХТ-26-01", "ХЭ-25-02"] and by_fac["econ"] == ["ЭМ-26-01", "ЭФ-24-03"]
    assert "НХ-26-01" not in by_fac["chem"] and "ЭБ-26-01" not in by_fac["econ"]
    assert by_fac["oil"] == ["РН-26-01"] and by_fac["pipe"] == ["ТТ-26-01"]
    assert by_fac["mech"] == ["МБ-26-01"] and by_fac["safety"] == ["БТ-26-01"]
    assert by_fac["intl"] == ["МЭ-26-01"] and by_fac["hum"] == ["ГО-26-01"]
    text, markup = bot._picker("ru")
    assert [t for t, _ in buttons(markup)] == [f["ru"] for f in bot.FACULTIES]
    text, markup = bot._picker("ru", "chem")
    assert text.startswith("🧪 Химической технологии и экологии\n👥 Выберите курс")


# ---- защита от подмены расписания («Смешарики»)

def smesh_week(gid, code):
    """Неделя, где вместо настоящих пар — Смешарики (так однажды подменили данные другому сервису)."""
    w = week_for(gid, code, "Как подружиться с Крошем")
    lo = w["rows"]["organizations"][0]["lessons"][0]
    lo["rooms"] = [{"number": "Дом Копатыча"}]
    lo["teachers"] = [{"lastName": "Совунья"}]
    return w


def test_guard_catches_smeshariki_but_not_real_names():
    import guard
    from schedule import Lesson
    real = [Lesson("2026-10-16", "Пт", "08:30", "10:00", "Физика", "Семинар", "1715", "Ежиков Иван Петрович"),
            Lesson("2026-10-16", "Пт", "10:15", "11:45", "Мультимедиа-технологии", "Лабораторная работа",
                   "ДОТ https://online.gubkin.ru", "Барашков А. А., Пинский Б. Б., Smith John",
                   notes=["Ссылка: https://zoom.us/j/1"]),
            Lesson("2026-10-16", "Пт", "12:00", "13:30", "Иностранный язык (английский)", "Тест", "Ц-11", "Нюшин В.В.")]
    assert guard.check_lessons(real) == []
    # на сайте в поле преподавателя бывают цифры — это не подмена
    assert guard.lesson_problems(Lesson("2026-10-14", "Ср", "08:30", "10:00", "Основы компьютерного зрения",
                                        "Лекция", "1234", "Вакансия 2, Иванов И.И. (0,5 ст.)")) == []
    # настоящие имена, похожие на персонажей, технические термины и длинные списки — тоже не подмена
    for subject, teacher, notes in [("Разработка на ASP.NET и Node.js", "Бараш Михаил Ильич, Ли Пин, Биби Ахмед", []),
                                    ("Заканчивание скважин", "Петров П.П.", ["Тема: заглушка скважины"]),
                                    ("Физическая культура", ", ".join(f"Преподавателев{i} И.И." for i in range(40)), [])]:
        assert guard.lesson_problems(Lesson("2026-10-14", "Ср", "08:30", "10:00", subject, "Лекция",
                                            ", ".join(str(1000 + i) for i in range(40)), teacher, notes=notes)) == []
    for subject, room, teacher in [("Физика", "дом Копатыча", "Петров"), ("Лекция у Кроша", "505", "Петров"),
                                   ("Физика", "505", "Совунья"), ("Физика", "505", "Кар-Карыч"),
                                   ("Смешарики", "505", "Петров"), ("Физика 🎉", "505", "Петров"),
                                   ("Физика", "505", "Петров"), ]:
        l = Lesson("2026-10-16", "Пт", "08:30", "10:00", subject, "Семинар", room, teacher)
        if (subject, room, teacher) == ("Физика", "505", "Петров"):
            l.notes = ["Перенесено к Нюше"]
        assert guard.lesson_problems(l), (subject, room, teacher)
    assert guard.lesson_problems(Lesson("2026-10-16", "Пт", "25:00", "26:00", "Физика", "", "", ""))


def test_bot_shows_error_instead_of_smeshariki(env, monkeypatch):
    cache = json.loads((env / "cache.json").read_text(encoding="utf-8"))
    cache[f"{MONDAY.isoformat()}|10707"] = [time.time(), slim_week(smesh_week("10707", "ГМ-26-12"))]
    (env / "cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    sent_admin = []

    async def send_message(chat, text, **kw):
        sent_admin.append((chat, text))
    monkeypatch.setattr(bot, "_admin_id", lambda: 99)
    monkeypatch.setattr(bot, "_alerted", set())
    bot._set_user_group(5, "ГМ-26-12")
    u = update(uid=5)
    u.get_bot = lambda: Obj(send_message=send_message)
    run(bot.cmd_today(u, ctx()))
    texts = [t for t, _ in u.effective_message.sent]
    assert any("Произошла ошибка" in t for t in texts)
    assert not any("Копатыч" in t or "Совунья" in t or "Крош" in t for t in texts)
    assert sent_admin and "ГМ-26-12" in sent_admin[0][1]
    u2 = update(uid=5)  # «Где пара» — тоже ошибка, без карты со Смешариками
    u2.get_bot = lambda: Obj(send_message=send_message)
    run(bot.cmd_map(u2, ctx()))
    assert "Произошла ошибка" in u2.effective_message.sent[0][0] and len(u2.effective_message.sent) == 1
    bot._set_user_group(6, "ГМ-26-11")  # у других групп всё по-прежнему
    u3 = update(uid=6)
    run(bot.cmd_today(u3, ctx()))
    assert "Геология" in u3.effective_message.sent[-1][0]


def _sync_env(tmp_path, monkeypatch, fake_get):
    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    client = GubkinClient(cache_file=str(tmp_path / "cache.json"))
    client.slim = slim_week
    monkeypatch.setattr(bot, "client", client)
    monkeypatch.setattr(bot, "_group", {"id": "10706", "code": bot.GROUP_CODE, "faculty": ""})
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    bot.GROUPS_FILE.write_text(json.dumps(GROUPS_JSON, ensure_ascii=False), encoding="utf-8")
    for name, f in (("LOG", "sync.log"), ("FAILED", "sync_failed.json"), ("STATUS", "sync_status.json"),
                    ("LOCK", "sync.lock")):
        monkeypatch.setattr(sync, name, tmp_path / f)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    monkeypatch.setattr(sync, "server", lambda: "root@test")
    pushed = {}
    monkeypatch.setattr(sync, "put", lambda srv, name, data: pushed.__setitem__(name, data))

    class R:
        returncode = 0
        stdout = json.dumps({"1": "ГМ-26-11"}, ensure_ascii=False).encode()
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())
    return client, pushed


def test_sync_keeps_old_week_when_site_sends_smeshariki(tmp_path, monkeypatch):
    """Сайт подсунул Смешариков одной группе — эта неделя не сохраняется, остаётся прежняя."""
    def fake_get(self, params):
        gid = str(params["groupId"])
        code = next(g["code"] for g in GROUPS + AIVT if g["id"] == gid)
        if gid == "10706":
            return smesh_week(gid, code)
        return week_for(gid, code, "Предмет " + code)
    client, pushed = _sync_env(tmp_path, monkeypatch, fake_get)
    old = slim_week(week_for("10706", "ГМ-26-11", "Геология"))
    client._cache[(MONDAY, "10706")] = (time.time() - 5 * 3600, old)
    assert sync.run(interactive=False) == 1  # текущая неделя выбранной группы не обновилась — это ошибка
    cache = json.loads(pushed["cache.json"])
    assert cache[f"{MONDAY.isoformat()}|10706"][1] == old  # прежние настоящие пары на месте
    assert "Копатыч" not in pushed["cache.json"].decode() and "Совунья" not in pushed["cache.json"].decode()
    assert "подозрительн" in (tmp_path / "sync.log").read_text(encoding="utf-8")


def test_sync_stops_when_site_sends_smeshariki_everywhere(tmp_path, monkeypatch):
    def fake_get(self, params):
        gid = str(params["groupId"])
        return smesh_week(gid, "X")
    client, pushed = _sync_env(tmp_path, monkeypatch, fake_get)
    calls = []
    real_week = GubkinClient.week

    def counting_week(self, day, gid):
        calls.append(gid)
        return real_week(self, day, gid)
    monkeypatch.setattr(GubkinClient, "week", counting_week)
    assert sync.run(interactive=False) == 1
    status = json.loads(pushed["sync_status.json"])
    assert status["error"].startswith("suspicious") and len(calls) < 15  # остановился, не перебирая всё
    assert "Копатыч" not in pushed.get("cache.json", b"").decode()


def test_sync_rejects_plausible_but_completely_new_week(tmp_path, monkeypatch):
    """Подмена «правдоподобными» парами: вся неделя вдруг с незнакомыми предметами и преподавателями."""
    import guard
    from schedule import parse_week
    vocab = guard.Vocabulary()
    real = week_for("10706", "ГМ-26-11", "Геология")
    lessons = real["rows"]["organizations"][0]["lessons"]
    for i, name in enumerate(["Физика", "Химия"]):
        lo = copy.deepcopy(lessons[0])
        lo["timeChunks"] = [0]
        lo["weekDayNumber"] = i + 1
        lo["course"] = {"name": name}
        lessons.append(lo)
    vocab.add(parse_week(real, "10706"))
    fake = copy.deepcopy(real)
    for i, lo in enumerate(fake["rows"]["organizations"][0]["lessons"]):
        lo["course"] = {"name": f"Таинственный курс {i}"}
        lo["teachers"] = [{"lastName": f"Незнакомцев{'аб'[i % 2]}", "firstName": "Икс"}]
    reason = guard.check_update(parse_week(fake, "10706"), parse_week(real, "10706"), vocab)
    assert reason and "3 из 3" in reason
    # обычный перенос одной пары — не подмена
    moved = copy.deepcopy(real)
    moved["rows"]["organizations"][0]["lessons"][0]["course"] = {"name": "Новый факультатив"}
    assert guard.check_update(parse_week(moved, "10706"), parse_week(real, "10706"), vocab) is None


def test_sync_keeps_suspicious_saved_weeks_but_reports_them(tmp_path, monkeypatch):
    def fake_get(self, params):
        gid = str(params["groupId"])
        code = next(g["code"] for g in GROUPS + AIVT if g["id"] == gid)
        return week_for(gid, code, "Предмет " + code)
    client, pushed = _sync_env(tmp_path, monkeypatch, fake_get)
    past = MONDAY - dt.timedelta(weeks=3)
    client._cache[(past, "10707")] = (time.time(), slim_week(smesh_week("10707", "ГМ-26-12")))
    client._save_cache()
    sync.run(interactive=False)
    # не удаляем (вдруг проверка ошиблась), но бот такое не покажет
    assert f"{past.isoformat()}|10707" in json.loads(pushed["cache.json"])
    assert "подозрительные недели (1), бот их не покажет" in (tmp_path / "sync.log").read_text(encoding="utf-8")


def test_sync_skips_one_poisoned_group_and_keeps_going(tmp_path, monkeypatch):
    def fake_get(self, params):
        gid = str(params["groupId"])
        code = next(g["code"] for g in GROUPS + AIVT if g["id"] == gid)
        return smesh_week(gid, code) if gid == "10600" else week_for(gid, code, "Предмет " + code)
    client, pushed = _sync_env(tmp_path, monkeypatch, fake_get)
    assert sync.run(interactive=False) == 0  # выбранная группа (ГМ-26-11) скачалась — всё хорошо
    cache = json.loads(pushed["cache.json"])
    assert not [k for k in cache if k.endswith("|10600")]  # Смешарики не сохранены
    assert [k for k in cache if k.endswith("|20001")]      # остальные группы — на месте
    log = (tmp_path / "sync.log").read_text(encoding="utf-8")
    assert "подозрительные недели (не сохранены): 10600" in log


def test_manual_sync_has_no_time_limit_scheduled_has(tmp_path, monkeypatch):
    """Вручную (sync_now) качаем весь план, сколько бы ни шло; по ходу отдаём скачанное боту и держим
    замок свежим. По расписанию — стоп через 11 минут (задача Windows живёт не дольше 15)."""
    clock = [0.0]
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    def fake_get(self, params):
        clock[0] += 60  # сайт медленный: минута на каждую неделю
        if params.get("method") == "getFaculties":
            return {"state": True, "rows": [{"id": 7, "name": "Факультет геологии и геофизики нефти и газа"}]}
        if params.get("method") == "getFacultyGroups":
            return {"state": True, "rows": [{"id": int(g["id"]), "code": g["code"]} for g in GROUPS]}
        gid = str(params["groupId"])
        code = next(g["code"] for g in GROUPS if g["id"] == gid)
        return week_for(gid, code, "Предмет " + code)

    monkeypatch.setattr(GubkinClient, "_get", fake_get)

    def setup(sub):
        d = tmp_path / sub
        d.mkdir()
        client = GubkinClient(cache_file=str(d / "cache.json"), cookie_file=str(d / "cookies.json"))
        client.slim = slim_week
        monkeypatch.setattr(bot, "client", client)
        monkeypatch.setattr(bot, "_group", {"id": None, "code": bot.GROUP_CODE, "faculty": ""})
        monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
        for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE"):
            monkeypatch.setattr(bot, name, d / getattr(bot, name).name)
        monkeypatch.setattr(sync, "STATUS", d / "sync_status.json")
        monkeypatch.setattr(sync, "LOG", d / "sync.log")
        monkeypatch.setattr(sync, "FAILED", d / "sync_failed.json")
        monkeypatch.setattr(sync, "LOCK", d / "sync.lock")
        return d

    monkeypatch.setattr(sync, "server", lambda: "root@test")
    puts = []
    monkeypatch.setattr(sync, "put", lambda srv, name, data: puts.append((clock[0], name)))
    touched = []
    real_touch = sync._touch_lock
    monkeypatch.setattr(sync, "_touch_lock", lambda: touched.append(sync.LOCK.exists()) or real_touch())

    class R:
        returncode = 0
        stdout = json.dumps({"1": "ГМ-26-11", "2": "ГМ-26-12"}, ensure_ascii=False).encode()
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())

    weeks = sync.mondays(dt.date.today())
    setup("manual")
    assert sync.run(interactive=True) == 0
    assert clock[0] > 25 * 60  # шло дольше прежних 25 минут — и не оборвалось
    cache = json.loads((tmp_path / "manual" / "cache.json").read_text(encoding="utf-8"))
    future = [m for m in weeks if m >= dt.date.today() - dt.timedelta(days=dt.date.today().weekday())]
    assert len([k for k in cache if k.split("|")[1] in ("10706", "10707")]) == 2 * len(future)  # всё скачано
    mid = [t for t, name in puts if name == "cache.json"][:-1]
    assert mid and all(b - a >= sync.SEND_EVERY for a, b in zip(mid, mid[1:]))  # промежуточно — раз в 10 минут
    assert touched and all(touched)  # замок обновлялся, пока он был
    assert "время вышло" not in (tmp_path / "manual" / "sync.log").read_text(encoding="utf-8")

    clock[0] = 0.0
    puts.clear()
    setup("scheduled")
    sync.run(interactive=False)
    assert "время вышло" in (tmp_path / "scheduled" / "sync.log").read_text(encoding="utf-8")
    assert "обновлено недель: 12" in (tmp_path / "scheduled" / "sync.log").read_text(encoding="utf-8")  # 11 мин + 1
    assert [name for _, name in puts].count("cache.json") == 1  # по расписанию — только в конце


def test_sync_does_not_remove_someone_elses_lock(tmp_path, monkeypatch):
    """Ручной sync ждёт капчу дольше 40 минут, по расписанию стартовал второй: закончив, второй не
    должен снять замок первого."""
    monkeypatch.setattr(sync, "LOCK", tmp_path / "sync.lock")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    monkeypatch.setattr(sync, "_run", lambda interactive: sync.LOCK.write_text("12345") and 0)
    assert sync.run(False) == 0
    assert sync.LOCK.read_text() == "12345"


def test_admin_payload_fits_in_url(env):
    """Много пользователей: адрес статистики не длиннее лимита, итоги — по всем, в списке — самые недавние."""
    import random
    rnd = random.Random(1)
    now = dt.datetime.now(bot.MSK)
    today = now.strftime("%Y-%m-%d")
    users = {}
    for i in range(1500):
        last = (now - dt.timedelta(hours=i)).strftime("%Y-%m-%d %H:%M")
        name = "".join(rnd.choice("абвгдежзиклмнопрстуфхцчшэюя") for _ in range(rnd.randint(4, 12)))
        users[str(100000000 + i * 7919)] = {
            "name": name.title() + " " + name[::-1], "username": f"user{rnd.randint(0, 10**8)}", "first": last,
            "last": last, "count": rnd.randint(1, 900), "acts": {"today": 3, "week": 2},
            "days": {(now - dt.timedelta(days=d)).strftime("%Y-%m-%d"): 2 for d in range(60)}}
    payload = bot._admin_payload(users)
    assert len(payload) <= bot.ADMIN_PAYLOAD_MAX
    data = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)), -15))
    t = data["t"]
    assert t["users"] == 1500 and t["acts"] == {"today": 4500, "week": 3000}
    assert len(t["days"]) == 14 and t["days"][today] == [3000, 1500]
    assert 0 < len(data["u"]) < 1500
    assert [r[4] for r in data["u"]] == sorted((r[4] for r in data["u"]), reverse=True)  # самые недавние
    # мало пользователей — в список попадают все
    small = dict(list(users.items())[:5])
    rows = json.loads(zlib.decompress(base64.urlsafe_b64decode(
        (p := bot._admin_payload(small)) + "=" * (-len(p) % 4)), -15))["u"]
    assert len(rows) == 5


def test_admin_payload_faculty_counts(env):
    """В статистике — сколько людей с какого факультета (по выбранной группе) и сколько без группы."""
    bot._set_user_group(1, "ГМ-26-11")
    bot._set_user_group(2, "ГМ-26-12")
    bot._set_user_group(3, "НЕТ-ТАКОЙ")
    users = {str(i): {"name": f"u{i}", "first": "2026-10-01 10:00", "last": "2026-10-01 10:00", "count": 1}
             for i in (1, 2, 3, 4)}
    payload = bot._admin_payload(users)
    fac = json.loads(zlib.decompress(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)), -15))["t"]["fac"]
    geo = next(f["ru"] for f in bot.FACULTIES if f["key"] == "geo")
    assert fac == [[geo, 2], ["Группа не выбрана", 2]]


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat, text, **kw):
        self.sent.append((chat, text))


def test_changes_job_notifies_on_new_cancellation(env, monkeypatch):
    """Отмена, появившаяся в новом расписании, приходит тем, кто выбрал группу; первое знакомство — молча."""
    monkeypatch.setattr(bot, "CHANGES_FILE", env / "changes_seen.json")
    monkeypatch.setattr(bot, "QUIET", (24 * 60, 0))
    monkeypatch.setattr(bot, "_changes_mtime", [None])
    monkeypatch.setattr(bot, "_msk_now", lambda: dt.datetime.combine(TODAY, dt.time(8, 0), bot.MSK))  # до пары
    bot._set_user_group(5, "ГМ-26-11")
    bot._set_user_group(6, "ГМ-26-12")
    c = ctx()
    c.bot = FakeBot()
    run(bot.changes_job(c))  # первый запуск: только запоминает
    assert c.bot.sent == []
    run(bot.changes_job(c))  # файл не менялся — ничего не делает
    assert c.bot.sent == []

    cache = json.loads((env / "cache.json").read_text(encoding="utf-8"))
    key = f"{MONDAY.isoformat()}|10706"
    cache[key][1]["rows"]["organizations"][0]["lessons"][0]["isCanceled"] = True
    (env / "cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.utime(env / "cache.json", (time.time() + 5, time.time() + 5))
    run(bot.changes_job(c))
    assert [chat for chat, _ in c.bot.sent] == [5]
    text = c.bot.sent[0][1]
    assert "ГМ-26-11" in text and "❌ Отмена" in text and "Геология" in text and "10:45–12:15" in text

    os.utime(env / "cache.json", (time.time() + 10, time.time() + 10))
    run(bot.changes_job(c))  # та же отмена второй раз не приходит
    assert len(c.bot.sent) == 1


def test_changes_job_no_false_alarm_when_window_moves(env, monkeypatch):
    """Отмена, которая уже была вчера, не должна прийти сегодня как «новая», когда её день вошёл в окно."""
    if TODAY.weekday() == 0:
        pytest.skip("вчера — прошлая неделя")
    monkeypatch.setattr(bot, "CHANGES_FILE", env / "changes_seen.json")
    monkeypatch.setattr(bot, "QUIET", (24 * 60, 0))
    monkeypatch.setattr(bot, "_changes_mtime", [None])
    monkeypatch.setattr(bot, "CHANGES_DAYS", 1)  # окно — один день
    cache = json.loads((env / "cache.json").read_text(encoding="utf-8"))
    cache[f"{MONDAY.isoformat()}|10706"][1]["rows"]["organizations"][0]["lessons"][0]["isCanceled"] = True
    (env / "cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    bot._set_user_group(5, "ГМ-26-11")
    c = ctx()
    c.bot = FakeBot()
    noon = dt.datetime.combine(TODAY, dt.time(8, 0), bot.MSK)
    monkeypatch.setattr(bot, "_msk_now", lambda: noon - dt.timedelta(days=1))
    run(bot.changes_job(c))  # вчера: запомнили неделю
    run(bot.changes_job(c))
    monkeypatch.setattr(bot, "_changes_mtime", [None])
    monkeypatch.setattr(bot, "_msk_now", lambda: noon)  # сегодня: та же отмена попала в окно
    run(bot.changes_job(c))
    assert c.bot.sent == []


def test_changes_job_skips_lessons_already_over(env, monkeypatch):
    """Пару, которая уже закончилась, отменять поздно — о ней не пишем."""
    monkeypatch.setattr(bot, "CHANGES_FILE", env / "changes_seen.json")
    monkeypatch.setattr(bot, "QUIET", (24 * 60, 0))
    monkeypatch.setattr(bot, "_changes_mtime", [None])
    monkeypatch.setattr(bot, "_msk_now", lambda: dt.datetime.combine(TODAY, dt.time(15, 0), bot.MSK))
    bot._set_user_group(5, "ГМ-26-11")
    c = ctx()
    c.bot = FakeBot()
    run(bot.changes_job(c))
    cache = json.loads((env / "cache.json").read_text(encoding="utf-8"))
    cache[f"{MONDAY.isoformat()}|10706"][1]["rows"]["organizations"][0]["lessons"][0]["isCanceled"] = True
    (env / "cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.utime(env / "cache.json", (time.time() + 5, time.time() + 5))
    run(bot.changes_job(c))  # пара сегодня 10:45–12:15, сейчас 15:00
    assert c.bot.sent == []


def test_broken_files_dont_wipe_what_is_known(env, monkeypatch):
    """Битый groups.json или cache.json не стирает уже известные группы и расписание."""
    assert bot._find_group("АА-26-01")
    (env / "groups.json").write_text("{обрывок", encoding="utf-8")
    os.utime(env / "groups.json", (time.time() + 5, time.time() + 5))
    assert bot._find_group("АА-26-01"), "группа пропала из-за битого файла"
    assert bot.client.has_group("20001")
    (env / "cache.json").write_text("{обрывок", encoding="utf-8")
    os.utime(env / "cache.json", (time.time() + 5, time.time() + 5))
    assert bot.client.has_group("20001"), "расписание пропало из-за битого файла"


def test_captcha_too_often_stops_instead_of_asking_again(monkeypatch):
    """Капча вернулась почти сразу после введённой — не мучаем человека, а останавливаемся."""
    real_solve = sync.solve_captcha
    monkeypatch.setattr(sync, "_captcha", {"solved": 0, "ok_since": 0, "wait": 0.0})
    monkeypatch.setattr(bot, "client", GubkinClient())
    solved = []
    monkeypatch.setattr(sync, "solve_captcha", lambda: solved.append(1))
    calls = {"n": 0}

    def site():
        calls["n"] += 1
        if calls["n"] in (1, 3):  # капча, после неё один удачный запрос — и снова капча
            raise sync.CaptchaRequired("капча")
        return "ok"
    assert sync._with_captcha(True, site) == "ok" and solved == [1]
    with pytest.raises(sync.CaptchaTooOften):
        sync._with_captcha(True, site)
    assert solved == [1]  # второй раз вводить не просили

    # пустой ввод — сразу остановка, без трёх попыток и без проверки кода на сайте
    asked = []
    monkeypatch.setattr("builtins.input", lambda *_: asked.append(1) or "  ")
    monkeypatch.setattr(sync, "_save_captcha", lambda data, attempt: pathlib.Path("c.png"))
    monkeypatch.setattr(bot.client, "captcha_image", lambda: b"")
    monkeypatch.setattr(bot.client, "validate_captcha", lambda code: pytest.fail("пустой код не проверяем"))
    monkeypatch.setattr(sync.os, "name", "posix")
    with pytest.raises(sync.CaptchaRequired):
        real_solve()
    assert asked == [1]


def test_full_mode_waits_instead_of_stopping(monkeypatch):
    """«Всё за раз»: капча слишком часто — ждём передышку и продолжаем, а не останавливаемся."""
    monkeypatch.setattr(sync, "_captcha", {"solved": 1, "ok_since": 0, "wait": 0.0})
    monkeypatch.setattr(sync, "FULL", {"on": True})
    monkeypatch.setattr(bot, "client", GubkinClient())
    monkeypatch.setattr(sync, "_touch_lock", lambda: None)
    slept = []
    monkeypatch.setattr(sync.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(sync, "solve_captcha", lambda: None)
    calls = {"n": 0}

    def site():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sync.CaptchaRequired("капча")
        return "ok"
    assert sync._with_captcha(True, site) == "ok"
    assert sum(slept) == sync.PAUSE
    # а все недели семестра в плане — в режиме «всё за раз» есть и дальние
    weeks = sync.mondays(TODAY)
    g = {"id": "1", "code": "А-26-01"}
    for m in weeks:
        bot.client._cache[(m, "1")] = (time.time() - 4 * 86400, {})
    full = [m for m, _, _ in sync.plan([g], {"А-26-01"}, weeks, MONDAY, far_weeks=len(weeks))]
    assert [m for m in weeks if m > MONDAY] == [m for m in full if m > MONDAY]


def test_sync_retries_after_short_connection_drop(tmp_path, monkeypatch):
    """На миг пропала связь (старой копии нет) — в конце запуска выбранной группе даём второй шанс."""
    import requests
    calls = []

    def fake_get(self, params):
        calls.append(params)
        if len(calls) == 1:
            raise requests.ConnectionError("обрыв")
        return week_for(str(params["groupId"]), "ГМ-26-11", "Геология")
    monkeypatch.setattr(GubkinClient, "_get", fake_get)
    client = GubkinClient(cache_file=str(tmp_path / "cache.json"))
    client.slim = slim_week
    for m in sync.mondays(dt.date.today()):
        if m > MONDAY:
            client._cache[(m, "10706")] = (time.time() - 2 * 86400, {})
    monkeypatch.setattr(bot, "client", client)
    monkeypatch.setattr(bot, "_group", {"id": "10706", "code": bot.GROUP_CODE, "faculty": ""})
    monkeypatch.setattr(bot, "_groups_cache", {"mtime": None, "list": []})
    for name in ("GROUPS_FILE", "USER_GROUPS_FILE", "GROUP_FILE"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    bot.GROUPS_FILE.write_text(json.dumps({"faculties": [{"key": "geo", "groups": GROUPS[:1]}], "time": time.time(),
                                           "tried": [f["key"] for f in bot.FACULTIES]}), encoding="utf-8")
    monkeypatch.setattr(sync, "LOG", tmp_path / "sync.log")
    monkeypatch.setattr(sync, "FAILED", tmp_path / "sync_failed.json")
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)

    class R:
        returncode = 0
        stdout = b"{}"
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())
    sync.download(False, "root@test")  # не падает: текущая неделя скачалась со второй попытки
    assert (MONDAY, "10706") in client._cache and client._cache[(MONDAY, "10706")][1]


def test_chess_invite_link_opens_the_game(env):
    """t.me/бот?start=chess_<id> — кнопка, открывающая партию в мини-приложении."""
    u = update(uid=9, text="/start chess_abc123")
    c = ctx(args=["chess_abc123"])
    c.bot = Obj(username="gubkin_test_bot")
    run(bot.cmd_start(u, c))
    text, kw = u.effective_message.sent[-1]
    url = kw["reply_markup"].inline_keyboard[0][0].web_app.url
    assert "шахматы" in text and "games.html?g=abc123" in url and "bot=gubkin_test_bot" in url


def test_games_command_opens_games_tab(env):
    """/games из меню команд — кнопка, открывающая вкладку «Игры»; команда есть в меню на всех языках."""
    u = update(uid=9, text="/games")
    c = ctx()
    c.bot = Obj(username="gubkin_test_bot")
    run(bot.cmd_games(u, c))
    text, kw = u.effective_message.sent[-1]
    url = kw["reply_markup"].inline_keyboard[0][0].web_app.url
    assert "шахматы" in text and url.endswith("games.html?lg=ru&bot=gubkin_test_bot")
    assert all(any(c == "games" for c, _ in cmds) for cmds in bot.COMMANDS.values())


def test_bot_sends_chess_search_notifications(env, monkeypatch, tmp_path):
    """Сервер игр положил «кто-то ищет игру» в очередь — бот присылает сообщение с кнопкой «♟ Играть»."""
    import games
    from telegram.error import Forbidden
    db = tmp_path / "games.db"
    monkeypatch.setattr(bot, "GAMES_DB", db)
    monkeypatch.setattr(bot, "_games_store", [None])
    run(bot.games_notify_job(Obj(bot=Obj(send_message=None))))  # базы игр нет — ничего не делаем
    s = games.Store(db)
    s.touch({"id": 1, "first_name": "Иван", "last_name": "Тестов"})
    s.db.execute("INSERT INTO games (id, kind, creator, status, created, updated) VALUES ('g1', 'search', 1, 'wait', 0, 0)")
    s.db.execute("INSERT INTO outbox VALUES ('g1', 2, ?), ('g1', 3, ?)", (time.time(), time.time()))
    s.db.commit()
    sent = []

    async def send_message(chat, text, **kw):
        if chat == 3:
            raise Forbidden("bot was blocked by the user")
        sent.append((chat, text, kw["reply_markup"].inline_keyboard[0][0]))
    blocked = []
    monkeypatch.setattr(bot, "_mark_blocked", lambda uid, b: blocked.append((uid, b)))
    c = Obj(bot=Obj(send_message=send_message, username="gubkin_test_bot"))
    run(bot.games_notify_job(c))
    (chat, text, btn), = sent
    assert chat == 2 and "Иван Т." in text and "ищет соперника" in text and btn.text == "♟ Играть"
    assert "games.html?g=g1" in btn.web_app.url
    assert blocked == [(3, True)]  # заблокировал бота — отмечаем
    run(bot.games_notify_job(c))
    assert len(sent) == 1  # очередь пуста — повторно не шлём
