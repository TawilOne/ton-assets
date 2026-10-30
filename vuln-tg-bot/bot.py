"""
VulnShopBot — НАМЕРЕННО УЯЗВИМЫЙ Telegram-бот для тренировки поиска уязвимостей.

!!! НЕ ЗАПУСКАЙ НА ПРОДЕ И НЕ ДАВАЙ ДОСТУП ЧУЖИМ !!!
Бот содержит RCE (выполнение команд на сервере). Запускай только в Docker
и только с ALLOWED_USERS, где перечислены твои Telegram ID.
"""

import base64
import json
import logging
import os
import pickle
import secrets
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

from jinja2 import Template
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DOCS_DIR = BASE / "docs"
DB_PATH = DATA / "shop.db"
FLAGS_PATH = DATA / "flags.json"

FLAG_NAMES = [
    "sqli", "idor", "callback", "logic", "admin", "path",
    "cmdi", "pickle", "ssti", "debug", "brute",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vulnshop")


# ---------------------------------------------------------------- setup

def load_flags() -> dict:
    """Флаги генерируются случайно при первом запуске, чтобы их нельзя было подсмотреть в коде."""
    DATA.mkdir(exist_ok=True)
    if FLAGS_PATH.exists():
        return json.loads(FLAGS_PATH.read_text())
    flags = {name: f"FLAG{{{name}_{secrets.token_hex(6)}}}" for name in FLAG_NAMES}
    FLAGS_PATH.write_text(json.dumps(flags, indent=2))
    return flags


FLAGS = load_flags()

# Флаги, которые лежат в файлах и достаются через чтение файловой системы.
(DATA / "secret").mkdir(exist_ok=True)
(DATA / "secret" / "flag.txt").write_text(FLAGS["path"] + "\n")
(DATA / "cmdi_flag.txt").write_text(FLAGS["cmdi"] + "\n")
(DATA / "pickle_flag.txt").write_text(FLAGS["pickle"] + "\n")

PROMO_CODE = f"{secrets.randbelow(1000):03d}"

CONFIG = {
    "shop_name": "VulnShop",
    "version": "0.1-beta",
    "internal_api_key": FLAGS["ssti"],
}

DEBUG_INFO = {"db": str(DB_PATH), "env": "dev", "maintenance_token": FLAGS["debug"]}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, name TEXT, balance INTEGER,
                greeting TEXT, role TEXT DEFAULT 'user', solved TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY, name TEXT, description TEXT,
                price INTEGER, in_stock INTEGER
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, item TEXT, details TEXT
            );
            CREATE TABLE IF NOT EXISTS secrets (id INTEGER PRIMARY KEY, name TEXT, value TEXT);
            """
        )
        if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO products VALUES (?, ?, ?, ?, ?)",
                [
                    (1, "Кружка хакера", "Чёрная, как твой терминал", 50, 1),
                    (2, "Стикерпак", "Наклейки на ноутбук", 20, 1),
                    (3, "Золотой флаг", "Легендарный товар", 1_000_000_000, 0),
                ],
            )
            conn.execute(
                "INSERT INTO users (id, name, balance, greeting, role) VALUES (1, 'admin', 999999, '', 'admin')"
            )
            conn.execute(
                "INSERT INTO orders (user_id, item, details) VALUES (1, 'Служебный заказ', ?)",
                (f"Пароль от склада: {FLAGS['idor']}",),
            )
            conn.execute("INSERT INTO secrets (name, value) VALUES ('db_backup_key', ?)", (FLAGS["sqli"],))


def get_user(update: Update) -> sqlite3.Row:
    tg = update.effective_user
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (tg.id,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (id, name, balance, greeting) VALUES (?, ?, 100, ?)",
                (tg.id, tg.first_name, "Привет, {{ user.name }}!"),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (tg.id,)).fetchone()
    return row


async def reply(update: Update, text: str) -> None:
    await update.effective_message.reply_text(text[:4000])


# ---------------------------------------------------------------- access guard (это НЕ уязвимость)

ALLOWED = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x}


async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in ALLOWED:
        log.warning("Blocked user %s", user.id if user else None)
        raise ApplicationHandlerStop


# ---------------------------------------------------------------- basic commands

HELP = """🛒 VulnShop — самый надёжный магазин в Telegram (нет)

