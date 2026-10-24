#!/usr/bin/env python3
"""Telegram-бот расписания РГУ им. Губкина для всех групп нескольких факультетов (FACULTIES):
геологии и геофизики, автоматики и ВТ, химической технологии и экологии, экономики и управления,
разработки нефтяных и газовых месторождений, трубопроводного транспорта,
инженерной механики, комплексной безопасности ТЭК,
международного энергетического бизнеса, гуманитарного образования.
Каждый пользователь выбирает
факультет и группу (/group); те, кто пришёл до выбора групп, получили ГМ-26-11.

Запуск:  BOT_TOKEN=123:abc python bot.py
Переменные окружения:
    BOT_TOKEN     — токен от @BotFather (обязательно)
    GROUP_CODE    — группа по умолчанию (ГМ-26-11): по ней ищется факультет, её получают
                    пользователи, которые пришли до появления выбора группы
    GROUP_ID      — id этой группы на сайте, если известен (иначе найдётся по коду)
    FACULTY_HINT  — подстрока названия факультета для поиска (по умолчанию «геолог»)
    TG_PROXY      — прокси только для Telegram, если api.telegram.org недоступен
                    (например socks5://127.0.0.1:1080 или http://host:port).
                    Сайт Губкина при этом открывается напрямую.
    TG_API_BASE   — адрес пересыльщика Bot API (например https://имя.workers.dev),
                    если Telegram заблокирован, а VPN нет (файл tg_api.txt — то же самое).
    GUBKIN_OFFLINE=1 — на сайт не ходить, брать расписание из cache.json, который присылает
                    ноутбук (sync.py): для сервера, которого сайт Губкина не пускает.
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import io
import json
import logging
import os
import pathlib
import re
import threading
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, InputFile, KeyboardButton,
                      ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.error import Forbidden
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

import guard
import i18n
import my_commands as my
from gubkin_api import CaptchaRequired, GubkinClient, GubkinError, normalize_code, write_atomic
from schedule import (_minutes, fetch_range, locate_room, semester_bounds, slim_week, to_csv, to_html,
                      to_ics, to_json, to_text)

MSK = ZoneInfo("Europe/Moscow")
GROUP_CODE = normalize_code(os.environ.get("GROUP_CODE", "ГМ-26-11"))
FACULTY_HINT = os.environ.get("FACULTY_HINT", "геолог")
TG_LIMIT = 4000
MORNING = dt.time(7, 0, tzinfo=MSK)  # время утренней рассылки
SUBSCRIBERS_FILE = pathlib.Path(os.environ.get("SUBSCRIBERS_FILE", "subscribers.json"))

log = logging.getLogger("gubkin-bot")
DATA_DIR = pathlib.Path(__file__).resolve().parent
client = GubkinClient(cookie_file=str(DATA_DIR / "cookies.json"), cache_file=str(DATA_DIR / "cache.json"),
                      fallback_proxy=os.environ.get("GUBKIN_PROXY"),  # запасной путь к сайту, если напрямую не пускает
                      proxy=os.environ.get("GUBKIN_PROXY_ALWAYS"),  # на сервере: всегда через прокси
                      offline=os.environ.get("GUBKIN_OFFLINE") == "1")  # расписание присылает ноутбук (sync.py)
client.slim = slim_week  # в кэше — только нужные поля: кэш всего факультета большой
GROUP_FILE = DATA_DIR / "group.json"  # группа по умолчанию и её факультет
GROUPS_FILE = DATA_DIR / "groups.json"  # все группы факультетов — присылает ноутбук (sync.py)
USER_GROUPS_FILE = DATA_DIR / "user_groups.json"  # кто какую группу выбрал: {id пользователя: "ГМ-26-11"}
USERS_FILE = DATA_DIR / "users.json"  # кто пользуется ботом (видно только администратору)
_users_lock = threading.Lock()
_lock = threading.Lock()  # одна сессия сайта на всех — запросы по очереди
_group: dict = {"id": os.environ.get("GROUP_ID"), "code": GROUP_CODE, "faculty": ""}

def _ensure_group() -> None:
    if _group["id"]:
        return
    try:  # найденная группа запоминается в файл — не ищем её на сайте при каждом запуске
        saved = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
        if saved.get("code_wanted") == GROUP_CODE and saved.get("id"):
            _group.update(id=saved["id"], code=saved["code"], faculty=saved.get("faculty", ""),
                          faculty_id=saved.get("faculty_id"))
            return
    except (OSError, ValueError):
        pass
    f, g = client.find_group(GROUP_CODE, FACULTY_HINT)
    _group.update(id=g["id"], code=g.get("code") or GROUP_CODE, faculty=f.get("name", ""), faculty_id=f.get("id"))
    log.info("group %s id=%s faculty=%s", _group["code"], _group["id"], _group["faculty"])
    try:
        write_atomic(GROUP_FILE, json.dumps({**_group, "code_wanted": GROUP_CODE}, ensure_ascii=False))
    except OSError:
        pass


def _locked(func, *args):
    with _lock:
        return func(*args)


def _fetch(start: dt.date, end: dt.date, grp: dict):
    """Пары группы grp ({"id", "code"}) с start по end и признак «взято из старого сохранённого»."""
    with _lock:
        gid = grp.get("id")
        if not gid:  # группа по умолчанию, а список групп ещё не пришёл — id из group.json / с сайта
            _ensure_group()
            gid = _group["id"]
        client.stale = False
        lessons = fetch_range(client, gid, grp["code"], start, end)
        return lessons, client.stale


# ---- факультеты, группы и выбор группы

from faculties import DEFAULT_FACULTY, FACULTIES  # noqa: E402 — общий список с мини-играми (games.py)

_groups_cache: dict = {"mtime": None, "list": []}
_ug_lock = threading.Lock()


def _faculty_groups() -> list[dict]:
    """Все группы факультета [{"id": "10706", "code": "ГМ-26-11"}, ...], отсортированные по коду.
    Список присылает ноутбук (groups.json); пока его нет — только группа по умолчанию."""
    try:
        mtime = GROUPS_FILE.stat().st_mtime
    except OSError:
        mtime = None
    if mtime != _groups_cache["mtime"]:
        found: dict[str, dict] = {}
        try:
            data = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
            # новый формат: {"faculties": [{"key", "id", "name", "groups"}]}; старый — один факультет геологии
            facs = data.get("faculties") or [{"key": DEFAULT_FACULTY, "groups": data.get("groups") or []}]
            for f in facs:
                for g in f.get("groups") or []:
                    code = normalize_code(str(g.get("code") or ""))
                    if g.get("id") and code and code not in found:
                        found[code] = {"id": str(g["id"]), "code": code, "fac": f.get("key") or DEFAULT_FACULTY}
            _groups_cache.update(mtime=mtime, list=[found[c] for c in sorted(found)])
        except (OSError, ValueError, AttributeError, TypeError):
            # файл не прочитался (нет его или битый) — оставляем прежний список и попробуем в следующий раз;
            # раньше здесь запоминался пустой список, и у всех «пропадала» выбранная группа до нового файла
            log.warning("cannot read %s, keeping previous group list", GROUPS_FILE)
    groups = list(_groups_cache["list"])
    if not any(g["code"] == GROUP_CODE for g in groups):
        groups.append(_default_group())
        groups.sort(key=lambda g: g["code"])
    return groups


def _default_group() -> dict:
    gid = _group["id"]
    if not gid:
        try:
            saved = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
            if normalize_code(saved.get("code_wanted") or "") == GROUP_CODE:
                gid = saved.get("id")
        except (OSError, ValueError, AttributeError):
            pass
    return {"id": str(gid) if gid else None, "code": GROUP_CODE, "fac": DEFAULT_FACULTY}


def _find_group(code: str) -> dict | None:
    code = normalize_code(code or "")
    return next((g for g in _faculty_groups() if g["code"] == code), None) if code else None


def _load_user_groups() -> dict[str, str]:
    try:
        data = json.loads(USER_GROUPS_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _set_user_group(user_id, code: str) -> None:
    with _ug_lock:
        data = _load_user_groups()
        data[str(user_id)] = code
        try:
            write_atomic(USER_GROUPS_FILE, json.dumps(data, ensure_ascii=False))
        except OSError:
            log.warning("cannot save %s", USER_GROUPS_FILE)


def _migrate_user_groups() -> None:
    """Один раз, при первом запуске с выбором групп: все, кто уже пользовался ботом,
    получают ГМ-26-11 — раньше бот показывал только её."""
    if USER_GROUPS_FILE.exists():
        return
    old = set(_load_users()) | set(_load_subs())
    try:
        write_atomic(USER_GROUPS_FILE, json.dumps({uid: GROUP_CODE for uid in sorted(old)}, ensure_ascii=False))
        log.info("выбор групп: %d прежних пользователей получили группу %s", len(old), GROUP_CODE)
    except OSError:
        log.warning("cannot save %s", USER_GROUPS_FILE)


def _group_of(user_id) -> dict | None:
    """Группа пользователя или None, если он её ещё не выбрал."""
    code = _load_user_groups().get(str(user_id))
    if not code:
        return None
    # группа могла пропасть из списка (расформировали) — тогда считаем, что не выбрана
    return _find_group(code)


def _user_group(update: Update) -> dict | None:
    return _group_of(update.effective_user.id) if update.effective_user else None


def _year(code: str) -> int | None:
    """'ГМ-26-11' -> 26 (год поступления)."""
    m = re.search(r"-(\d{2})-", code)
    return int(m.group(1)) if m else None


def _year_label(year, lang: str) -> str:
    if year is None:
        return i18n.t("groups_other", lang)
    today = _today()
    first = (today.year if today.month >= 8 else today.year - 1) % 100  # учебный год начинается в сентябре
    n = first - year + 1
    return i18n.course(n, lang) if 1 <= n <= 6 else f"20{year:02d}"


def _faculties() -> list[dict]:
    """Факультеты, по которым есть группы, — в порядке FACULTIES."""
    keys = {g.get("fac") for g in _faculty_groups()}
    return [f for f in FACULTIES if f["key"] in keys]


def _faculty_label(key: str, lang: str) -> str:
    f = next((f for f in FACULTIES if f["key"] == key), None)
    return (f.get(lang) or f["ru"]) if f else key


def _by_year(fac: str | None = None) -> list[tuple]:
    """[(год поступления или None, [группы])] факультета fac, первый курс — первым."""
    out: dict = {}
    for g in _faculty_groups():
        if fac is None or g.get("fac") == fac:
            out.setdefault(_year(g["code"]), []).append(g)
    return sorted(out.items(), key=lambda kv: (kv[0] is None, -(kv[0] or 0)))


def _rows(buttons: list, n: int = 3) -> list[list]:
    return [buttons[i:i + n] for i in range(0, len(buttons), n)]


def _picker(lang: str, fac: str | None = None, year: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    """Выбор группы: факультет → курс → группа. Шаг, где выбирать не из чего, пропускается."""
    note = "" if _groups_cache["list"] else "\n\n" + i18n.t("group_list_loading", lang)
    facs = _faculties()
    if fac is None or fac not in [f["key"] for f in facs]:
        if len(facs) > 1:
            buttons = [[InlineKeyboardButton(f.get(lang) or f["ru"], callback_data=f"grp:f:{f['key']}")] for f in facs]
            return i18n.t("group_choose_faculty", lang) + note, InlineKeyboardMarkup(buttons)
        fac = facs[0]["key"] if facs else None
    back_to_facs = [InlineKeyboardButton(i18n.t("btn_back", lang), callback_data="grp:b")] if len(facs) > 1 else None
    years = _by_year(fac)
    head = _faculty_label(fac, lang) + "\n" if len(facs) > 1 else ""
    if year is None and len(years) > 1:
        buttons = [InlineKeyboardButton(_year_label(y, lang), callback_data=f"grp:y:{fac}:{'' if y is None else y}")
                   for y, _ in years]
        rows = _rows(buttons) + ([back_to_facs] if back_to_facs else [])
        return head + i18n.t("group_choose_year", lang) + note, InlineKeyboardMarkup(rows)
    wanted = None if year in (None, "") else int(year)
    groups = next((gs for y, gs in years if y == wanted), []) if len(years) > 1 else (years[0][1] if years else [])
    buttons = [InlineKeyboardButton(g["code"], callback_data=f"grp:s:{g['code']}")
               for g in groups if len(f"grp:s:{g['code']}".encode()) <= 64][:96]  # ограничения Telegram
    rows = _rows(buttons)
    if len(years) > 1:  # назад — к курсам этого факультета
        rows.append([InlineKeyboardButton(i18n.t("btn_back", lang), callback_data=f"grp:f:{fac}")])
    elif back_to_facs:
        rows.append(back_to_facs)
    return head + i18n.t("group_choose", lang) + note, InlineKeyboardMarkup(rows)


async def _ask_group(update: Update, head: str = "") -> None:
    lang = _lang(update)
    text, markup = _picker(lang)
    await update.effective_message.reply_text((head + "\n\n" if head else "") + text, reply_markup=markup)


async def cmd_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/group — выбрать группу; /group ГМ-26-12 — сразу задать."""
    lang = _lang(update)
    if ctx.args:
        code = " ".join(ctx.args)
        grp = _find_group(code)
        if grp:
            await _group_chosen(update, grp)
        else:
            await _ask_group(update, i18n.t("group_unknown", lang, code=normalize_code(code)))
        return
    await _ask_group(update)


