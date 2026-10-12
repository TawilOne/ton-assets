#!/usr/bin/env python3
"""Telegram-бот расписания РГУ им. Губкина для группы ГМ-26-11 (факультет геологии).

Запуск:  BOT_TOKEN=123:abc python bot.py
Переменные окружения:
    BOT_TOKEN     — токен от @BotFather (обязательно)
    GROUP_CODE    — код группы (по умолчанию ГМ-26-11)
    GROUP_ID      — id группы на сайте, если известен (иначе найдётся по коду)
    FACULTY_HINT  — подстрока названия факультета для поиска (по умолчанию «геолог»)
    TG_PROXY      — прокси только для Telegram, если api.telegram.org недоступен
                    (например socks5://127.0.0.1:1080 или http://host:port).
                    Сайт Губкина при этом открывается напрямую.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import pathlib
import threading
import time
from zoneinfo import ZoneInfo

from telegram import InputFile, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

import my_commands as my
from gubkin_api import CaptchaRequired, GubkinClient, GubkinError
from schedule import fetch_range, semester_bounds, to_csv, to_html, to_ics, to_json, to_text

MSK = ZoneInfo("Europe/Moscow")
GROUP_CODE = os.environ.get("GROUP_CODE", "ГМ-26-11")
FACULTY_HINT = os.environ.get("FACULTY_HINT", "геолог")
TG_LIMIT = 4000
MORNING = dt.time(7, 0, tzinfo=MSK)  # время утренней рассылки
SUBSCRIBERS_FILE = pathlib.Path(os.environ.get("SUBSCRIBERS_FILE", "subscribers.json"))

log = logging.getLogger("gubkin-bot")
DATA_DIR = pathlib.Path(__file__).resolve().parent
client = GubkinClient(cookie_file=str(DATA_DIR / "cookies.json"), cache_file=str(DATA_DIR / "cache.json"))
GROUP_FILE = DATA_DIR / "group.json"
USERS_FILE = DATA_DIR / "users.json"  # кто пользуется ботом (видно только администратору)
_users_lock = threading.Lock()
_lock = threading.Lock()  # одна сессия сайта на всех — запросы по очереди
_group: dict = {"id": os.environ.get("GROUP_ID"), "code": GROUP_CODE, "faculty": ""}

HELP = (
    f"📅 Расписание РГУ им. Губкина, группа {GROUP_CODE}\n\n"
    "/today — на сегодня\n"
    "/tomorrow — на завтра\n"
    "/first — во сколько завтра первая пара\n"
    "/week — текущая неделя\n"
    "/next — следующая неделя\n"
    "/date ДД.ММ.ГГГГ — на конкретный день\n\n"
    "/lectures — лекции на этой неделе\n"
    "/labs — лабораторные на этой неделе\n"
    "/count — сколько пар на неделе\n"
    "/free — свободные дни на неделе\n"
    "/stats — сколько пар по каждому предмету за семестр\n\n"
    "/all — всё расписание семестра файлами (календарь, Excel)\n"
    "/subscribe — присылать расписание каждое утро в 7:00\n"
    "/unsubscribe — отписаться от рассылки\n"
    "/about — о боте"
)


def _ensure_group() -> None:
    if _group["id"]:
        return
    try:  # найденная группа запоминается в файл — не ищем её на сайте при каждом запуске
        saved = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
        if saved.get("code_wanted") == GROUP_CODE and saved.get("id"):
            _group.update(id=saved["id"], code=saved["code"], faculty=saved.get("faculty", ""))
            return
    except (OSError, ValueError):
        pass
    f, g = client.find_group(GROUP_CODE, FACULTY_HINT)
    _group.update(id=g["id"], code=g.get("code") or GROUP_CODE, faculty=f.get("name", ""))
    log.info("group %s id=%s faculty=%s", _group["code"], _group["id"], _group["faculty"])
    try:
        GROUP_FILE.write_text(json.dumps({**_group, "code_wanted": GROUP_CODE}, ensure_ascii=False),
                              encoding="utf-8")
    except OSError:
        pass


def _locked(func, *args):
    with _lock:
        return func(*args)


def _fetch(start: dt.date, end: dt.date):
    with _lock:
        _ensure_group()
        client.stale = False
        lessons = fetch_range(client, _group["id"], _group["code"], start, end)
        return lessons, client.stale


async def _run(update: Update, start: dt.date, end: dt.date):
    try:
        await update.effective_chat.send_action("typing")  # «печатает…», пока грузим с сайта
    except Exception:
        pass
    t0 = time.monotonic()
    try:
        lessons, stale = await asyncio.to_thread(_fetch, start, end)
        log.info("schedule %s..%s loaded in %.1f s%s", start, end, time.monotonic() - t0,
                 " (из сохранённого, сайт не ответил)" if stale else "")
        if stale:
            await update.effective_message.reply_text(
                "⚠️ Сайт Губкина сейчас не отвечает — показываю последнее сохранённое расписание.")
        return lessons
    except CaptchaRequired:
        await update.effective_message.reply_text("Сайт попросил капчу. Отправьте /captcha и введите код с картинки.")
    except (GubkinError, OSError) as e:
        log.exception("fetch failed")
        if "timed out" in str(e).lower() or "connect" in str(e).lower():
            msg = ("⏳ Сайт Губкина не отвечает.\n\n"
                   "Если на компьютере с ботом включён VPN — сайт может не открываться через него. "
                   "Попробуйте позже или настройте VPN так, чтобы российские сайты шли напрямую.")
        else:
            msg = f"Не удалось получить расписание: {e}"
        await update.effective_message.reply_text(msg)
    return None


def _split(text: str) -> list[str]:
    """Режет длинный текст по переносам строк (Telegram ограничивает длину сообщения)."""
    parts = []
    while text:
        cut = len(text) if len(text) <= TG_LIMIT else text.rfind("\n", 0, TG_LIMIT) + 1 or TG_LIMIT
        parts.append(text[:cut])
        text = text[cut:]
    return parts


async def _send_text(update: Update, text: str, html: bool = False) -> None:
    for part in _split(text):
        await update.effective_message.reply_text(part, parse_mode="HTML" if html else None,
                                                  reply_markup=KEYBOARD)


def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


async def _show(update: Update, start: dt.date, end: dt.date, empty: str) -> None:
    lessons = await _run(update, start, end)
    if lessons is not None:
        await _send_text(update, to_html(lessons, empty, today=_today()), html=True)


# Кнопки внизу чата: текст кнопки -> имя функции-команды
BUTTONS = [
    [("📅 Сегодня", "cmd_today"), ("➡️ Завтра", "cmd_tomorrow")],
    [("🗓 Неделя", "cmd_week"), ("⏭ След. неделя", "cmd_next")],
]
KEYBOARD = ReplyKeyboardMarkup([[KeyboardButton(text) for text, _ in row] for row in BUTTONS],
                               resize_keyboard=True)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP, reply_markup=KEYBOARD)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = _today()
    await _show(update, d, d, "Сегодня занятий нет 🎉")


async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = _today() + dt.timedelta(days=1)
    await _show(update, d, d, "Завтра занятий нет 🎉")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE, shift: int = 0) -> None:
    mon = _today() - dt.timedelta(days=_today().weekday()) + dt.timedelta(weeks=shift)
    await _show(update, mon, mon + dt.timedelta(days=6), "На этой неделе занятий нет")


async def cmd_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_week(update, ctx, shift=1)


async def cmd_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        d = dt.datetime.strptime(ctx.args[0], "%d.%m.%Y").date()
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Формат: /date 15.10.2026")
        return
    await _show(update, d, d, "В этот день занятий нет")


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    start, end = semester_bounds(_today())
    msg = await update.effective_message.reply_text(
        f"Выгружаю расписание {GROUP_CODE} с {start:%d.%m.%Y} по {end:%d.%m.%Y}… это займёт около минуты.")
    lessons = await _run(update, start, end)
    if lessons is None:
        return
    code = _group["code"]
    stem = f"{code}_{start}_{end}"
    meta = {"group": code, "group_id": _group["id"], "faculty": _group["faculty"],
            "from": str(start), "to": str(end)}
    files = [
        (f"{stem}.ics", to_ics(lessons, f"Расписание {code}"), "календарь (Google/Apple/Outlook)"),
        (f"{stem}.csv", to_csv(lessons), "таблица для Excel"),
        (f"{stem}.json", to_json(lessons, meta), "данные"),
        (f"{stem}.txt", to_text(lessons), "текст"),
    ]
    for name, content, caption in files:
        await update.effective_message.reply_document(
            InputFile(io.BytesIO(content.encode("utf-8")), filename=name), caption=caption)
    await msg.edit_text(f"Готово: {len(lessons)} занятий с {start:%d.%m.%Y} по {end:%d.%m.%Y}.")


async def cmd_captcha(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        img = await asyncio.to_thread(_locked, client.captcha_image)
    except (GubkinError, OSError) as e:
        await update.effective_message.reply_text(f"Не удалось загрузить капчу: {e}")
        return
    ctx.user_data["captcha"] = True
    await update.effective_message.reply_photo(img, caption="Введите символы с картинки ответным сообщением")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    for row in BUTTONS:  # нажата кнопка — выполняем соответствующую команду
        for label, func_name in row:
            if text == label:
                ctx.user_data.pop("captcha", None)
                await globals()[func_name](update, ctx)
                return
    if not ctx.user_data.pop("captcha", False):
        await update.effective_message.reply_text(HELP, reply_markup=KEYBOARD)
        return
    try:
        ok = await asyncio.to_thread(_locked, client.validate_captcha, update.effective_message.text)
    except (GubkinError, OSError) as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(
        "Капча принята ✅ Повторите команду." if ok else "Неверно. Отправьте /captcha ещё раз.")


# ---- утренняя рассылка

def _load_subscribers() -> set[int]:
    try:
        return set(json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _save_subscribers(ids: set[int]) -> None:
    SUBSCRIBERS_FILE.write_text(json.dumps(sorted(ids)), encoding="utf-8")


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ids = _load_subscribers()
    ids.add(update.effective_chat.id)
    _save_subscribers(ids)
    await update.effective_message.reply_text(
        f"Готово! Каждое утро в {MORNING:%H:%M} пришлю расписание на день (если есть пары). "
        "Отписаться — /unsubscribe")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ids = _load_subscribers()
    ids.discard(update.effective_chat.id)
    _save_subscribers(ids)
    await update.effective_message.reply_text("Вы отписались от утренней рассылки.")


async def morning_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ids = _load_subscribers()
    if not ids:
        return
    d = _today()
    try:
        lessons, _ = await asyncio.to_thread(_fetch, d, d)
    except (GubkinError, OSError):
        log.exception("morning fetch failed")
        return
    if not lessons:  # в выходной не беспокоим
        return
    text = "Доброе утро! ☀️ Расписание на сегодня:\n\n" + to_html(lessons, today=d)
    for chat_id in ids:
        try:
            for part in _split(text):
                await ctx.bot.send_message(chat_id, part, parse_mode="HTML")
        except Exception:  # пользователь заблокировал бота и т. п.
            log.warning("cannot send to %s", chat_id)


async def prefetch_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Заранее подгружает текущую и следующую неделю в кэш — тогда /today, /week и кнопки
    отвечают мгновенно, без ожидания сайта."""
    mon = _today() - dt.timedelta(days=_today().weekday())
    try:
        await asyncio.to_thread(_fetch, mon, mon + dt.timedelta(days=13))
    except CaptchaRequired:
        log.warning("prefetch: сайт просит капчу — отправьте боту /captcha")
    except (GubkinError, OSError) as e:
        log.warning("prefetch failed: %s", e)


