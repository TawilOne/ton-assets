"""Тесты бота мини-игр без сети и без Telegram:  python -m pytest test_bot.py"""
import asyncio
import datetime as dt
import json
import pathlib
import re
import sys
import time

import pytest
from telegram import ReplyKeyboardRemove
from telegram.error import Forbidden

import bot
import i18n

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Msg:
    def __init__(self, text=""):
        self.text = text
        self.caption, self.photo, self.media_group_id, self.message_id = None, (), None, 1
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append((text, kw))
        return self

    async def reply_photo(self, photo, **kw):
        self.sent.append((("photo", photo), kw))

    async def reply_media_group(self, media, **kw):
        self.sent.append((("album", [(m.media, m.caption) for m in media]), kw))


class Query:
    def __init__(self, data):
        self.data = data
        self.edited, self.answers = [], []

    async def answer(self, *a, **kw):
        self.answers.append((a, kw))

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edited.append((text, reply_markup))


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def update(uid=1, text="", data=None):
    user = Obj(id=uid, full_name="Тест", username="", is_bot=False)
    return Obj(effective_user=user, effective_message=Msg(text), effective_chat=Obj(id=uid),
               callback_query=Query(data) if data else None, my_chat_member=None)


def ctx(args=None):
    return Obj(args=args or [], user_data={}, bot_data={})


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("USERS_FILE", "ADMIN_STATS_FILE", "GAMES_DB"):
        monkeypatch.setattr(bot, name, tmp_path / getattr(bot, name).name)
    monkeypatch.setattr(i18n, "LANG_FILE", tmp_path / "lang.json")
    monkeypatch.setattr(i18n, "_langs", None)
    monkeypatch.setattr(bot, "_games_store", [None])
    monkeypatch.setattr(bot, "_admin_id", lambda: 99)
    return tmp_path