/shop — витрина
/search <текст> — поиск товаров
/balance — баланс
/transfer <user_id> <сумма> — перевести деньги
/orders — мои заказы
/order <id> — детали заказа
/vip — VIP-зал (от 1 000 000 монет)
/promo <код> — активировать промокод
/doc <имя> — документы (rules.txt, faq.txt)
/ping <host> — проверить доступность сервера
/setgreeting <текст> — своё приветствие (поддерживает {{ user.name }})
/greet — показать приветствие
/export — бэкап настроек
/import <base64> — восстановить настройки
/flag <FLAG{...}> — сдать флаг
/score — прогресс"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    await reply(update, f"Твой ID: {user['id']}\n\n{HELP}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(update, HELP)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    await reply(update, f"Баланс: {user['balance']} монет")


# ---------------------------------------------------------------- shop / callback tampering

async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    get_user(update)
    with db() as conn:
        products = conn.execute("SELECT * FROM products").fetchall()
    buttons = []
    for p in products:
        if p["in_stock"]:
            buttons.append([InlineKeyboardButton(
                f"{p['name']} — {p['price']}", callback_data=f"buy:{p['id']}:{p['price']}")])
        else:
            buttons.append([InlineKeyboardButton(
                f"{p['name']} — нет в наличии", callback_data=f"soldout:{p['id']}")])
    await update.effective_message.reply_text("Витрина:", reply_markup=InlineKeyboardMarkup(buttons))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = get_user(update)
    parts = query.data.split(":")

    if parts[0] == "soldout":
        await query.answer("Товара нет в наличии 😢", show_alert=True)
        return

    if parts[0] == "buy":
        item_id, price = int(parts[1]), int(parts[2])
        with db() as conn:
            product = conn.execute("SELECT * FROM products WHERE id = ?", (item_id,)).fetchone()
            if product is None:
                await query.answer("Нет такого товара")
                return
            if user["balance"] < price:
                await query.answer("Недостаточно средств", show_alert=True)
                return
            conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (price, user["id"]))
            details = f"Оплачено {price}"
            if item_id == 3:
                details += f". Твой золотой флаг: {FLAGS['callback']}"
            cur = conn.execute(
                "INSERT INTO orders (user_id, item, details) VALUES (?, ?, ?)",
                (user["id"], product["name"], details),
            )
        await query.answer("Куплено!")
        await reply(update, f"Заказ #{cur.lastrowid}: {product['name']}\n{details}")


# ---------------------------------------------------------------- SQL injection

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = " ".join(context.args)
    sql = f"SELECT id, name, price FROM products WHERE in_stock = 1 AND name LIKE '%{q}%'"
    with db() as conn:
        rows = conn.execute(sql).fetchall()
    if not rows:
        await reply(update, "Ничего не найдено")
        return
    await reply(update, "\n".join(f"#{r[0]} {r[1]} — {r[2]}" for r in rows))


# ---------------------------------------------------------------- IDOR

async def orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    with db() as conn:
        rows = conn.execute("SELECT id, item FROM orders WHERE user_id = ?", (user["id"],)).fetchall()
    await reply(update, "\n".join(f"#{r['id']} {r['item']}" for r in rows) or "Заказов нет")


async def order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    order_id = int(context.args[0])
    with db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        await reply(update, "Заказ не найден")
        return
    await reply(update, f"Заказ #{row['id']}\nТовар: {row['item']}\n{row['details']}")


# ---------------------------------------------------------------- business logic

async def transfer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    if len(context.args) != 2:
        await reply(update, "Использование: /transfer <user_id> <сумма>")
        return
    target, amount = int(context.args[0]), int(context.args[1])
    if target == user["id"]:
        await reply(update, "Нельзя переводить самому себе")
        return
    if amount > user["balance"]:
        await reply(update, "Недостаточно средств")
        return
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (target,)).fetchone() is None:
            await reply(update, "Получатель не найден")
            return
        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (amount, user["id"]))
        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (amount, target))
    await reply(update, f"Переведено {amount} монет пользователю {target}")


async def vip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    if user["balance"] >= 1_000_000:
        await reply(update, f"Добро пожаловать в VIP-зал! {FLAGS['logic']}")
    else:
        await reply(update, "Вход только для миллионеров")