# ---- команды из заданий (my_commands.py)

NOT_DONE = "🛠 Эта команда ещё не готова — допишите функцию {} в файле my_commands.py"


async def _reply_my(update: Update, result, func_name: str) -> None:
    if result is None:
        await update.effective_message.reply_text(NOT_DONE.format(func_name))
    elif isinstance(result, list):
        await _send_text(update, "\n".join(map(str, result)) or "Пусто")
    else:
        await _send_text(update, str(result) or "Пусто")


def _week_bounds() -> tuple[dt.date, dt.date]:
    mon = _today() - dt.timedelta(days=_today().weekday())
    return mon, mon + dt.timedelta(days=6)


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_my(update, my.about_text(), "about_text")


async def cmd_count(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lessons = await _run(update, *_week_bounds())
    if lessons is not None:
        await _reply_my(update, my.count_text(lessons), "count_text")


async def _cmd_kind(update: Update, kind: str) -> None:
    lessons = await _run(update, *_week_bounds())
    if lessons is None:
        return
    result = my.only_kind(lessons, kind)
    if result is None:
        await _reply_my(update, None, "only_kind")
    else:
        await _send_text(update, to_html(result, f"На этой неделе нет пар типа «{kind}»", today=_today()),
                         html=True)


async def cmd_lectures(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_kind(update, "Лекция")


async def cmd_labs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_kind(update, "Лабораторная")


async def cmd_first(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = _today() + dt.timedelta(days=1)
    lessons = await _run(update, d, d)
    if lessons is not None:
        await _reply_my(update, my.first_lesson_text(lessons), "first_lesson_text")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("Считаю по всему семестру, подождите около минуты…")
    lessons = await _run(update, *semester_bounds(_today()))
    if lessons is not None:
        await _reply_my(update, my.stats_text(lessons), "stats_text")


async def cmd_free(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lessons = await _run(update, *_week_bounds())
    if lessons is not None:
        await _reply_my(update, my.free_days(lessons), "free_days")


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


async def track_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Запоминает каждого, кто пишет боту. О новых людях сообщает администратору."""
    user = update.effective_user
    if user is None or user.is_bot:
        return
    now = dt.datetime.now(MSK).strftime("%Y-%m-%d %H:%M")
    text = update.effective_message.text if update.effective_message else ""
    with _users_lock:
        users = _load_users()
        key = str(user.id)
        is_new = key not in users
        u = users.setdefault(key, {"first": now, "count": 0})
        u.update(name=user.full_name, username=user.username or "", last=now,
                 count=u.get("count", 0) + 1, last_text=(text or "")[:40])
        try:
            USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            log.warning("cannot save users.json")
    admin = _admin_id()
    if is_new and admin and user.id != admin:
        try:
            await ctx.bot.send_message(admin, f"🆕 Новый пользователь: {_user_label(u)}\nid: <code>{user.id}</code>",
                                       parse_mode="HTML")
        except Exception:
            log.warning("cannot notify admin")


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
    lines = [f"👥 <b>Пользователей: {len(users)}</b> · сегодня активны: {active}", ""]
    for uid, u in sorted(users.items(), key=lambda kv: kv[1].get("last", ""), reverse=True)[:50]:
        last = u.get("last", "")
        last = f"{last[8:10]}.{last[5:7]} {last[11:]}" if len(last) >= 16 else last
        lines.append(f"• {_user_label(u)}\n   запросов: {u.get('count', 0)} · последний: {last}"
                     f" · id <code>{uid}</code>")
    if len(users) > 50:
        lines.append(f"\n…и ещё {len(users) - 50}")
    for part in _split("\n".join(lines)):
        await update.effective_message.reply_text(part, parse_mode="HTML")


def _read_setting(name: str) -> str | None:
    """Первая непустая строка файла рядом с bot.py (например token.txt), или None."""
    try:
        for line in (DATA_DIR / name).read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                return line.strip()
    except OSError:
        pass
    return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # токен и прокси можно задать переменной окружения или файлом рядом с bot.py
    token = os.environ.get("BOT_TOKEN") or _read_setting("token.txt")
    if not token:
        raise SystemExit("Нет токена бота. Создайте рядом с bot.py файл token.txt и вставьте в него "
                         "токен от @BotFather (или задайте переменную BOT_TOKEN).")
    proxy = os.environ.get("TG_PROXY") or _read_setting("proxy.txt")
    builder = (Application.builder().token(token)
               .connect_timeout(30).read_timeout(30).write_timeout(30)
               .get_updates_connect_timeout(30).get_updates_read_timeout(30)
               # команды обрабатываются параллельно: /start не ждёт, пока грузится чужой /today
               .concurrent_updates(True))
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
        log.info("Telegram via proxy %s", proxy.split("@")[-1])
    app = builder.build()
    # group=-1: срабатывает раньше остальных обработчиков на каждое сообщение
    app.add_handler(TypeHandler(Update, track_user), group=-1)
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("captcha", cmd_captcha))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    # команды из заданий — чтобы добавить свою, допишите строку по образцу
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("count", cmd_count))
    app.add_handler(CommandHandler("lectures", cmd_lectures))
    app.add_handler(CommandHandler("labs", cmd_labs))
    app.add_handler(CommandHandler("first", cmd_first))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("free", cmd_free))
    app.job_queue.run_daily(morning_job, time=MORNING)
    # подгрузка расписания при старте и каждые 20 минут (кэш живёт 30 минут)
    app.job_queue.run_repeating(prefetch_job, interval=20 * 60, first=5)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # старые сообщения, отправленные, пока бот был выключен, пропускаем —
    # иначе при запуске он долго отвечает на каждое из них
    # если Telegram пока недоступен (VPN ещё не включён), не падаем, а пробуем снова
    log.info("Подключаюсь к Telegram... Если долго висит — проверьте VPN/прокси.")
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