async def _group_chosen(update: Update, grp: dict) -> None:
    lang = _lang(update)
    _set_user_group(update.effective_user.id, grp["code"])
    text = i18n.t("group_set", lang, code=grp["code"])
    if client.offline and grp["id"] and not client.has_group(grp["id"]):
        text += "\n\n" + i18n.t("group_pending", lang, code=grp["code"])
    # новое сообщение, а не правка старого: только так приходит нижняя панель кнопок
    await update.effective_message.reply_text(text, reply_markup=keyboard(lang))


async def on_group(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    lang = _lang(update)
    parts = (q.data or "").split(":")
    act, arg = (parts + ["", ""])[1:3]
    await q.answer()
    if act == "s":
        grp = _find_group(arg)
        if not grp:  # список групп обновился, а кнопки старые
            text, markup = _picker(lang)
            await _edit(q, text, markup)
            return
        await _edit(q, i18n.t("group_current", lang, code=grp["code"]), None)
        await _group_chosen(update, grp)
    elif act == "f":  # факультет выбран — его курсы
        await _edit(q, *_picker(lang, arg))
    elif act == "y":  # курс выбран — его группы; «grp:y:26» — кнопки до появления факультетов
        fac, year = (parts[2], parts[3]) if len(parts) > 3 else (DEFAULT_FACULTY, arg)
        await _edit(q, *_picker(lang, fac, year))
    elif act == "menu":  # кнопка в настройках — выбор группы новым сообщением
        await _ask_group(update)
    else:  # "b" — назад к выбору факультета
        await _edit(q, *_picker(lang))


async def _edit(q, text: str, markup) -> None:
    try:
        await q.edit_message_text(text, reply_markup=markup)
    except Exception:  # сообщение не изменилось или слишком старое
        pass


def _lang(update: Update) -> str:
    """Язык пользователя (выбирается в /settings)."""
    return i18n.get_lang(update.effective_user.id) if update.effective_user else "ru"


async def _run(update: Update, start: dt.date, end: dt.date, grp: dict | None = None):
    lang = _lang(update)
    grp = grp or _user_group(update)
    if not grp:  # группа ещё не выбрана — сначала выбор группы
        await _ask_group(update, i18n.t("group_first", lang))
        return None
    if client.offline and grp["id"] and not client.has_group(grp["id"]):
        await update.effective_message.reply_text(i18n.t("group_pending", lang, code=grp["code"]))
        return None
    try:
        await update.effective_chat.send_action("typing")  # «печатает…», пока грузим с сайта
    except Exception:
        pass
    t0 = time.monotonic()
    try:
        lessons, stale = await asyncio.to_thread(_fetch, start, end, grp)
        log.info("schedule %s %s..%s loaded in %.1f s%s", grp["code"], start, end, time.monotonic() - t0,
                 " (из сохранённого, сайт не ответил)" if stale else "")
        problems = guard.check_lessons(lessons)
        if problems:  # пришло не расписание (подмена данных) — не показываем, сообщаем об ошибке
            log.warning("подозрительное расписание %s: %s", grp["code"], "; ".join(problems[:3]))
            await update.effective_message.reply_text(i18n.t("data_error", lang))
            await _alert_admin(update, key="data:" + dt.date.today().isoformat(),
                               text=f"⚠️ В расписании группы {grp['code']} подозрительные данные, пользователям "
                                    f"показана ошибка: {problems[0]}")
            return None
        if stale and client.offline:
            await update.effective_message.reply_text(
                i18n.t("stale_offline", lang, ago=_ago(client.cache_age(), lang)), disable_web_page_preview=True)
        elif stale:
            await update.effective_message.reply_text(i18n.t("stale_site", lang))
        return lessons
    except CaptchaRequired:
        admin = update.effective_user and update.effective_user.id == _admin_id()
        await update.effective_message.reply_text(i18n.t("captcha_needed" if admin else "site_busy", lang))
    except (GubkinError, OSError) as e:
        log.exception("fetch failed")
        if "timed out" in str(e).lower() or "connect" in str(e).lower():
            msg = i18n.t("site_down", lang)
        else:
            msg = i18n.t("fetch_failed", lang, e=e)
        await update.effective_message.reply_text(msg)
    return None


def _ago(seconds: float | None, lang: str = "ru") -> str:
    if seconds is None:
        return i18n.t("long_ago", lang)
    hours = int(seconds // 3600)
    if hours < 24:
        return i18n.t("ago_hours", lang, n=hours)
    return i18n.days_ago(hours // 24, lang)


def _split(text: str) -> list[str]:
    """Режет длинный текст по переносам строк (Telegram ограничивает длину сообщения)."""
    parts = []
    while text:
        cut = len(text) if len(text) <= TG_LIMIT else text.rfind("\n", 0, TG_LIMIT) + 1 or TG_LIMIT
        parts.append(text[:cut])
        text = text[cut:]
    return parts


async def _send_text(update: Update, text: str, html: bool = False) -> None:
    kb = keyboard(_lang(update))
    for part in _split(text):
        await update.effective_message.reply_text(part, parse_mode="HTML" if html else None, reply_markup=kb)


def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


def _group_head(grp: dict) -> str:
    """Подпись над расписанием — чья это группа."""
    return f"👥 <b>{_esc_html(grp['code'])}</b>\n\n"


async def _show(update: Update, start: dt.date, end: dt.date, empty_key: str) -> None:
    grp = _user_group(update)
    lessons = await _run(update, start, end, grp)
    if lessons is not None:
        lang = _lang(update)
        await _send_text(update, _group_head(grp) + to_html(lessons, i18n.t(empty_key, lang), today=_today(),
                                                            lang=lang), html=True)


# Кнопки внизу чата: ключ текста кнопки (i18n.TEXT) -> имя функции-команды
BUTTONS = [
    [("btn_today", "cmd_today"), ("btn_tomorrow", "cmd_tomorrow")],
    [("btn_week", "cmd_week"), ("btn_next", "cmd_next")],
    [("btn_digest", "cmd_digest"), ("btn_group", "cmd_group")],  # рассылка в выбранное время, выбор группы
    [("btn_map", "cmd_map")],  # карта — главная кнопка, одна во всю ширину, последней строкой
]
# настройки — не в нижней панели, а в меню команд Telegram (кнопка «Меню») и командой /settings;
# текст кнопки на любом языке -> команда (кнопки старой клавиатуры тоже работают)
BUTTON_ACTIONS = {i18n.t(key, lang): func for row in BUTTONS + [[("btn_settings", "cmd_settings")]]
                  for key, func in row for lang in i18n.LANGS}
BUTTON_ACTIONS["⏰ Рассылка расписания"] = "cmd_digest"  # прежний текст кнопки — у тех, кто не обновил панель


def keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(i18n.t(key, lang)) for key, _ in row] for row in BUTTONS],
                               resize_keyboard=True)


