"""Тесты выбора группы и расписания всего факультета, без сети и без Telegram:
python -m pytest test_groups.py"""

import asyncio
import copy
import datetime as dt
import json
import time

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
             f"{MONDAY.isoformat()}|10707": [time.time(), slim_week(week_for("10707", "ГМ-26-12", "Физика"))]}
    (tmp_path / "cache.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "groups.json").write_text(json.dumps({"groups": GROUPS, "time": time.time()}, ensure_ascii=False),
                                          encoding="utf-8")
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
    labels = [t for t, _ in buttons(markup)]
    assert text.startswith("👥 Выберите курс") and labels[-1] == "Другие"
    assert labels == sorted(labels[:-1], key=lambda s: int(s.split()[0]) if s[0].isdigit() else 99) + ["Другие"]


def test_default_group_without_groups_list(env):
    bot.GROUPS_FILE.unlink()
    groups = bot._faculty_groups()
    assert groups == [{"id": "10706", "code": "ГМ-26-11"}]
    text, markup = bot._picker("ru")
    assert "ещё загружается" in text and buttons(markup) == [("ГМ-26-11", "grp:s:ГМ-26-11")]


def test_new_user_picks_group_and_gets_schedule(env):
    u = update(uid=5)
    run(bot.cmd_today(u, ctx()))  # группа не выбрана — бот просит выбрать
    assert "Сначала выберите свою группу" in u.effective_message.sent[0][0]

    u = update(uid=5)
    run(bot.cmd_start(u, ctx()))
    assert "Группа не выбрана" in u.effective_message.sent[0][0]
    year_buttons = buttons(u.effective_message.sent[1][1]["reply_markup"])
    cb = next(c for t, c in year_buttons if t == "1 курс")

    u = update(uid=5, data=cb)
    run(bot.on_group(u, ctx()))
    text, markup = u.callback_query.edited[0]
    assert ("ГМ-26-12", "grp:s:ГМ-26-12") in buttons(markup) and ("⬅️ Назад", "grp:b") in buttons(markup)
    assert all(t.startswith("ГМ-26") for t, c in buttons(markup) if c.startswith("grp:s:"))

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
    tasks = sync.plan(groups, {"Б-26-02"}, weeks, MONDAY)
    first = [(m, g["code"]) for m, g, _ in tasks[:3]]
    assert first == [(MONDAY + dt.timedelta(weeks=k), "Б-26-02") for k in range(3)]
    others = [(m, g["code"]) for m, g, chosen in tasks if not chosen]
    assert others == [(MONDAY, "А-26-01"), (MONDAY, "В-25-01"),
                      (MONDAY + dt.timedelta(weeks=1), "А-26-01"), (MONDAY + dt.timedelta(weeks=1), "В-25-01")]
    rest = [m for m, g, chosen in tasks[3:] if chosen]
    assert len(rest) == len(weeks) - 3 and all(g["code"] == "Б-26-02" for _, g, c in tasks if c)
    # всё свежее — остальные группы не качаем, у выбранной — только ближние недели
    for m in weeks:
        for g in groups:
            bot.client._cache[(m, g["id"])] = (time.time(), {})
    tasks = sync.plan(groups, {"Б-26-02"}, weeks, MONDAY)
    assert [(m, g["code"]) for m, g, _ in tasks] == [(MONDAY + dt.timedelta(weeks=k), "Б-26-02") for k in range(3)]


