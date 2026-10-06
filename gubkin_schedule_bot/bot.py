#!/usr/bin/env python3
"""Telegram-бот расписания РГУ им. Губкина для группы ГМ-26-11 (факультет геологии).

Запуск:  BOT_TOKEN=123:abc python bot.py
Переменные окружения:
    BOT_TOKEN     — токен от @BotFather (обязательно)
    GROUP_CODE    — код группы (по умолчанию ГМ-26-11)
    GROUP_ID      — id группы на сайте, если известен (иначе найдётся по коду)
    FACULTY_HINT  — подстрока названия факультета для поиска (по умолчанию «геолог»)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import os
import threading
from zoneinfo import ZoneInfo

from telegram import InputFile, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from gubkin_api import CaptchaRequired, GubkinClient, GubkinError
from schedule import fetch_range, semester_bounds, to_csv, to_ics, to_json, to_text

MSK = ZoneInfo("Europe/Moscow")
GROUP_CODE = os.environ.get("GROUP_CODE", "ГМ-26-11")
FACULTY_HINT = os.environ.get("FACULTY_HINT", "геолог")
TG_LIMIT = 4000

log = logging.getLogger("gubkin-bot")
client = GubkinClient()
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
    "/captcha — если сайт попросил капчу"
)


def _ensure_group() -> None:
    if _group["id"]:
        return
    f, g = client.find_group(GROUP_CODE, FACULTY_HINT)
    _group.update(id=g["id"], code=g.get("code") or GROUP_CODE, faculty=f.get("name", ""))
    log.info("group %s id=%s faculty=%s", _group["code"], _group["id"], _group["faculty"])


def _fetch(start: dt.date, end: dt.date):
    with _lock:
        _ensure_group()
        return fetch_range(client, _group["id"], _group["code"], start, end)


async def _run(update: Update, start: dt.date, end: dt.date):
    try:
        return await asyncio.to_thread(_fetch, start, end)
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
        img = await asyncio.to_thread(client.captcha_image)
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
        ok = await asyncio.to_thread(client.validate_captcha, update.effective_message.text)
    except (GubkinError, OSError) as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(
        "Капча принята ✅ Повторите команду." if ok else "Неверно. Отправьте /captcha ещё раз.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Укажите токен бота: BOT_TOKEN=... python bot.py")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("next", cmd_next))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("captcha", cmd_captcha))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