def test_nothing_from_the_university_site_is_left():
    """Бот не скачивает ничего с сайта университета: нет ни модулей, ни адресов сайта, ни расписания и карты."""
    assert not {"gubkin_api", "schedule", "sync", "guard"} & set(sys.modules)
    code = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html", ".sh", ".json", ".bat")
            and ".git" not in p.parts and "__pycache__" not in p.parts]
    hits = [str(p) for p in code if re.search(r"gubkin\.ru|lk\.gubkin", p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []
    for gone in ("docs/index.html", "docs/campus.html", "docs/campus.js", "docs/plans", "floorplans",
                 "gubkin_schedule_bot/sync.py", "gubkin_schedule_bot/gubkin_api.py", "gubkin_schedule_bot/schedule.py"):
        assert not (ROOT / gone).exists(), gone


def test_start_shows_games_and_removes_old_keyboard():
    u = update(uid=5, text="/start")
    run(bot.cmd_start(u, ctx()))
    (text, kw), = u.effective_message.sent
    assert "/games" in text and isinstance(kw["reply_markup"], ReplyKeyboardRemove)


def test_old_schedule_buttons_and_commands_answer_politely():
    """Старая нижняя панель («📅 Сегодня»), команды /today, /map и кнопки под старыми сообщениями —
    бот отвечает, что расписания больше нет, и убирает панель."""
    u = update(uid=5, text="📅 Сегодня")
    run(bot.on_gone(u, ctx()))
    (text, kw), = u.effective_message.sent
    assert "больше нет" in text and isinstance(kw["reply_markup"], ReplyKeyboardRemove)
    assert {"today", "week", "map", "group", "subscribe"} <= set(bot.GONE_COMMANDS)
    q = update(uid=5, data="grp:menu")
    run(bot.on_gone_button(q, ctx()))
    (args, kw), = q.callback_query.answers
    assert "больше нет" in args[0] and kw["show_alert"]


def test_chess_invite_link_opens_the_game():
    u = update(uid=9, text="/start chess_abc123")
    c = ctx(args=["chess_abc123"])
    c.bot = Obj(username="gubkin_test_bot")
    run(bot.cmd_start(u, c))
    text, kw = u.effective_message.sent[-1]
    url = kw["reply_markup"].inline_keyboard[0][0].web_app.url
    assert "шахматы" in text and "games.html?g=abc123" in url and "bot=gubkin_test_bot" in url


def test_games_command_and_menu():
    u = update(uid=9, text="/games")
    c = ctx()
    c.bot = Obj(username="gubkin_test_bot")
    run(bot.cmd_games(u, c))
    text, kw = u.effective_message.sent[-1]
    assert "шахматы" in text and kw["reply_markup"].inline_keyboard[0][0].web_app.url.endswith(
        "games.html?lg=ru&bot=gubkin_test_bot")
    assert all([c for c, _ in cmds] == ["games", "settings"] for cmds in bot.COMMANDS.values())


def test_language_switch():
    q = update(uid=7, data="lang:en")
    run(bot.on_lang(q, ctx()))
    assert i18n.get_lang(7) == "en" and "Settings" in q.callback_query.edited[0][0]


def test_broadcast_text_photo_and_album(env):
    bot.USERS_FILE.write_text(json.dumps({"1": {"name": "a"}, "2": {"name": "b"}}), encoding="utf-8")
    sent = []

    async def send_message(chat, text, **kw):
        if chat == 2:
            raise Forbidden("bot was blocked by the user")
        sent.append(("text", chat, text, type(kw["reply_markup"]).__name__))

    async def send_photo(chat, photo, **kw):
        if chat == 2:
            raise Forbidden("bot was blocked by the user")
        sent.append(("photo", chat, photo, kw["caption"]))

    async def send_media_group(chat, media, **kw):
        if chat == 2:
            raise Forbidden("bot was blocked by the user")
        sent.append(("album", chat, [(m.media, m.caption) for m in media]))
    c = ctx()
    c.bot, c.job_queue = Obj(send_message=send_message, send_photo=send_photo, send_media_group=send_media_group), None

    u = update(uid=99, text="/broadcast Привет")
    run(bot.cmd_broadcast(u, c))
    run(bot.on_broadcast(update(uid=99, data="bc:send"), c))
    assert sent == [("text", 1, "Привет", "ReplyKeyboardRemove")]  # заодно убираем старую панель
    assert bot._load_users()["2"]["blocked"]  # заблокировал бота — отмечаем

    sent.clear()
    u = update(uid=99)
    u.effective_message.text, u.effective_message.caption = None, "/broadcast Нас 500!"
    u.effective_message.photo = (Obj(file_id="small"), Obj(file_id="big"))
    run(bot.on_admin_photo(u, c))
    assert u.effective_message.sent[0][0] == ("photo", "big")
    run(bot.on_broadcast(update(uid=99, data="bc:send"), c))
    assert sent == [("photo", 1, "big", "Нас 500!")]

    sent.clear()
    first, second = update(uid=99), update(uid=99)
    for x, mid, fid, cap in ((first, 10, "p1", "/broadcast Альбом"), (second, 11, "p2", None)):
        m = x.effective_message
        m.text, m.caption, m.media_group_id, m.message_id = None, cap, "a1", mid
        m.photo = (Obj(file_id=fid),)
    run(bot.on_admin_photo(second, c))
    run(bot.on_admin_photo(first, c))
    run(bot._album_done(c, "a1"))
    assert "с 2 картинками" in first.effective_message.sent[-1][0]
    run(bot.on_broadcast(update(uid=99, data="bc:send"), c))
    assert sent == [("album", 1, [("p1", "Альбом"), ("p2", None)])]

    x = update(uid=5)  # не админ — ничего
    x.effective_message.caption, x.effective_message.photo = "/broadcast взлом", (Obj(file_id="x"),)
    run(bot.on_admin_photo(x, c))
    assert x.effective_message.sent == [] and "broadcast" not in c.bot_data


def test_users_stats_list_everyone():
    now = dt.datetime.now(bot.MSK)
    users = {str(1000 + i): {"name": f"Студент {i}", "first": "2026-09-01 10:00", "count": 1,
                             "last": (now - dt.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M")} for i in range(700)}
    bot.USERS_FILE.write_text(json.dumps(users, ensure_ascii=False), encoding="utf-8")
    u = update(uid=99)
    run(bot.cmd_users(u, ctx()))
    assert "Пользователей: <b>700</b>" in u.effective_message.sent[0][0]
    data = json.loads(bot.ADMIN_STATS_FILE.read_text(encoding="utf-8"))
    assert len(data["u"]) == 700 and data["u"][0][0] == "Студент 0"
    assert len(bot._admin_payload(users)) <= bot.ADMIN_PAYLOAD_MAX
    u = update(uid=5)
    run(bot.cmd_users(u, ctx()))
    assert "только для администратора" in u.effective_message.sent[0][0]


def test_chess_notifications(env):
    import games
    s = games.Store(bot.GAMES_DB)
    s.touch({"id": 1, "first_name": "Иван", "last_name": "Тестов"})
    s.touch({"id": 2, "first_name": "Мария", "last_name": "Тестова"})
    s.touch({"id": 3, "first_name": "Олег", "last_name": "Тестов"})
    s.db.execute("INSERT INTO games (id, kind, creator, status, created, updated) VALUES ('g1', 'search', 3, 'wait', 0, 0)")
    s.db.execute("INSERT INTO outbox (gid, uid, created) VALUES ('g1', 2, ?)", (time.time(),))
    gid = s.create(2, "invite")
    s.db.execute("UPDATE players SET seen = 0 WHERE id = 2")  # Мария свернула приложение
    s.db.commit()
    s.join(1, gid)
    sent = []

    async def send_message(chat, text, **kw):
        sent.append((chat, text, kw["reply_markup"].inline_keyboard[0][0].text))
    run(bot.games_notify_job(Obj(bot=Obj(send_message=send_message, username="gubkin_test_bot"))))
    assert sorted(sent) == [(2, "♟ Олег Т. ищет соперника в шахматах. Сыграем?\n\n"
                                "Отключить такие сообщения — кнопкой 🔔 в мини-играх.", "♟ Играть"),
                            (2, "♟ Соперник нашёлся: Иван Т.. Партия началась — заходите!", "♟ К партии")]