KEYBOARD = keyboard("ru")


def _group_line(grp: dict | None, lang: str) -> str:
    return i18n.t("group_current", lang, code=grp["code"]) if grp else i18n.t("group_none", lang)


def help_text(lang: str, grp: dict | None = None) -> str:
    return i18n.t("help", lang, group_line=_group_line(grp, lang))


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
    lang, grp = _lang(update), _user_group(update)
    await update.effective_message.reply_text(
        help_text(lang, grp) + "\n\n" + i18n.t("beta", lang) + "\n\n" + i18n.t("feedback", lang),
        reply_markup=keyboard(lang), disable_web_page_preview=True)
    if not grp:  # новый пользователь — сразу предлагаем выбрать группу
        await _ask_group(update)


# ---- настройки: язык

def _settings_markup(lang: str, chat_id) -> InlineKeyboardMarkup:
    langs = [InlineKeyboardButton(("• " if code == lang else "") + name, callback_data=f"lang:{code}")
             for code, name in i18n.LANG_NAMES.items()]
    group = [[InlineKeyboardButton(i18n.t("btn_group_change", lang), callback_data="grp:menu")]]
    return InlineKeyboardMarkup([langs] + group + _sub_markup(lang, str(chat_id) in _load_subs()))


def _settings_text(lang: str, chat_id) -> str:
    head, choose = i18n.t("settings", lang, name=i18n.LANG_NAMES[lang]).split("\n\n", 1)
    return f"{head}\n{_group_line(_group_of(chat_id), lang)}\n{_sub_status(chat_id, lang)}\n\n{choose}"


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang, chat = _lang(update), update.effective_chat.id
    await update.effective_message.reply_text(_settings_text(lang, chat), parse_mode="HTML",
                                              reply_markup=_settings_markup(lang, chat))


