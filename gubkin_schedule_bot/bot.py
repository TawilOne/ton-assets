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

from telegram import InputFile, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import my_commands as my
from gubkin_api import CaptchaRequired, GubkinClient, GubkinError
from schedule import fetch_range, semester_bounds, to_csv, to_ics, to_json, to_text

MSK = ZoneInfo("Europe/Moscow")
GROUP_CODE = os.environ.get("GROUP_CODE", "ГМ-26-11")
FACULTY_HINT = os.environ.get("FACULTY_HINT", "геолог")
TG_LIMIT = 4000
MORNING = dt.time(7, 0, tzinfo=MSK)  # время утренней рассылки
SUBSCRIBERS_FILE = pathlib.Path(os.environ.get("SUBSCRIBERS_FILE", "subscribers.json"))

log = logging.getLogger("gubkin-bot")
DATA_DIR = pathlib.Path(__file__).resolve().parent
client = GubkinClient(cookie_file=str(DATA_DIR / "cookies.json"))
GROUP_FILE = DATA_DIR / "group.json"
_lock = threading.Lock()  # одна сессия сайта на всех — запросы по очереди
_group: dict = {"id": os.environ.get("GROUP_ID"), "code": GROUP_CODE, "faculty": ""}

HELP = (
    f"Расписание РГУ им. Губкина, группа {GROUP_CODE}\n\n"
    "/today — на сегодня\n"
    "/tomorrow — на завтра\n"
    "/week — текущая неделя\n"
    "/next — следующая неделя\n"
    "/date ДД.ММ.ГГГГ — на конкретный день\n"
    "/all — выгрузить ВСЁ расписание семестра файлами (JSON, CSV для Excel, ICS для календаря, TXT)\n"
    "/subscribe — присылать расписание каждое утро в 7:00\n"
    "/unsubscribe — отписаться от рассылки\n"
    "/captcha — если сайт попросил капчу\n\n"
    "Команды из заданий (my_commands.py): /about /count /lectures /labs /first /stats /free"
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
        return fetch_range(client, _group["id"], _group["code"], start, end)


async def _run(update: Update, start: dt.date, end: dt.date):
    try:
        await update.effective_chat.send_action("typing")  # «печатает…», пока грузим с сайта
    except Exception:
        pass
    t0 = time.monotonic()
    try:
        lessons = await asyncio.to_thread(_fetch, start, end)
        log.info("schedule %s..%s loaded in %.1f s", start, end, time.monotonic() - t0)
        return lessons
    except CaptchaRequired:
        await update.effective_message.reply_text("Сайт попросил капчу. Отправьте /captcha и введите код с картинки.")
    except (GubkinError, OSError) as e:
        log.exception("fetch failed")
        await update.effective_message.reply_text(f"Не удалось получить расписание: {e}")
    return None


async def _send_text(update: Update, text: str) -> None:
    while text:  # Telegram ограничивает длину сообщения
        cut = len(text) if len(text) <= TG_LIMIT else text.rfind("\n", 0, TG_LIMIT) + 1 or TG_LIMIT
        await update.effective_message.reply_text(text[:cut])
        text = text[cut:]


def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


async def _show(update: Update, start: dt.date, end: dt.date, empty: str) -> None:
    lessons = await _run(update, start, end)
    if lessons is not None:
        await _send_text(update, to_text(lessons, empty))


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP)


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
    if not ctx.user_data.pop("captcha", False):
        await update.effective_message.reply_text(HELP)
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
        lessons = await asyncio.to_thread(_fetch, d, d)
    except (GubkinError, OSError):
        log.exception("morning fetch failed")
        return
    if not lessons:  # в выходной не беспокоим
        return
    text = "Доброе утро! ☀️ Сегодня:\n\n" + to_text(lessons)
    for chat_id in ids:
        try:
            await ctx.bot.send_message(chat_id, text[:TG_LIMIT])
        except Exception:  # пользователь заблокировал бота и т. п.
            log.warning("cannot send to %s", chat_id)


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
        await _send_text(update, to_text(result, f"На этой неделе нет пар типа «{kind}»"))


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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Укажите токен бота: BOT_TOKEN=... python bot.py")
    proxy = os.environ.get("TG_PROXY")
    builder = (Application.builder().token(token)
               .connect_timeout(30).read_timeout(30).write_timeout(30)
               .get_updates_connect_timeout(30).get_updates_read_timeout(30)
               # команды обрабатываются параллельно: /start не ждёт, пока грузится чужой /today
               .concurrent_updates(True))
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
        log.info("Telegram via proxy %s", proxy.split("@")[-1])
    app = builder.build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # старые сообщения, отправленные, пока бот был выключен, пропускаем —
    # иначе при запуске он долго отвечает на каждое из них
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
