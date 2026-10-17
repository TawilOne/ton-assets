"""Проверка, что с сайта пришло настоящее расписание, а не подмена.

Сайт может отдать боту вместо пар что угодно (так однажды сделали с другим сервисом расписания —
вместо пар показывались Смешарики: «кабинет — дом Копатыча, преподаватель — Совунья»).
Здесь два уровня проверки:

1. check_lessons — «жёсткие» признаки подмены в самих парах: персонажи мультфильмов и прочие
   заведомо чужие слова, эмодзи, ссылки в названии/ФИО, невозможное время. Только то, чего в
   настоящем расписании не бывает: цифры в поле преподавателя, например, на сайте встречаются. Используется и ботом перед показом, и ноутбуком при скачивании.
2. check_update — ноутбук сравнивает новую версию недели со старой: если почти все пары вдруг
   с невиданными раньше предметами И преподавателями — это не перенос пар, а подмена.
"""
from __future__ import annotations

import re

from gubkin_api import GubkinError


class SuspiciousData(GubkinError):
    """Сайт отдал что-то, что не похоже на расписание."""


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


# Заведомо не расписание: персонажи «Смешариков» (во всех падежах) и типичные заглушки.
# Сравниваем целыми словами и только с перечисленными окончаниями — чтобы не задеть настоящие
# фамилии: «Ежиков», «Барашков», «Пинский», «Нюшин» сюда не попадают.
_M = ["", "а", "у", "ом", "ем", "е"]  # окончания имён мужского рода: Крош, Кроша, Крошу, Крошем, Кроше
_STEMS = {
    "крош": _M, "ежик": _M, "бараш": _M, "лосяш": _M, "копатыч": _M, "кар-карыч": _M, "карыч": _M,
    "пин": _M, "нюш": ["а", "и", "е", "у", "ей"], "совунь": ["я", "и", "е", "ю", "ей"], "биби": [""],
    "смешарик": ["", "и", "ов", "ам", "ами", "ах", "а", "у", "ом", "е"],
    "мультфильм": ["", "а", "ы", "ов", "е"],
    "lorem": [""], "ipsum": [""], "заглушк": ["а", "и", "у", "е"], "asdf": [""], "qwerty": [""],
}
_WORDS = sorted({stem + end for stem, ends in _STEMS.items() for end in ends}, key=len, reverse=True)
_STOP_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in _WORDS) + r")(?![\w-])", re.IGNORECASE)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")
_URL_RE = re.compile(r"https?://|www\.|\.(?:ru|com|net|org)\b", re.IGNORECASE)
_TIME_RE = re.compile(r"^\d\d:\d\d$")


def lesson_problems(l) -> list[str]:
    """Что не так с одной парой (пустой список — всё в порядке)."""
    out = []
    fields = {"предмет": l.subject, "преподаватель": l.teacher, "аудитория": l.room, "тип": l.kind}
    for name, value in fields.items():
        m = _STOP_RE.search(_norm(value))
        if m:
            out.append(f"{name}: «{m.group(0)}»")
        if _EMOJI_RE.search(value or ""):
            out.append(f"{name}: эмодзи")
        # ссылка в аудитории бывает настоящей (онлайн-занятие), в названии предмета и ФИО — нет
        if name in ("предмет", "преподаватель") and _URL_RE.search(value or ""):
            out.append(f"{name}: ссылка")
    for n in l.notes:  # в примечаниях бывают ссылки на онлайн-занятия — их не трогаем
        m = _STOP_RE.search(_norm(n))
        if m:
            out.append(f"примечание: «{m.group(0)}»")
    if not re.search(r"[A-Za-zА-Яа-яЁё]", l.subject or "") or len(l.subject or "") > 250:
        out.append("предмет: не похоже на название")
    if len(l.teacher or "") > 300 or len(l.room or "") > 150 or len(l.kind or "") > 80:
        out.append("слишком длинный текст")
    if not (_TIME_RE.match(l.start or "") and _TIME_RE.match(l.end or "")) or not \
            ("06:00" <= l.start < l.end <= "23:59"):
        out.append("время: невозможное")
    return out


def check_lessons(lessons) -> list[str]:
    """Признаки подмены во всех парах сразу — по одному на пару, без повторов."""
    out = []
    for l in lessons:
        p = lesson_problems(l)
        if p:
            out.append(f"{l.date} {l.start} {l.subject[:40]!r}: {'; '.join(p)}")
    return out


class Vocabulary:
    """Всё, что уже встречалось в настоящем расписании: предметы, преподаватели, аудитории."""

    def __init__(self):
        self.subjects: set[str] = set()
        self.teachers: set[str] = set()

    def add(self, lessons) -> None:
        for l in lessons:
            self.subjects.add(_norm(l.subject))
            self.teachers.update(_norm(t) for t in (l.teacher or "").split(", ") if t)


def check_update(new, old, vocab: Vocabulary) -> str | None:
    """Новая версия недели вместо старой: если большинство пар — с незнакомыми и предметом,
    и преподавателем, это подмена, а не перенос. Возвращает причину или None."""
    if not old or len(new) < 3:
        return None
    strange = [l for l in new if _norm(l.subject) not in vocab.subjects
               and not any(_norm(t) in vocab.teachers for t in (l.teacher or "").split(", ") if t)]
    if len(strange) * 2 > len(new):
        return f"{len(strange)} из {len(new)} пар с незнакомыми предметами и преподавателями"
    return None
