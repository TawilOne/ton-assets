#!/usr/bin/env python3
"""Telegram-бот мини-игр (шахматы онлайн; сама игра — мини-приложение docs/games.html и сервер games.py).

Расписание, карта и любые обращения к сайту университета из бота удалены полностью.
Тем, у кого осталась старая нижняя панель кнопок, бот отвечает, что расписания больше нет, и убирает панель.

Запуск:  BOT_TOKEN=123:abc python bot.py
Переменные окружения:
    BOT_TOKEN     — токен от @BotFather (обязательно; или файл token.txt рядом)
    TG_PROXY      — прокси для Telegram, если api.telegram.org недоступен (или файл proxy.txt)
    TG_API_BASE   — адрес пересыльщика Bot API (например https://имя.workers.dev; или файл tg_api.txt)
    ADMIN_ID      — id администратора (или файл admin.txt): /users, /broadcast
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import json
import logging
import os
import pathlib
import re
import threading
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, Update, WebAppInfo
from telegram.error import Forbidden
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

import i18n

MSK = ZoneInfo("Europe/Moscow")
log = logging.getLogger("games-bot")
DATA_DIR = pathlib.Path(__file__).resolve().parent
USERS_FILE = DATA_DIR / "users.json"  # кто пользуется ботом (видно только администратору)
DEFAULT_WEBAPP_URL = "https://tawilone.github.io/ton-assets/"
NO_KEYBOARD = ReplyKeyboardRemove()  # убрать старую нижнюю панель кнопок расписания
_users_lock = threading.Lock()


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Файл всегда целый: сначала во временный, потом мгновенная подмена."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _lang(update: Update) -> str:
    """Язык пользователя (выбирается в /settings)."""
    return i18n.get_lang(update.effective_user.id) if update.effective_user else "ru"


def _bot_username(ctx) -> str:
    """Имя бота (для ссылок-приглашений в мини-играх); пусто, если бот ещё не знает себя."""
    try:
        return ctx.bot.username or ""
    except Exception:  # noqa: BLE001
        return ""


def _webapp_base() -> str:
    return os.environ.get("WEBAPP_URL") or _read_setting("webapp.txt") or DEFAULT_WEBAPP_URL


async def _open_game(update: Update, ctx: ContextTypes.DEFAULT_TYPE, gid: str) -> None:
    """Ссылка-приглашение t.me/бот?start=chess_<id>: кнопка, открывающая партию в мини-приложении."""
    lang = _lang(update)
    url = _webapp_base() + "games.html?" + urlencode({"g": gid, "lg": lang, "bot": _bot_username(ctx)})
    await update.effective_message.reply_text(
        i18n.t("chess_invite", lang),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t("chess_open", lang), web_app=WebAppInfo(url))]]))


async def cmd_games(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/games (меню команд): кнопка, открывающая вкладку «Игры» мини-приложения."""
    lang = _lang(update)
    url = _webapp_base() + "games.html?" + urlencode({"lg": lang, "bot": _bot_username(ctx)})
    await update.effective_message.reply_text(
        i18n.t("games_msg", lang),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t("games_open", lang), web_app=WebAppInfo(url))]]))


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    arg = (ctx.args or [""])[0] if getattr(ctx, "args", None) else ""
    if re.fullmatch(r"chess_[A-Za-z0-9]{4,16}", arg):
        await _open_game(update, ctx, arg[6:])
        return
    await update.effective_message.reply_text(i18n.t("welcome", _lang(update)), reply_markup=NO_KEYBOARD)