async def on_lang(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    lang = (q.data or "").split(":", 1)[-1]
    if lang not in i18n.LANGS:
        await q.answer()
        return
    i18n.set_lang(update.effective_user.id, lang)
    await q.answer(i18n.t("lang_set", lang))
    try:
        chat = update.effective_chat.id
        await q.edit_message_text(_settings_text(lang, chat), parse_mode="HTML",
                                  reply_markup=_settings_markup(lang, chat))
    except Exception:  # сообщение не изменилось
        pass
    # новая клавиатура внизу чата — уже на выбранном языке
    await update.effective_message.reply_text(i18n.t("lang_set", lang), reply_markup=keyboard(lang))


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = _today()
    await _show(update, d, d, "empty_today")


async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    d = _today() + dt.timedelta(days=1)
    await _show(update, d, d, "empty_tomorrow")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE, shift: int = 0) -> None:
    mon = _today() - dt.timedelta(days=_today().weekday()) + dt.timedelta(weeks=shift)
    await _show(update, mon, mon + dt.timedelta(days=6), "empty_week")


async def cmd_next(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_week(update, ctx, shift=1)


async def cmd_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        d = dt.datetime.strptime(ctx.args[0], "%d.%m.%Y").date()
    except (IndexError, ValueError):
        await update.effective_message.reply_text(i18n.t("date_usage", _lang(update)))
        return
    await _show(update, d, d, "empty_day")


async def cmd_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang, grp = _lang(update), _user_group(update)
    if not grp:
        await _ask_group(update, i18n.t("group_first", lang))
        return
    start, end = semester_bounds(_today())
    msg = await update.effective_message.reply_text(
        i18n.t("export_wait", lang, group=grp["code"], a=f"{start:%d.%m.%Y}", b=f"{end:%d.%m.%Y}"))
    lessons = await _run(update, start, end, grp)
    if lessons is None:
        return
    code = grp["code"]
    stem = f"{code}_{start}_{end}"
    meta = {"group": code, "group_id": grp["id"] or _group["id"], "faculty": _group.get("faculty") or "",
            "from": str(start), "to": str(end)}
    files = [
        (f"{stem}.ics", to_ics(lessons, i18n.t("export_title", lang, group=code)), i18n.t("file_ics", lang)),
        (f"{stem}.csv", to_csv(lessons), i18n.t("file_csv", lang)),
        (f"{stem}.json", to_json(lessons, meta), i18n.t("file_json", lang)),
        (f"{stem}.txt", to_text(lessons), i18n.t("file_txt", lang)),
    ]
    for name, content, caption in files:
        await update.effective_message.reply_document(
            InputFile(io.BytesIO(content.encode("utf-8")), filename=name), caption=caption)
    await msg.edit_text(i18n.t("export_done", lang, n=len(lessons), a=f"{start:%d.%m.%Y}", b=f"{end:%d.%m.%Y}"))


async def cmd_captcha(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # капчу вводит только администратор: иначе любой мог бы гонять запросы к сайту от имени бота
    if update.effective_user.id != _admin_id():
        await update.effective_message.reply_text(i18n.t("site_busy", _lang(update)))
        return
    if client.offline:
        await update.effective_message.reply_text("Капчу вводят на ноутбуке: запустите sync_now.bat.")
        return
    try:
        img = await asyncio.to_thread(_locked, client.captcha_image)
    except (GubkinError, OSError) as e:
        await update.effective_message.reply_text(i18n.t("captcha_failed", _lang(update), e=e))
        return
    ctx.user_data["captcha"] = True
    await update.effective_message.reply_photo(img, caption=i18n.t("captcha_prompt", _lang(update)))


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    func_name = BUTTON_ACTIONS.get(text)  # нажата кнопка — выполняем соответствующую команду
    if func_name:
        ctx.user_data.pop("captcha", None)
        ctx.user_data.pop("await_time", None)
        await globals()[func_name](update, ctx)
        return
    if ctx.user_data.pop("await_time", False):  # ответ на «во сколько присылать расписание?»
        await _set_time(update, ctx, text)
        return
    if not ctx.user_data.pop("captcha", False) or update.effective_user.id != _admin_id():
        grp = _find_group(text) if len(text) <= 20 else None
        if grp:  # написали код группы, например «ГМ-26-12» — выбираем её
            await _group_chosen(update, grp)
            return
        lang = _lang(update)
        await update.effective_message.reply_text(help_text(lang, _user_group(update)), reply_markup=keyboard(lang))
        return
    try:
        ok = await asyncio.to_thread(_locked, client.validate_captcha, update.effective_message.text)
    except (GubkinError, OSError) as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(i18n.t("captcha_ok" if ok else "captcha_bad", _lang(update)))


# ---- мини-приложение с картой («Где пара»)

DEFAULT_WEBAPP_URL = "https://tawilone.github.io/ton-assets/"


async def cmd_map(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Где текущая/следующая пара + кнопка, открывающая карту в мини-приложении.
    Пары передаются в адресе страницы — сама страница с сайтом Губкина не общается."""
    lang = _lang(update)
    now = dt.datetime.now(MSK)
    now_min = now.hour * 60 + now.minute
    day = _today()
    lessons = await _run(update, day, day)
    if lessons is None:
        return
    lessons = [l for l in lessons if not l.cancelled]
    if not any(l.end and _minutes(l.end) > now_min for l in lessons):  # сегодня пар больше нет — берём завтра
        day = day + dt.timedelta(days=1)
        lessons = await _run(update, day, day, _user_group(update))
        if lessons is None:
            return
        lessons = [l for l in lessons if not l.cancelled]
        now_min = -1
    if not lessons:
        await update.effective_message.reply_text(i18n.t("no_classes_2days", lang))
        return

    current = next((l for l in lessons if _minutes(l.start) <= now_min < _minutes(l.end or l.start)), None)
    upcoming = next((l for l in lessons if _minutes(l.start) > now_min), None)
    lines = []
    colon = i18n.NBSP + ":" if lang == "fr" else ":"
    for key, l in (("now", current), ("next", upcoming)):
        if l:
            where = i18n.t("room", lang, room=l.room) if l.room else i18n.t("no_room", lang)
            place = locate_room(l.room, lang)
            lines.append(f"{i18n.t(key, lang)}{colon} {l.start}–{l.end} {i18n.subject(l.subject, lang)}\n    {where}"
                         + (f" — {place}" if place else ""))
    title = i18n.t("today_title" if day == _today() else "tomorrow_title", lang, date=i18n.day_month(day, lang))

    payload = [[l.start, l.end, l.room, i18n.subject(l.subject, lang)[:60], i18n.kind(l.kind, lang)]
               for l in lessons]
    url = (os.environ.get("WEBAPP_URL") or _read_setting("webapp.txt") or DEFAULT_WEBAPP_URL) + "?" + urlencode(
        {"d": title, "dt": day.isoformat(), "l": json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "lg": lang,
         "bot": _bot_username(ctx), "v": int(time.time() // 300)})  # новый адрес раз в 5 минут — Telegram не держит старую версию страницы
    button = InlineKeyboardMarkup([[InlineKeyboardButton(i18n.t("open_map", lang), web_app=WebAppInfo(url))]])
    note = "\n\n" + i18n.t("map_note", lang)
    grp = _user_group(update)
    head = f"👥 {grp['code']}\n\n" if grp else ""
    await update.effective_message.reply_text(head + ("\n".join(lines) or title) + note, reply_markup=button)


# ---- рассылка расписания в выбранное время

EVENING = 18 * 60  # с 18:00 присылаем расписание на следующий день
_subs_lock = threading.Lock()


def _load_subs() -> dict[str, dict]:
    """{chat_id: {"time": "07:00", "last": "2026-10-14"}}. Старый формат (список id) — время 07:00."""
    try:
        data = json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):
        return {str(i): {"time": f"{MORNING:%H:%M}"} for i in data}
    return {str(k): v for k, v in data.items() if isinstance(v, dict) and v.get("time")}


def _save_subs(subs: dict) -> None:
    try:
        write_atomic(SUBSCRIBERS_FILE, json.dumps(subs, ensure_ascii=False))
    except OSError:
        log.warning("cannot save %s", SUBSCRIBERS_FILE)


def _parse_time(text: str) -> str | None:
    """'7:00', '07.30', '7 30', '21-15', '7' -> 'HH:MM' или None."""
    m = re.fullmatch(r"\s*(\d{1,2})(?:\s*[:.\-hч ]\s*(\d{2}))?\s*", text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def _sub_markup(lang: str, on: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(i18n.t("btn_sub_change" if on else "btn_sub_set", lang), callback_data="sub:set")]]
    if on:
        rows.append([InlineKeyboardButton(i18n.t("btn_sub_off", lang), callback_data="sub:off")])
    return rows


def _sub_status(chat_id, lang: str) -> str:
    sub = _load_subs().get(str(chat_id))
    return i18n.t("sub_on", lang, time=sub["time"]) if sub else i18n.t("sub_off", lang)


async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «⏰ Рассылка расписания»: если рассылки нет — сразу спрашиваем время,
    если есть — показываем время и кнопки «Изменить время» / «Отключить рассылку»."""
    chat, lang = update.effective_chat.id, _lang(update)
    if str(chat) not in _load_subs():
        await cmd_subscribe(update, ctx)
        return
    await update.effective_message.reply_text(_sub_status(chat, lang),
                                              reply_markup=InlineKeyboardMarkup(_sub_markup(lang, True)))


async def cmd_subscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Спрашивает время рассылки; ответ ловит on_text."""
    ctx.user_data["await_time"] = True
    ctx.user_data.pop("captcha", None)
    await update.effective_message.reply_text(i18n.t("sub_ask", _lang(update)), parse_mode="HTML")


async def cmd_unsubscribe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    with _subs_lock:
        subs = _load_subs()
        subs.pop(str(update.effective_chat.id), None)
        _save_subs(subs)
    ctx.user_data.pop("await_time", None)
    await update.effective_message.reply_text(i18n.t("unsubscribed", _lang(update)))


async def _set_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    lang = _lang(update)
    hhmm = _parse_time(text)
    if not hhmm:
        ctx.user_data["await_time"] = True  # ждём ещё одну попытку
        await update.effective_message.reply_text(i18n.t("sub_bad", lang), parse_mode="HTML")
        return
    now = dt.datetime.now(MSK)
    with _subs_lock:
        subs = _load_subs()
        # если выбранное время сегодня уже прошло — первая рассылка будет завтра
        subs[str(update.effective_chat.id)] = {"time": hhmm, "last": now.date().isoformat()
                                               if hhmm <= f"{now:%H:%M}" else ""}
        _save_subs(subs)
    h, m = map(int, hhmm.split(":"))
    key = "sub_done_tomorrow" if h * 60 + m >= EVENING else "sub_done_today"
    await update.effective_message.reply_text(i18n.t(key, lang, time=hhmm), reply_markup=keyboard(lang))


async def on_sub_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if q.data == "sub:off":
        await cmd_unsubscribe(update, ctx)
    else:
        await cmd_subscribe(update, ctx)


async def digest_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Раз в полминуты: кому пора — присылает расписание (утром — на этот день, вечером — на завтра).
    Опоздание до 15 минут (бот перезапускался) — всё равно присылаем, но не дважды за день."""
    now = dt.datetime.now(MSK)
    now_min = now.hour * 60 + now.minute
    today = now.date().isoformat()
    with _subs_lock:
        subs = _load_subs()
    due = []
    for chat_id, sub in subs.items():
        h, m = map(int, sub["time"].split(":"))
        if sub.get("last") != today and 0 <= now_min - (h * 60 + m) < 15:
            due.append((chat_id, h * 60 + m))
    if not due:
        return
    texts = {}
    for chat_id, at in due:
        evening = at >= EVENING
        d = now.date() + dt.timedelta(days=1 if evening else 0)
        lang = i18n.get_lang(chat_id)  # в личном чате id чата = id пользователя
        grp = _group_of(chat_id)
        if not grp:  # группа не выбрана — присылать нечего
            continue
        head = i18n.t("evening" if evening else "morning" if at < 12 * 60 else "digest_today", lang)
        key = (d, lang, grp["code"], head)
        if key not in texts:
            try:
                lessons, _ = await asyncio.to_thread(_fetch, d, d, grp)
            except (GubkinError, OSError) as e:  # у этой группы пока нет расписания — попробуем через полминуты
                log.warning("digest %s: %s", grp["code"], e)
                continue
            if guard.check_lessons(lessons):  # подмена данных — такое не присылаем
                log.warning("digest %s: подозрительное расписание, не отправляю", grp["code"])
                continue
            if not lessons:  # пар нет — не беспокоим
                texts[key] = None
            else:
                texts[key] = head + "\n" + _group_head(grp) + to_html(lessons, today=now.date(), lang=lang)
        with _subs_lock:  # отмечаем сразу, чтобы не прислать повторно
            subs = _load_subs()
            if chat_id in subs:
                subs[chat_id]["last"] = today
                _save_subs(subs)
        if texts[key] is None:
            continue
        try:
            for part in _split(texts[key]):
                await ctx.bot.send_message(int(chat_id), part, parse_mode="HTML")
        except Forbidden:  # пользователь заблокировал бота
            _mark_blocked(chat_id, True)
        except Exception:
            log.warning("cannot send to %s", chat_id)


async def prefetch_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Заранее подгружает текущую и следующую неделю в кэш — тогда /today, /week и кнопки
    отвечают мгновенно, без ожидания сайта."""
    mon = _today() - dt.timedelta(days=_today().weekday())
    codes = sorted(set(_load_user_groups().values()) | {GROUP_CODE})
    for code in codes[:20]:  # не больше 20 групп за раз — частые запросы вызывают капчу
        grp = _find_group(code)
        if not grp:
            continue
        try:
            await asyncio.to_thread(_fetch, mon, mon + dt.timedelta(days=13), grp)
        except CaptchaRequired:
            log.warning("prefetch: сайт просит капчу — отправьте боту /captcha")
            return
        except (GubkinError, OSError) as e:
            log.warning("prefetch %s failed: %s", code, e)


SYNC_STATUS_FILE = DATA_DIR / "sync_status.json"
_alerted: set[str] = set()


async def _alert_admin(update: Update, key: str, text: str) -> None:
    """Пишет администратору — одно и то же не чаще раза (key включает дату)."""
    admin = _admin_id()
    if not admin or key in _alerted:
        return
    _alerted.add(key)
    try:
        await update.get_bot().send_message(admin, text)
    except Exception as e:
        log.warning("не удалось написать администратору: %s", e)


# ---- уведомления об отменах и переносах

CHANGES_FILE = DATA_DIR / "changes_seen.json"  # какие отмены/переносы уже видели: {id группы: {понедельник: [...]}}
CHANGES_DAYS = 7          # следим за парами на неделю вперёд
QUIET = (23 * 60, 7 * 60)  # ночью не пишем — сообщение придёт утром
_changes_mtime: list = [None]


def _msk_now() -> dt.datetime:
    return dt.datetime.now(MSK)


def _change_events(lessons) -> dict[str, tuple]:
    """Отмены, переносы и смены аудитории: {ключ: (вид, пара, пояснение)}."""
    out = {}
    for l in lessons:
        found = [("cancel", "")] if l.cancelled else []
        for n in l.notes:
            if n.startswith("Перенесено"):
                found.append(("move", n))
            elif n.startswith("Аудитория:"):
                found.append(("room", n))
        for kind_, detail in found:
            out[f"{l.date}|{l.start}|{l.subject}|{l.kind}|{l.subgroup}|{kind_}|{detail}"] = (kind_, l, detail)
    return out


def _change_text(code: str, events: list, lang: str) -> str:
    parts = [i18n.t("chg_title", lang, code=code)]
    for kind_, l, detail in sorted(events, key=lambda e: (e[1].date, e[1].start)):
        when = f"{i18n.day_month(dt.date.fromisoformat(l.date), lang)}, {l.start}–{l.end}"
        parts.append(f"{i18n.t('chg_' + kind_, lang)}: {when}\n{i18n.subject(l.subject, lang)}"
                     + (f" ({i18n.kind(l.kind, lang)})" if l.kind else "")
                     + (f"\n{i18n.note(detail, lang)}" if detail else ""))
    return "\n\n".join(parts)


def _monday(d: dt.date) -> str:
    return (d - dt.timedelta(days=d.weekday())).isoformat()


async def changes_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Ноутбук прислал новое расписание — ищем новые отмены и переносы и пишем тем, кто выбрал группу.
    Неделя, которую бот видит впервые, ничего не присылает: это не изменение, а первое знакомство."""
    try:
        mtime = client.cache_file.stat().st_mtime
    except (OSError, AttributeError):
        return
    now = _msk_now()
    now_min = now.hour * 60 + now.minute
    if mtime == _changes_mtime[0] or now_min >= QUIET[0] or now_min < QUIET[1]:
        return
    try:
        seen = json.loads(CHANGES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = None  # первый запуск — только запоминаем
    users, chosen = _load_users(), _load_user_groups()
    by_code: dict[str, list[str]] = {}
    for uid, code in chosen.items():
        if not (users.get(uid) or {}).get("blocked"):
            by_code.setdefault(code, []).append(uid)
    today = now.date()
    start, end = today, today + dt.timedelta(days=CHANGES_DAYS - 1)
    new_seen, sends = {}, []
    for code, uids in by_code.items():
        grp = _find_group(code)
        if not grp or not grp.get("id"):
            continue
        old = (seen or {}).get(grp["id"]) or {}  # неделю или группу видим впервые — только запоминаем
        new_seen[grp["id"]], fresh = {}, []
        for wk in sorted({_monday(start + dt.timedelta(days=i)) for i in range(CHANGES_DAYS)}):
            mon = dt.date.fromisoformat(wk)
            # неделю берём и запоминаем целиком: иначе, когда окно сдвинется на новый день, его старые
            # отмены показались бы новыми
            try:  # недели может ещё не быть на сервере — тогда её пропускаем, помним прежнее
                lessons, _ = await asyncio.to_thread(_fetch, mon, mon + dt.timedelta(days=6), grp)
            except Exception:
                if wk in old:
                    new_seen[grp["id"]][wk] = old[wk]
                continue
            if guard.check_lessons(lessons):  # подозрительные данные — не рассылаем, помним прежнее
                if wk in old:
                    new_seen[grp["id"]][wk] = old[wk]
                continue
            events = _change_events(lessons)
            # пишем только о том, что ещё впереди: окно — сегодня и CHANGES_DAYS-1 дней, прошедшие пары — нет
            fresh += [ev for k, ev in events.items() if wk in old and k not in old[wk]
                      and start <= dt.date.fromisoformat(ev[1].date) <= end
                      and not (ev[1].date == start.isoformat() and _minutes(ev[1].end or ev[1].start) <= now_min)]
            new_seen[grp["id"]][wk] = sorted(events)
        if fresh:
            sends.append((grp["code"], uids, fresh))
    try:
        write_atomic(CHANGES_FILE, json.dumps(new_seen, ensure_ascii=False))
    except OSError:
        log.warning("cannot save %s", CHANGES_FILE)
        return
    _changes_mtime[0] = mtime
    for code, uids, fresh in sends:
        log.info("изменения в расписании %s: %d, получателей %d", code, len(fresh), len(uids))
        for uid in uids:
            lang = i18n.get_lang(uid)
            try:
                await ctx.bot.send_message(int(uid), _change_text(code, fresh, lang))
            except Forbidden:
                _mark_blocked(uid, True)
            except Exception:
                log.warning("cannot send change to %s", uid)
            await asyncio.sleep(0.05)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Непойманная ошибка в обработчике: пишем в журнал, а человеку — что что-то пошло не так
    (раньше он просто не получал ответа)."""
    log.error("ошибка при обработке сообщения", exc_info=ctx.error)
    if isinstance(update, Update) and update.effective_message and update.effective_user:
        try:
            await update.effective_message.reply_text(i18n.t("oops", _lang(update)))
        except Exception:
            pass


async def sync_watch_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Только для offline-сервера: следит за тем, как ноутбук присылает расписание (sync.py),
    и пишет администратору, если нужна капча или расписание давно не обновлялось."""
    admin = _admin_id()
    if not admin:
        return
    try:
        st = json.loads(SYNC_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    msg, key = None, None
    # капча бывает разовой: ноутбук попробует снова через 3 часа. Пишем, только если из-за неё
    # расписание не обновляется уже больше суток, и не чаще раза в день
    last_ok = st.get("last_ok")
    try:
        stuck = not last_ok or dt.datetime.now() - dt.datetime.fromisoformat(last_ok) > dt.timedelta(hours=24)
    except ValueError:
        stuck = True
    if st.get("error") == "captcha" and stuck:
        msg, key = ("🔐 Сайт Губкина уже сутки просит капчу у ноутбука, расписание не обновляется. "
                    "Запустите на ноутбуке sync_now.bat и введите код с картинки."), \
            "captcha:" + dt.date.today().isoformat()
    if not msg and str(st.get("error", "")).startswith("suspicious"):
        msg, key = ("⚠️ Сайт Губкина отдаёт подозрительное расписание (похоже на подмену данных). Ноутбук "
                    "не стал его сохранять, у пользователей осталось прежнее. Проверьте lk.gubkin.ru вручную."), \
            "suspicious:" + dt.date.today().isoformat()
    age = client.cache_age()
    if not msg and age is not None and age > 48 * 3600:
        msg = (f"📅 Расписание на сервере обновлялось {_ago(age)}. Включите ноутбук с интернетом "
               "или запустите sync_now.bat.")
        key = "old:" + dt.date.today().isoformat()  # не чаще раза в день
    # последнее предупреждение запоминаем в файле — после перезапуска бота (update.sh) не повторяем его
    alert_file = DATA_DIR / "sync_alert.txt"
    try:
        last_key = alert_file.read_text(encoding="utf-8").strip()
    except OSError:
        last_key = ""
    if msg and last_key != key:
        try:
            alert_file.write_text(key, encoding="utf-8")
        except OSError:
            pass
        try:
            await ctx.bot.send_message(admin, msg)
        except Exception as e:
            log.warning("sync watch: не удалось написать администратору: %s", e)


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


# что нажал пользователь — для статистики в /users
ACTION_OF_FUNC = {"cmd_today": "today", "cmd_tomorrow": "tomorrow", "cmd_week": "week", "cmd_next": "next",
                  "cmd_map": "map", "cmd_digest": "digest", "cmd_settings": "settings", "cmd_group": "group"}
ACTION_OF_CMD = {"start": "start", "help": "start", "today": "today", "tomorrow": "tomorrow", "week": "week",
                 "next": "next", "date": "date", "map": "map", "subscribe": "digest", "unsubscribe": "digest",
                 "settings": "settings", "language": "settings", "all": "export", "captcha": "captcha",
                 "group": "group"}


ADMIN_CMDS = {"users", "broadcast", "untranslated", "myid"}


def _action(update: Update) -> str | None:
    if update.callback_query:
        kind = (update.callback_query.data or "").split(":", 1)[0]
        return {"lang": "settings", "sub": "digest", "grp": "group"}.get(kind)
    text = (update.effective_message.text or "").strip() if update.effective_message else ""
    if text.startswith("/"):
        cmd = text[1:].split()[0].split("@")[0].lower() if len(text) > 1 else ""
        if cmd in ADMIN_CMDS:  # служебные команды администратора в статистику не попадают
            return None
        return ACTION_OF_CMD.get(cmd, "other")
    if text in BUTTON_ACTIONS:
        return ACTION_OF_FUNC.get(BUTTON_ACTIONS[text], "other")
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
    "Сначала бот покажет, как будет выглядеть сообщение, и спросит подтверждение. "
    "Вместе с сообщением у каждого обновится нижняя панель кнопок — /start нажимать не нужно."
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
    raw = (update.effective_message.text or "").split(maxsplit=1)
    texts = _parse_broadcast(raw[1]) if len(raw) > 1 else {}
    if not texts.get("ru"):
        await update.effective_message.reply_text(BROADCAST_HELP)
        return
    ctx.bot_data["broadcast"] = texts
    n = len(_load_users())
    for lang in i18n.LANGS:  # предпросмотр каждого варианта
        if lang in texts:
            await update.effective_message.reply_text(f"Предпросмотр ({i18n.LANG_NAMES[lang]}):\n\n{texts[lang]}")
    await update.effective_message.reply_text(
        f"Отправить это {n} пользователям? Без перевода английским и французским придёт русский вариант.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Отправить всем ({n})", callback_data="bc:send"),
                                            InlineKeyboardButton("✖️ Отмена", callback_data="bc:cancel")]]))


async def on_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    if update.effective_user.id != _admin_id():
        return
    texts = ctx.bot_data.pop("broadcast", None)
    if q.data != "bc:send" or not texts:
        await q.edit_message_text("Рассылка отменена.")
        return
    await q.edit_message_text("Отправляю…")
    ok = fail = 0
    for uid in list(_load_users()):
        lang = i18n.get_lang(uid)
        try:  # вместе с сообщением — новая нижняя панель на языке пользователя
            await ctx.bot.send_message(int(uid), texts.get(lang) or texts["ru"], reply_markup=keyboard(lang))
            ok += 1
        except Forbidden:  # заблокировал бота или удалил аккаунт
            _mark_blocked(uid, True)
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)  # Telegram ограничивает ~30 сообщений в секунду
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
    url = (os.environ.get("WEBAPP_URL") or _read_setting("webapp.txt") or DEFAULT_WEBAPP_URL) + \
        f"admin.html?v={int(time.time() // 300)}&p=" + _admin_payload(users)
    await update.effective_message.reply_text(
        f"👥 Пользователей: <b>{len(users)}</b> · сегодня активны: <b>{active}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📊 Открыть статистику",
                                                                 web_app=WebAppInfo(url))]]))


