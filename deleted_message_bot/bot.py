"""Telegram Business bot that reports deleted and edited messages.

The bot is connected through Settings -> Telegram Business -> Chatbots
("Автоматизация чатов"). Telegram only sends the IDs of deleted messages,
so every incoming business message is cached in SQLite and looked up when
a deletion arrives.
"""

import asyncio
import html
import logging
import os
import sqlite3
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    Message,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.getenv("DB_PATH", "messages.db")
KEEP_DAYS = int(os.getenv("KEEP_DAYS", "30"))
# Also report messages that the account owner deletes or edits.
NOTIFY_OWN = os.getenv("NOTIFY_OWN", "false").lower() in ("1", "true", "yes")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("deleted-bot")

db = sqlite3.connect(DB_PATH)
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS connections (
        id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL,
        owner_chat_id INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        connection_id TEXT NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        sender_id INTEGER,
        sender_name TEXT,
        text TEXT,
        media_type TEXT,
        file_id TEXT,
        date INTEGER NOT NULL,
        PRIMARY KEY (connection_id, chat_id, message_id)
    );
    """
)
db.commit()

MEDIA_TYPES = (
    "photo", "video", "animation", "voice", "video_note",
    "audio", "document", "sticker",
)
MEDIA_LABELS = {
    "photo": "🖼 Фото", "video": "🎬 Видео", "animation": "🎞 GIF",
    "voice": "🎤 Голосовое", "video_note": "⏺ Кружок", "audio": "🎵 Аудио",
    "document": "📎 Файл", "sticker": "🩷 Стикер",
}

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def extract_media(message: Message) -> tuple[str | None, str | None]:
    for kind in MEDIA_TYPES:
        value = getattr(message, kind)
        if value:
            if kind == "photo":
                value = value[-1]  # largest size
            return kind, value.file_id
    return None, None


def sender_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return message.chat.full_name or "Неизвестно"
    name = user.full_name
    if user.username:
        name += f" (@{user.username})"
    return name


async def get_connection(connection_id: str) -> tuple[int, int] | None:
    row = db.execute(
        "SELECT owner_id, owner_chat_id FROM connections WHERE id = ?",
        (connection_id,),
    ).fetchone()
    if row:
        return row
    try:
        conn = await bot.get_business_connection(connection_id)
    except Exception:
        log.exception("Cannot fetch business connection %s", connection_id)
        return None
    save_connection(conn)
    return conn.user.id, conn.user_chat_id


def save_connection(conn: BusinessConnection) -> None:
    db.execute(
        "INSERT OR REPLACE INTO connections VALUES (?, ?, ?)",
        (conn.id, conn.user.id, conn.user_chat_id),
    )
    db.commit()


def save_message(message: Message) -> None:
    media_type, file_id = extract_media(message)
    db.execute(
        "INSERT OR REPLACE INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            message.business_connection_id,
            message.chat.id,
            message.message_id,
            message.from_user.id if message.from_user else None,
            sender_name(message),
            message.text or message.caption,
            media_type,
            file_id,
            int(message.date.timestamp()),
        ),
    )
    db.commit()


def cleanup() -> None:
    db.execute(
        "DELETE FROM messages WHERE date < ?",
        (int(time.time()) - KEEP_DAYS * 86400,),
    )
    db.commit()


async def send_report(chat_id: int, header: str, text: str | None,
                      media_type: str | None, file_id: str | None) -> None:
    body = header
    if text:
        body += f"\n\n<blockquote>{html.escape(text)}</blockquote>"
    if media_type:
        body += f"\n\n{MEDIA_LABELS.get(media_type, media_type)}"

    if not file_id:
        await bot.send_message(chat_id, body)
        return

    try:
        if media_type in ("sticker", "video_note"):
            # These types cannot carry a caption.
            await bot.send_message(chat_id, body)
            send = getattr(bot, f"send_{media_type}")
            await send(chat_id, file_id)
        else:
            send = getattr(bot, f"send_{media_type}")
            await send(chat_id, file_id, caption=body[:1024])
    except Exception:
        log.exception("Failed to resend media")
        await bot.send_message(chat_id, body + "\n\n<i>(медиа недоступно)</i>")


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "👋 Я сохраняю сообщения из ваших личных чатов и присылаю их сюда, "
        "если собеседник их удалит или изменит.\n\n"
        "Подключение: <b>Настройки → Telegram для бизнеса → "
        "Автоматизация чатов</b> → укажите меня → выберите чаты.\n\n"
        "⚠️ Нужен Telegram Premium. Я вижу только сообщения, полученные "
        "<b>после</b> подключения."
    )


@dp.business_connection()
async def on_connection(conn: BusinessConnection) -> None:
    save_connection(conn)
    try:
        if conn.is_enabled:
            await bot.send_message(
                conn.user_chat_id,
                "✅ Бот подключён. Слежу за удалёнными и изменёнными сообщениями.",
            )
        else:
            await bot.send_message(conn.user_chat_id, "⛔️ Бот отключён.")
    except Exception:
        log.warning("Owner %s has not started the bot yet", conn.user_chat_id)


@dp.business_message()
async def on_business_message(message: Message) -> None:
    save_message(message)


@dp.edited_business_message()
async def on_edited(message: Message) -> None:
    connection = await get_connection(message.business_connection_id)
    old = db.execute(
        "SELECT text FROM messages WHERE connection_id = ? AND chat_id = ? "
        "AND message_id = ?",
        (message.business_connection_id, message.chat.id, message.message_id),
    ).fetchone()
    save_message(message)
    if not connection:
        return
    owner_id, owner_chat_id = connection
    if message.from_user and message.from_user.id == owner_id and not NOTIFY_OWN:
        return

    new_text = message.text or message.caption or ""
    old_text = old[0] if old and old[0] else None
    if old_text == new_text:
        return
    report = (
        f"✏️ <b>{html.escape(sender_name(message))}</b> изменил(а) сообщение\n\n"
        f"<b>Было:</b>\n<blockquote>"
        f"{html.escape(old_text) if old_text else '<i>нет в архиве</i>'}"
        f"</blockquote>\n<b>Стало:</b>\n<blockquote>{html.escape(new_text)}</blockquote>"
    )
    await bot.send_message(owner_chat_id, report)


@dp.deleted_business_messages()
async def on_deleted(event: BusinessMessagesDeleted) -> None:
    connection = await get_connection(event.business_connection_id)
    if not connection:
        return
    owner_id, owner_chat_id = connection
    chat_name = html.escape(event.chat.full_name or str(event.chat.id))

    missing = 0
    for message_id in event.message_ids:
        row = db.execute(
            "SELECT sender_id, sender_name, text, media_type, file_id, date "
            "FROM messages WHERE connection_id = ? AND chat_id = ? "
            "AND message_id = ?",
            (event.business_connection_id, event.chat.id, message_id),
        ).fetchone()
        if not row:
            missing += 1
            continue
        sender_id, name, text, media_type, file_id, date = row
        if sender_id == owner_id and not NOTIFY_OWN:
            continue
        sent_at = time.strftime("%d.%m.%Y %H:%M", time.localtime(date))
        header = (
            f"🗑 <b>{html.escape(name)}</b> удалил(а) сообщение\n"
            f"Чат: {chat_name} · отправлено {sent_at}"
        )
        await send_report(owner_chat_id, header, text, media_type, file_id)
        await asyncio.sleep(0.05)  # stay under flood limits on bulk deletes

    if missing:
        await bot.send_message(
            owner_chat_id,
            f"🗑 В чате {chat_name} удалено сообщений: {missing}, "
            "но их нет в архиве (они пришли до подключения бота).",
        )


async def cleanup_loop() -> None:
    while True:
        cleanup()
        await asyncio.sleep(3600)


async def main() -> None:
    asyncio.create_task(cleanup_loop())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