def test_sync_plan_limits_other_groups(monkeypatch):
    monkeypatch.setattr(bot, "client", GubkinClient())
    weeks = sync.mondays(TODAY)
    groups = [{"id": str(i), "code": f"Г-26-{i:02d}"} for i in range(5)]
    bot.client._cache[(MONDAY, "0")] = (time.time() - 30 * 3600, {})  # есть, но старая
    bot.client._cache[(MONDAY, "1")] = (time.time() - 50 * 3600, {})  # ещё старее
    bot.client._cache[(MONDAY, "2")] = (time.time(), {})              # свежая — не нужна
    tasks = sync.plan(groups, set(), weeks, MONDAY, others_limit=4)
    assert [(m, g["code"]) for m, g, _ in tasks] == [
        (MONDAY, "Г-26-03"), (MONDAY, "Г-26-04"),  # сначала тех, которых нет совсем
        (MONDAY + dt.timedelta(weeks=1), "Г-26-00"), (MONDAY + dt.timedelta(weeks=1), "Г-26-01")]
    tasks = sync.plan(groups, set(), weeks, MONDAY)
    assert (MONDAY, "Г-26-01") in [(m, g["code"]) for m, g, _ in tasks][-2:]  # самые старые — после отсутствующих


def test_sync_end_to_end(tmp_path, monkeypatch):
    """Весь sync с поддельным сайтом и сервером: список групп, расписание, ужатый кэш, отправка."""
    from gubkin_api import CaptchaRequired
    calls = []

    def fake_get(self, params):
        calls.append(params)
        if params.get("method") == "getFaculties":
            return {"state": True, "rows": [{"id": 1, "name": "Факультет разработки"},
                                            {"id": 7, "name": "Факультет геологии и геофизики нефти и газа"}]}
        if params.get("method") == "getFacultyGroups":
            rows = [{"id": int(g["id"]), "code": g["code"]} for g in GROUPS] if params["facultyId"] == 7 else []
            return {"state": True, "rows": rows}
        if captcha_after[0] and sum(1 for c in calls if c.get("act") == "schedule") > captcha_after[0]:
            raise CaptchaRequired("капча")
        gid = str(params["groupId"])
        code = next(g["code"] for g in GROUPS if g["id"] == gid)
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
    monkeypatch.setattr(sync, "server", lambda: "root@test")
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    pushed = {}
    monkeypatch.setattr(sync, "put", lambda srv, name, data: pushed.__setitem__(name, data))

    class R:
        returncode = 0
        stdout = json.dumps({"1": "ГМ-26-11", "2": "ГМ-26-12"}, ensure_ascii=False).encode()
    monkeypatch.setattr(sync, "ssh", lambda *a, **kw: R())

    assert sync.run(interactive=False) == 0
    groups = json.loads(pushed["groups.json"])
    assert groups["faculty"]["id"] == 7 and len(groups["groups"]) == len(GROUPS)
    cache = json.loads(pushed["cache.json"])
    weeks = sync.mondays(dt.date.today())
    chosen = {k for k in cache if k.endswith("|10706") or k.endswith("|10707")}
    assert len(chosen) == 2 * len(weeks)  # выбранные группы — весь семестр
    others = {k.split("|")[1] for k in cache} - {"10706", "10707"}
    assert others == {"10600", "10500", "10800"}
    assert all(len([k for k in cache if k.endswith("|" + g)]) == 2 for g in others)  # остальные — 2 недели
    sample = cache[f"{MONDAY.isoformat()}|10707"][1]
    assert "extra" not in json.dumps(sample) and "photo" not in json.dumps(sample)
    assert json.loads(pushed["sync_status.json"])["ok"]

    # следующий запуск через 3 часа: список групп не запрашиваем, остальные группы ещё свежие
    for k, (ts, data) in list(bot.client._cache.items()):
        bot.client._cache[k] = (ts - 3 * 3600, data)
    calls.clear()
    assert sync.run(interactive=False) == 0
    assert not [c for c in calls if c.get("act") == "list"]
    assert {str(c["groupId"]) for c in calls} == {"10706", "10707"} and len(calls) == 6

    # капча посреди запуска: что успели — сохранено и отправлено, статус «captcha»
    bot.client._cache.clear()
    calls.clear()
    captcha_after[0] = 3
    assert sync.run(interactive=False) == 1
    assert json.loads(pushed["sync_status.json"])["error"] == "captcha"
    assert len(json.loads(pushed["cache.json"])) == 3