def _faculty_counts(users: dict, groups: dict) -> list:
    """[[название факультета, сколько людей], ...] по выбранным группам — больше всего первым;
    в конце — те, кто группу не выбирал (или её нет в списке факультетов)."""
    fac_of = {g["code"]: g.get("fac") for g in _faculty_groups()}
    try:  # и прямо из groups.json — на случай, если список в памяти бота устарел
        for f in json.loads(GROUPS_FILE.read_text(encoding="utf-8")).get("faculties") or []:
            for g in f.get("groups") or []:
                fac_of.setdefault(normalize_code(str(g.get("code") or "")), f.get("key"))
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    counts: dict[str, int] = {}
    for uid in users:
        key = fac_of.get(normalize_code(groups.get(uid, "")))
        counts[key] = counts.get(key, 0) + 1
    rows = [[f["ru"], counts[f["key"]]] for f in FACULTIES if counts.get(f["key"])]
    rows.sort(key=lambda r: -r[1])
    if counts.get(None):
        rows.append(["Группа не выбрана", counts[None]])
    return rows


ADMIN_PAYLOAD_MAX = 6000  # GitHub Pages не открывает слишком длинные адреса («URI Too Long»)


def _admin_payload(users: dict, limit: int = ADMIN_PAYLOAD_MAX) -> str:
    """Данные для admin.html: сжатый JSON (deflate + base64url) в параметре «p» адреса.
    В «#» класть нельзя: Telegram дописывает туда свои параметры. Итоги (графики, счётчики) считаем
    здесь по всем пользователям, а в список кладём столько самых недавних, сколько влезает в limit."""
    import base64
    import zlib
    subs, groups = _load_subs(), _load_user_groups()
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
              "acts": acts, "days": per_day, "fac": _faculty_counts(users, groups)}
    rows = []
    for uid, u in sorted(users.items(), key=lambda kv: kv[1].get("last", ""), reverse=True)[:300]:
        rows.append([u.get("name") or "", u.get("username") or "", u.get("count", 0), u.get("first", ""),
                     u.get("last", ""), i18n.get_lang(uid), (subs.get(uid) or {}).get("time", ""),
                     0, 0, groups.get(uid, ""), u.get("blocked") or ""])

    def pack(n: int) -> str:
        data = json.dumps({"now": now.strftime("%Y-%m-%d %H:%M"), "t": totals, "u": rows[:n]},
                          ensure_ascii=False, separators=(",", ":")).encode()
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