# ---------------------------------------------------------------- broken access control

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.first_name.strip().lower() != "admin":
        await reply(update, "Доступ запрещён. Эта команда только для администратора.")
        return
    with db() as conn:
        users = conn.execute("SELECT id, name, balance FROM users").fetchall()
    lines = [f"{u['id']} {u['name']} {u['balance']}" for u in users]
    await reply(update, f"Админ-панель\nФлаг: {FLAGS['admin']}\nПромокод: {PROMO_CODE}\n\n" + "\n".join(lines))


# ---------------------------------------------------------------- path traversal

async def doc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = " ".join(context.args)
    name = name.replace("../", "")  # защита от path traversal
    path = os.path.join(DOCS_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            await reply(update, f.read())
    except FileNotFoundError:
        await reply(update, "Документ не найден")


# ---------------------------------------------------------------- command injection

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    host = " ".join(context.args)
    if any(c in host for c in ";&"):
        await reply(update, "Хакер обнаружен! 🚨")
        return
    result = subprocess.run(
        f"ping -c 1 -W 2 {host}", shell=True, capture_output=True, text=True, timeout=10, cwd=DATA
    )
    await reply(update, (result.stdout + result.stderr) or "(пусто)")


# ---------------------------------------------------------------- insecure deserialization

async def export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    settings = {"name": user["name"], "greeting": user["greeting"]}
    await reply(update, base64.b64encode(pickle.dumps(settings)).decode())


async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    settings = pickle.loads(base64.b64decode(context.args[0]))
    with db() as conn:
        conn.execute(
            "UPDATE users SET name = ?, greeting = ? WHERE id = ?",
            (str(settings.get("name")), str(settings.get("greeting")), user["id"]),
        )
    await reply(update, f"Настройки восстановлены: {settings}")


# ---------------------------------------------------------------- SSTI

async def setgreeting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    text = update.effective_message.text.partition(" ")[2]
    with db() as conn:
        conn.execute("UPDATE users SET greeting = ? WHERE id = ?", (text, user["id"]))
    await reply(update, "Приветствие сохранено, проверь через /greet")


async def greet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    rendered = Template(user["greeting"]).render(user=dict(user), config=CONFIG)
    await reply(update, rendered or "(пусто)")


# ---------------------------------------------------------------- brute force

async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code = context.args[0] if context.args else ""
    if code == PROMO_CODE:
        await reply(update, f"Промокод активирован! {FLAGS['brute']}")
    else:
        await reply(update, "Неверный промокод")


# ---------------------------------------------------------------- scoreboard

async def flag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    submitted = " ".join(context.args).strip()
    found = next((name for name, value in FLAGS.items() if value == submitted), None)
    if found is None:
        await reply(update, "❌ Неверный флаг")
        return
    solved = set(json.loads(user["solved"]))
    solved.add(found)
    with db() as conn:
        conn.execute("UPDATE users SET solved = ? WHERE id = ?", (json.dumps(sorted(solved)), user["id"]))
    await reply(update, f"✅ Верно! Уязвимость «{found}». Прогресс: {len(solved)}/{len(FLAGS)}")


async def score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = get_user(update)
    solved = json.loads(user["solved"])
    await reply(update, f"Решено {len(solved)}/{len(FLAGS)}: {', '.join(solved) or '—'}")


# ---------------------------------------------------------------- verbose errors

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    tb = "".join(traceback.format_exception(context.error))
    log.error(tb)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            f"💥 Internal error\n{tb[-2500:]}\n\nDEBUG: {DEBUG_INFO}"[:4000]
        )


# ---------------------------------------------------------------- main

def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        sys.exit("Задай BOT_TOKEN")
    if not ALLOWED:
        sys.exit("Задай ALLOWED_USERS=<твой_telegram_id> — бот уязвим к RCE, чужих пускать нельзя")

    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(TypeHandler(Update, guard), group=-1)

    commands = {
        "start": start, "help": help_cmd, "balance": balance, "shop": shop,
        "search": search, "orders": orders, "order": order, "transfer": transfer,
        "vip": vip, "admin": admin, "doc": doc, "ping": ping, "export": export,
        "import": import_cmd, "setgreeting": setgreeting, "greet": greet,
        "promo": promo, "flag": flag, "score": score,
    }
    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    log.info("VulnShopBot started, allowed users: %s", ALLOWED)
    app.run_polling()


if __name__ == "__main__":
    main()