async def on_gone(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Старые команды и кнопки расписания и карты: их больше нет — говорим об этом и убираем старую панель."""
    await update.effective_message.reply_text(i18n.t("schedule_gone", _lang(update)), reply_markup=NO_KEYBOARD)


# старые команды расписания, карты и заданий — на них бот только отвечает, что их больше нет
GONE_COMMANDS = ["today", "tomorrow", "week", "next", "date", "all", "map", "group", "subscribe", "unsubscribe",
                 "captcha", "about", "count", "lectures", "labs", "first", "stats", "free", "untranslated"]


# ---- настройки: язык

def _settings_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(("• " if code == lang else "") + name, callback_data=f"lang:{code}")
                                  for code, name in i18n.LANG_NAMES.items()]])


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang = _lang(update)
    await update.effective_message.reply_text(i18n.t("settings", lang, name=i18n.LANG_NAMES[lang]), parse_mode="HTML",
                                              reply_markup=_settings_markup(lang))


async def on_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    lang = (q.data or "").split(":", 1)[-1]
    if lang not in i18n.LANGS:
        await q.answer()
        return
    i18n.set_lang(update.effective_user.id, lang)
    await q.answer(i18n.t("lang_set", lang))
    try:
        await q.edit_message_text(i18n.t("settings", lang, name=i18n.LANG_NAMES[lang]), parse_mode="HTML",
                                  reply_markup=_settings_markup(lang))
    except Exception:  # сообщение не изменилось
        pass


# ---- шахматы: уведомления «кто-то ищет игру» и «соперник нашёлся»

GAMES_DB = DATA_DIR / "games.db"  # база сервера игр (games.py); он кладёт уведомления, бот их отправляет
_games_store: list = [None]


async def games_notify_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not GAMES_DB.exists():
        return
    def take():
        if _games_store[0] is None:
            import games  # python-chess и aiohttp нужны только здесь
            _games_store[0] = games.Store(GAMES_DB, check_same_thread=False)
        return _games_store[0].take_outbox()
    try:  # в отдельном потоке: если база занята сервером игр, бот не должен ждать вместе со всеми командами
        todo = await asyncio.to_thread(take)
    except Exception:  # noqa: BLE001 — база занята или игр на этом сервере нет: попробуем в следующий раз
        log.warning("games outbox", exc_info=True)
        return
    for n in todo:
        uid, lang = n["uid"], i18n.get_lang(n["uid"])
        url = _webapp_base() + "games.html?" + urlencode({"g": n["gid"], "lg": lang, "bot": _bot_username(ctx)})
        who = n["name"] + (f" · {n['faculty']}" if n["faculty"] else "")
        found = n.get("kind") == "start"  # соперник нашёлся / кто-то ищет игру
        try:
            await ctx.bot.send_message(int(uid), i18n.t("chess_found" if found else "chess_seek", lang, who=who),
                                       reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                                           i18n.t("chess_found_btn" if found else "chess_seek_btn", lang),
                                           web_app=WebAppInfo(url))]]))
        except Forbidden:
            _mark_blocked(uid, True)
        except Exception:  # noqa: BLE001
            log.warning("cannot send chess notification to %s", uid)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Непойманная ошибка в обработчике: пишем в журнал, а человеку — что что-то пошло не так
    (раньше он просто не получал ответа)."""
    log.error("ошибка при обработке сообщения", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message and update.effective_user:
        try:
            await update.effective_message.reply_text(i18n.t("oops", _lang(update)))
        except Exception:
            pass


# ---- статистика пользователей (только для администратора)

def _admin_id() -> int | None:
    """id администратора: переменная ADMIN_ID или файл admin.txt рядом с bot.py."""
    raw = os.environ.get("ADMIN_ID") or _read_setting("admin.txt")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _load_users() -> dict:
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _user_label(u: dict) -> str:
    name = _esc_html(u.get("name") or "без имени")
    return name + (f" (@{_esc_html(u['username'])})" if u.get("username") else "")


def _esc_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# что нажал пользователь — для статистики в /users
ACTION_OF_CMD = {"start": "start", "help": "start", "games": "games", "chess": "games",
                 "settings": "settings", "language": "settings"}
ADMIN_CMDS = {"users", "broadcast", "myid"}


def _action(update: Update) -> str | None:
    if update.callback_query:
        kind = (update.callback_query.data or "").split(":", 1)[0]
        return {"lang": "settings"}.get(kind)
    text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if text.startswith("/"):
        cmd = text[1:].split()[0].split("@")[0].lower() if len(text) > 1 else ""
        if cmd in ADMIN_CMDS:  # служебные команды администратора в статистику не попадают
            return None
        return ACTION_OF_CMD.get(cmd, "other")
    return "text" if text else None


async def track_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Запоминает каждого, кто пишет боту. О новых людях сообщает администратору."""
    user = update.effective_user
    if user is None or user.is_bot:
        return
    now = dt.datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
    member = update.my_chat_member
    if member and member.chat.type == "private":  # пользователь заблокировал бота или снова запустил
        _mark_blocked(user.id, member.new_chat_member.status in ("kicked", "left"), now)
        return
    if user.id != _admin_id() and _flooding(user.id):  # защита от засыпания бота сообщениями
        if len(_hits[user.id]) == RATE_MAX + 1 and update.effective_message:  # предупреждаем один раз
            try:
                await update.effective_message.reply_text(i18n.t("too_fast", _lang(update)))
            except Exception:
                pass
        raise ApplicationHandlerStop  # дальше это сообщение не обрабатываем
    text = update.effective_message.text if update.effective_message else ""
    action = _action(update)
    with _users_lock:
        users = _load_users()
        key = str(user.id)
        is_new = key not in users
        u = users.setdefault(key, {"first": now, "count": 0})
        u.update(name=user.full_name, username=user.username or "", last=now,
                 count=u.get("count", 0) + 1, last_text=(text or "")[:40])
        u.pop("blocked", None)  # пишет боту — значит, не заблокировал
        if action and action != "text":  # по кнопкам и по дням — для графиков в /users
            acts = u.setdefault("acts", {})
            acts[action] = acts.get(action, 0) + 1
            days = u.setdefault("days", {})
            days[now[:10]] = days.get(now[:10], 0) + 1
            for old in sorted(days)[:-60]:  # храним 60 последних дней
                del days[old]
        try:
            write_atomic(USERS_FILE, json.dumps(users, ensure_ascii=False, indent=1))
        except OSError:
            log.warning("cannot save users.json")
    admin = _admin_id()
    if is_new and admin and user.id != admin:
        try:
            await ctx.bot.send_message(admin, f"🆕 Новый пользователь: {_user_label(u)}\nid: <code>{user.id}</code>",
                                       parse_mode="HTML")
        except Exception:
            log.warning("cannot notify admin")


RATE_MAX, RATE_WINDOW = 20, 30.0  # не больше 20 сообщений и нажатий за 30 секунд от одного человека
_hits: dict[int, collections.deque] = {}


def _flooding(user_id: int) -> bool:
    now = time.monotonic()
    if len(_hits) > 10000:  # память не растёт бесконечно: забываем тех, кто давно молчит
        for uid in [u for u, q in _hits.items() if not q or now - q[-1] > RATE_WINDOW]:
            del _hits[uid]
    q = _hits.setdefault(user_id, collections.deque())
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    q.append(now)
    return len(q) > RATE_MAX


def _mark_blocked(user_id, blocked: bool, when: str | None = None) -> None:
    """Отмечает в users.json, что пользователь заблокировал бота (или разблокировал)."""
    with _users_lock:
        users = _load_users()
        u = users.get(str(user_id))
        if u is None or bool(u.get("blocked")) == blocked:
            return
        if blocked:
            u["blocked"] = when or dt.datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
        else:
            u.pop("blocked", None)
        try:
            write_atomic(USERS_FILE, json.dumps(users, ensure_ascii=False, indent=1))
        except OSError:
            log.warning("cannot save users.json")


# ---- рассылка объявления всем пользователям (только администратор)

_BC_SPLIT = re.compile(r"^\s*-{2,}\s*(ru|en|fr)\s*-{2,}\s*$", re.IGNORECASE | re.MULTILINE)
BROADCAST_HELP = (
    "Как разослать объявление всем, кто запускал бота:\n\n"
    "/broadcast Текст объявления\n\n"
    "Можно добавить переводы — тем, у кого выбран английский или французский, придёт свой вариант:\n\n"
    "/broadcast Текст по-русски\n---en---\nText in English\n---fr---\nTexte en français\n\n"
    "С картинкой: отправьте картинку, а в подписи к ней напишите /broadcast Текст (до 1024 знаков). "
    "Можно и несколько картинок сразу (до 10) — альбомом, подпись /broadcast — у любой из них.\n\n"
    "Сначала бот покажет, как будет выглядеть сообщение, и спросит подтверждение. "
    "Вместе с сообщением у каждого пропадёт старая нижняя панель кнопок расписания."
)


def _parse_broadcast(text: str) -> dict[str, str]:
    parts = _BC_SPLIT.split(text)
    out = {"ru": parts[0].strip()}
    for lang, body in zip(parts[1::2], parts[2::2]):
        out[lang.lower()] = body.strip()
    return {k: v for k, v in out.items() if v}


async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != _admin_id():
        await update.effective_message.reply_text("Эта команда только для администратора.")
        return
    msg = update.effective_message
    # команда может быть и подписью к картинке — тогда рассылаем картинку с этой подписью
    await _broadcast_prepare(msg, ctx, msg.text or msg.caption or "", [msg.photo[-1].file_id] if msg.photo else [])


def _album(photos: list[str], caption: str) -> list:
    """Альбом для Telegram: подпись — у первой картинки (так её показывает Telegram под альбомом)."""
    from telegram import InputMediaPhoto
    return [InputMediaPhoto(p, caption=caption if i == 0 else None) for i, p in enumerate(photos)]


async def _broadcast_prepare(msg, ctx: ContextTypes.DEFAULT_TYPE, raw_text: str, photos: list[str]) -> None:
    """Предпросмотр рассылки и вопрос «Отправить?». photos — 0, 1 или альбом до 10 картинок."""
    raw = raw_text.split(maxsplit=1)
    texts = _parse_broadcast(raw[1]) if len(raw) > 1 else {}
    if not texts.get("ru"):
        await msg.reply_text(BROADCAST_HELP)
        return
    if photos and any(len(t) > 1024 for t in texts.values()):  # у подписи к картинке в Telegram предел 1024 знака
        await msg.reply_text("Подпись к картинке длиннее 1024 знаков — Telegram такую не пропустит. "
                             "Сократите текст или отправьте картинку и текст отдельно.")
        return
    ctx.bot_data["broadcast"], ctx.bot_data["broadcast_photos"] = texts, photos[:10]
    n = len(_load_users())
    for lang in i18n.LANGS:  # предпросмотр каждого варианта
        if lang in texts:
            if len(photos) > 1:
                await msg.reply_media_group(_album(photos[:10], texts[lang]))
            elif photos:
                await msg.reply_photo(photos[0], caption=texts[lang])
            else:
                await msg.reply_text(f"Предпросмотр ({i18n.LANG_NAMES[lang]}):\n\n{texts[lang]}")
    what = f" с {len(photos[:10])} картинками" if len(photos) > 1 else " с картинкой" if photos else ""
    await msg.reply_text(
        f"Отправить это{what} {n} пользователям? Без перевода английским и французским придёт русский вариант.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Отправить всем ({n})", callback_data="bc:send"),
                                            InlineKeyboardButton("✖️ Отмена", callback_data="bc:cancel")]]))


ALBUM_WAIT = 2.0  # альбом Telegram присылает пачкой отдельных сообщений — ждём, пока придут все картинки


async def on_admin_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Картинки от администратора: одна с подписью /broadcast — рассылка; альбом (2–10 фото) — собираем все
    его картинки (подпись обычно только у первой) и через пару секунд запускаем рассылку альбомом."""
    msg = update.effective_message
    if update.effective_user.id != _admin_id() or not msg.photo:
        return
    if not msg.media_group_id:
        if re.match(r"^/broadcast(@\w+)?(\s|$)", msg.caption or ""):
            await cmd_broadcast(update, ctx)
        return
    albums = ctx.bot_data.setdefault("albums", {})
    a = albums.setdefault(msg.media_group_id, {"msg": msg, "photos": [], "caption": ""})
    a["photos"].append((msg.message_id, msg.photo[-1].file_id))
    if msg.caption and not a["caption"]:
        a["caption"], a["msg"] = msg.caption, msg
    if len(a["photos"]) == 1 and ctx.job_queue:  # первая картинка альбома — ждём остальные
        ctx.job_queue.run_once(_album_job, ALBUM_WAIT, data=msg.media_group_id)


async def _album_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _album_done(ctx, ctx.job.data)


async def _album_done(ctx: ContextTypes.DEFAULT_TYPE, group_id: str) -> None:
    a = ctx.bot_data.get("albums", {}).pop(group_id, None)
    if not a or not re.match(r"^/broadcast(@\w+)?(\s|$)", a["caption"]):
        return  # альбом без /broadcast в подписи — это не рассылка
    photos = [f for _, f in sorted(a["photos"])]  # в том порядке, как в альбоме
    await _broadcast_prepare(a["msg"], ctx, a["caption"], photos)


async def on_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if update.effective_user.id != _admin_id():
        return
    texts, photos = ctx.bot_data.pop("broadcast", None), ctx.bot_data.pop("broadcast_photos", None) or []
    if q.data != "bc:send" or not texts:
        await q.edit_message_text("Рассылка отменена.")
        return
    await q.edit_message_text("Отправляю…")
    ok = fail = 0
    for uid in list(_load_users()):
        lang = i18n.get_lang(uid)
        text = texts.get(lang) or texts["ru"]
        try:  # вместе с сообщением убираем старую нижнюю панель расписания (у альбома кнопок не бывает)
            if len(photos) > 1:
                await ctx.bot.send_media_group(int(uid), _album(photos, text))
            elif photos:
                await ctx.bot.send_photo(int(uid), photos[0], caption=text, reply_markup=NO_KEYBOARD)
            else:
                await ctx.bot.send_message(int(uid), text, reply_markup=NO_KEYBOARD)
            ok += 1
        except Forbidden:  # заблокировал бота или удалил аккаунт
            _mark_blocked(uid, True)
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05 * max(1, len(photos)))  # Telegram ограничивает ~30 сообщений в секунду
    await q.edit_message_text(f"✅ Разослано: {ok}" + (f"\nНе доставлено: {fail} (заблокировали бота)" if fail else ""))


async def cmd_myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"Ваш id: <code>{update.effective_user.id}</code>", parse_mode="HTML")


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    admin = _admin_id()
    if admin is None:
        await update.effective_message.reply_text(
            "Администратор не настроен. Узнайте свой id командой /myid и сохраните его "
            "в файл admin.txt рядом с bot.py, потом перезапустите бота.")
        return
    if update.effective_user.id != admin:
        await update.effective_message.reply_text("Эта команда только для администратора.")
        return
    users = _load_users()
    if not users:
        await update.effective_message.reply_text("Пока никто не пользовался ботом.")
        return
    today = dt.datetime.now(MSK).strftime("%Y-%m-%d")
    active = sum(1 for u in users.values() if u.get("last", "").startswith(today))
    blocked = sum(1 for u in users.values() if u.get("blocked"))
    # полный список (все, кто когда-либо пользовался ботом) отдаёт админу сервер игр — по подписи Telegram;
    # в адресе — урезанная копия на случай, если сервер игр не отвечает
    try:
        write_atomic(ADMIN_STATS_FILE, json.dumps(_admin_data(users), ensure_ascii=False, separators=(",", ":")))
    except OSError:
        log.warning("cannot save %s", ADMIN_STATS_FILE)
    url = (os.environ.get("WEBAPP_URL") or _read_setting("webapp.txt") or DEFAULT_WEBAPP_URL) + \
        f"admin.html?v={int(time.time() // 300)}&p=" + _admin_payload(users)
    await update.effective_message.reply_text(
        f"👥 Пользователей: <b>{len(users)}</b> · сегодня активны: <b>{active}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Открыть статистику",
                                                                 web_app=WebAppInfo(url))]]))