async def cmd_untranslated(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Для администратора: предметы из расписания, которых ещё нет в словаре переводов (i18n.SUBJECTS)."""
    if update.effective_user.id != _admin_id():
        await update.effective_message.reply_text("Эта команда только для администратора.")
        return
    from schedule import parse_week
    names = set()
    with _lock:
        for (_, gid), (_, data) in list(client._cache.items()):
            try:
                names.update(l.subject for l in parse_week(data, gid))
            except Exception:
                continue
    missing = sorted(n for n in names if n and not i18n.has_subject(n))
    if not names:
        text = "Расписание ещё не загружено — сначала откройте «Неделя»."
    elif not missing:
        text = f"✅ Все предметы переведены ({len(names)})."
    else:
        text = "Нет перевода (пришлите этот список — добавлю в словарь):\n\n" + "\n".join(f"• {n}" for n in missing)
    await _send_text(update, text)


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


COMMANDS = {  # меню команд Telegram (кнопка «Меню» слева от поля ввода) — на языке приложения Telegram
    "ru": [("today", "Сегодня"), ("tomorrow", "Завтра"), ("week", "Неделя"), ("games", "Мини-игры: шахматы"),
           ("group", "Выбрать группу"),
           ("subscribe", "Присылать расписание в выбранное время"), ("settings", "Настройки и язык")],
    "en": [("today", "Today"), ("tomorrow", "Tomorrow"), ("week", "This week"), ("games", "Mini games: chess"),
           ("group", "Choose your group"),
           ("subscribe", "Get the timetable at a time you choose"), ("settings", "Settings and language")],
    "fr": [("today", "Aujourd'hui"), ("tomorrow", "Demain"), ("week", "Cette semaine"), ("games", "Mini-jeux : échecs"),
           ("group", "Choisir votre groupe"),
           ("subscribe", "Recevoir l'emploi du temps à l'heure choisie"), ("settings", "Paramètres et langue")],
}


# имя и описание бота для тех, у кого Telegram на английском/французском (русские — из BotFather)
BOT_PROFILE = {
    "en": ("Gubkin Timetable (Beta)", "Gubkin University timetable for several faculties, with a map showing "
           "where your class is.",
           "Gubkin University timetable for every group of the Geology, Automation & Computing, Chemical "
           "Technology & Ecology, and Economics & Management faculties: choose your group and get "
           "today's and tomorrow's classes, this week, and a floor map with the room of your class. "
           "The bot is in beta, so check the official timetable too."),
    "fr": ("Emploi du temps Gubkin (bêta)", "Emploi du temps de l'Université Gubkin pour plusieurs facultés, "
           "avec le plan de la salle de cours.",
           "Emploi du temps de l'Université Gubkin pour tous les groupes des facultés de géologie, "
           "d'automatique et informatique, de génie chimique et écologie, et d'économie et gestion\u00a0: "
           "choisissez votre groupe et consultez les cours d'aujourd'hui et de demain, la semaine, et un plan "
           "de l'étage avec la salle de votre cours. Le bot est en version bêta\u00a0: vérifiez aussi "
           "l'emploi du temps officiel."),
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
    _migrate_user_groups()
    # group=-1: срабатывает раньше остальных обработчиков на каждое сообщение
    app.add_handler(TypeHandler(Update, track_user), group=-1)
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler(["settings", "language"], cmd_settings))
    app.add_handler(CallbackQueryHandler(on_lang, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(on_sub_button, pattern=r"^sub:"))
    app.add_handler(CommandHandler("group", cmd_group))
    app.add_handler(CommandHandler(["games", "chess"], cmd_games))
    app.add_handler(CallbackQueryHandler(on_group, pattern=r"^grp:"))
    app.add_handler(CommandHandler("untranslated", cmd_untranslated))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CallbackQueryHandler(on_broadcast, pattern=r"^bc:"))
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
    app.job_queue.run_repeating(digest_job, interval=30, first=10)  # рассылка в выбранное время
    # подгрузка расписания при старте и каждые 20 минут (кэш живёт 30 минут)
    if client.offline:  # сервер: на сайт не ходим, следим за присылаемым расписанием
        log.info("offline: расписание берётся из cache.json, который присылает ноутбук (sync.py)")
        app.job_queue.run_repeating(sync_watch_job, interval=15 * 60, first=60)
        app.job_queue.run_repeating(changes_job, interval=3 * 60, first=90)  # отмены и переносы
    else:
        app.job_queue.run_repeating(prefetch_job, interval=20 * 60, first=5)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # старые сообщения, отправленные, пока бот был выключен, пропускаем —
    # иначе при запуске он долго отвечает на каждое из них
    # если Telegram пока недоступен (VPN ещё не включён), не падаем, а пробуем снова
    log.info("Подключаюсь к Telegram... Если долго висит — проверьте VPN/прокси.")
    # ALL_TYPES — чтобы приходили и события «пользователь заблокировал бота» (для /users)
    app.run_polling(drop_pending_updates=True, bootstrap_retries=-1, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