ADMIN_PAYLOAD_MAX = 6000  # GitHub Pages не открывает слишком длинные адреса («URI Too Long»)
ADMIN_STATS_FILE = DATA_DIR / "admin_stats.json"  # полная статистика для admin.html — её отдаёт games.py


def _admin_data(users: dict) -> dict:
    """Статистика для admin.html: итоги (графики, счётчики) и строка на КАЖДОГО пользователя, недавние — первыми."""
    now = dt.datetime.now(MSK)
    today = now.strftime("%Y-%m-%d")
    days = [(now - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    per_day = {k: [0, 0] for k in days}  # день → [клики, людей]
    acts: dict[str, int] = {}
    for u in users.values():
        for k, n in (u.get("days") or {}).items():
            if k in per_day:
                per_day[k][0] += n
                per_day[k][1] += 1
        for a, n in (u.get("acts") or {}).items():
            acts[a] = acts.get(a, 0) + n
    totals = {"users": len(users), "blocked": sum(1 for u in users.values() if u.get("blocked")),
              "today": sum(1 for u in users.values() if u.get("last", "").startswith(today)),
              "a7": sum(1 for u in users.values() if u.get("last", "") >= days[7]),
              "new7": sum(1 for u in users.values() if u.get("first", "") >= days[7]),
              "acts": acts, "days": per_day, "fac": []}
    rows = []
    for uid, u in sorted(users.items(), key=lambda kv: kv[1].get("last", ""), reverse=True):
        rows.append([u.get("name") or "", u.get("username") or "", u.get("count", 0), u.get("first", ""),
                     u.get("last", ""), i18n.get_lang(uid), "", 0, 0, "", u.get("blocked") or ""])
    return {"now": now.strftime("%Y-%m-%d %H:%M"), "t": totals, "u": rows}


def _admin_payload(users: dict, limit: int = ADMIN_PAYLOAD_MAX) -> str:
    """Запасная копия статистики в параметре «p» адреса: сжатый JSON (deflate + base64url).
    В «#» класть нельзя: Telegram дописывает туда свои параметры. Итоги — по всем пользователям,
    а в список — столько самых недавних, сколько влезает в limit (полный список даёт сервер игр)."""
    import base64
    import zlib
    full = _admin_data(users)
    rows = full["u"][:300]

    def pack(n: int) -> str:
        data = json.dumps({**full, "u": rows[:n]}, ensure_ascii=False, separators=(",", ":")).encode()
        z = zlib.compressobj(9, zlib.DEFLATED, -15)  # «сырой» deflate — его умеет распаковать браузер
        return base64.urlsafe_b64encode(z.compress(data) + z.flush()).decode().rstrip("=")

    best = pack(len(rows))
    if len(best) > limit:  # больше всего строк, при которых адрес ещё влезает
        lo, hi = 0, len(rows)
        best = pack(0)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            p = pack(mid)
            if len(p) <= limit:
                lo, best = mid, p
            else:
                hi = mid - 1
    return best


def _read_setting(name: str) -> str | None:
    """Первая непустая строка файла рядом с bot.py (например token.txt), или None."""
    try:
        for line in (DATA_DIR / name).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                return line.strip()
    except OSError:
        pass
    return None


class _ShortNetworkErrors(logging.Filter):
    """Короткие обрывы связи с Telegram (VPN переподключается) — одной строкой вместо простыни ошибок.
    Бот сам переподключается, это не поломка."""
    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        if exc is not None and type(exc).__name__ in ("NetworkError", "TimedOut"):
            record.msg = "⚠️ Нет связи с Telegram (проверьте VPN) — пробую переподключиться..."
            record.args = None
            record.exc_info = None
            record.exc_text = None
        return True


async def on_gone_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer(i18n.t("schedule_gone", _lang(update)), show_alert=True)


COMMANDS = {  # меню команд Telegram (кнопка «Меню» слева от поля ввода) — на языке приложения Telegram
    "ru": [("games", "Мини-игры: шахматы"), ("settings", "Язык")],
    "en": [("games", "Mini games: chess"), ("settings", "Language")],
    "fr": [("games", "Mini-jeux : échecs"), ("settings", "Langue")],
}
# описание бота для тех, у кого Telegram на английском/французском (русские имя, описание и аватар — в @BotFather)
BOT_PROFILE = {
    "en": ("Mini games", "Online chess with a rating.", "Mini games: play online chess with other players and climb the rating."),
    "fr": ("Mini-jeux", "Échecs en ligne avec classement.",
           "Mini-jeux\u00a0: jouez aux échecs en ligne avec d'autres joueurs et grimpez au classement."),
}


async def _set_commands(app: Application) -> None:
    from telegram import BotCommand
    try:
        for lang, cmds in COMMANDS.items():
            await app.bot.set_my_commands([BotCommand(c, d) for c, d in cmds],
                                          language_code=None if lang == "ru" else lang)
        for lang, (name, short, desc) in BOT_PROFILE.items():
            await app.bot.set_my_name(name, language_code=lang)
            await app.bot.set_my_short_description(short, language_code=lang)
            await app.bot.set_my_description(desc, language_code=lang)
    except Exception as e:  # меню и описание — не главное, бот работает и без них
        log.warning("set_my_commands: %s", e)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(_ShortNetworkErrors())
    # httpx пишет каждый запрос вместе с адресом, а в адресе — токен бота; такие строки не нужны
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.environ.get("BOT_TOKEN") or _read_setting("token.txt")
    if not token:
        raise SystemExit("Нет токена бота. Создайте рядом с bot.py файл token.txt и вставьте в него "
                         "токен от @BotFather (или задайте переменную BOT_TOKEN).")
    proxy = os.environ.get("TG_PROXY") or _read_setting("proxy.txt")
    builder = (Application.builder().token(token)
               .connect_timeout(30).read_timeout(30).write_timeout(30)
               .get_updates_connect_timeout(30).get_updates_read_timeout(30)
               .concurrent_updates(True))
    # пересыльщик (например, Cloudflare Worker): бот ходит в Telegram через свой адрес вместо api.telegram.org
    relay = (os.environ.get("TG_API_BASE") or _read_setting("tg_api.txt") or "").rstrip("/")
    if relay:
        builder = builder.base_url(f"{relay}/bot").base_file_url(f"{relay}/file/bot")
        log.info("Telegram via relay %s", relay)
    elif proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
        log.info("Telegram via proxy %s", proxy.split("@")[-1])
    app = builder.post_init(_set_commands).build()
    app.add_error_handler(on_error)
    # group=-1: срабатывает раньше остальных обработчиков на каждое сообщение
    app.add_handler(TypeHandler(Update, track_user), group=-1)
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler(["games", "chess"], cmd_games))
    app.add_handler(CommandHandler(["settings", "language"], cmd_settings))
    app.add_handler(CallbackQueryHandler(on_lang, pattern=r"^lang:"))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(MessageHandler(filters.PHOTO, on_admin_photo))  # /broadcast подписью к фото или к альбому
    app.add_handler(CallbackQueryHandler(on_broadcast, pattern=r"^bc:"))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler(GONE_COMMANDS, on_gone))
    app.add_handler(CallbackQueryHandler(on_gone_button))  # старые кнопки под сообщениями расписания
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_gone))  # старая нижняя панель и любой текст
    app.job_queue.run_repeating(games_notify_job, interval=5, first=15)  # шахматы: уведомления
    log.info("Подключаюсь к Telegram...")
    # ALL_TYPES — чтобы приходили и события «пользователь заблокировал бота» (для /users)
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
