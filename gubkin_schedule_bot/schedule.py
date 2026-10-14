"""Разбор ответа act=schedule и выгрузка расписания в JSON / CSV / ICS / текст.

Формат ответа сайта:
{ "state": true, "rows": {
    "week": {"weekRussia": {"type": "upper|lower",
                            "days": [{"date": "22-09-2026", "weekDayNumber": 1}, ...]}},
    "organizations": [{
        "lessonsTimeChunks": ["08:45-09:30", "09:30-10:15", ...],
        "lessons": [{"weekDayNumber": 1, "timeChunks": [0, 1], "course": {"name": "..."},
                     "type": "Лекция", "rooms": [{"number": "1234"}],
                     "teachers": [{"lastName": "...", "firstName": "...", "middleName": "..."}],
                     "groups": [{"id": 10706, "code": "ГМ-26-11"}], "subgroup": 0,
                     "isCanceled": false, "isMoved": false, "movedFrom": "...", "movedTo": "...",
                     "changes": {"rooms": [...], "teachers": [...]}}]}]}}
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from dataclasses import asdict, dataclass, field

import i18n
from gubkin_api import GubkinClient, normalize_code

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


@dataclass
class Lesson:
    date: str           # YYYY-MM-DD
    weekday: str
    start: str          # HH:MM
    end: str
    subject: str
    kind: str = ""
    room: str = ""
    teacher: str = ""
    subgroup: int = 0   # 0 — вся группа
    week_type: str = "" # верхняя / нижняя
    cancelled: bool = False
    notes: list[str] = field(default_factory=list)


def _parse_date(s: str) -> dt.date | None:
    p = s.strip().replace(".", "-").replace("/", "-").split("-")
    if len(p) != 3:
        return None
    try:
        if len(p[0]) == 4:
            return dt.date(int(p[0]), int(p[1]), int(p[2]))
        return dt.date(int(p[2]), int(p[1]), int(p[0]))
    except ValueError:
        return None


def _rooms(lst) -> str:
    out = []
    for r in lst or []:
        v = (r.get("number") or r.get("name")) if isinstance(r, dict) else r
        if v and str(v).strip() and str(v).strip() not in out:
            out.append(str(v).strip())
    return ", ".join(out)


def _teacher(t) -> str:
    if not isinstance(t, dict):
        return str(t or "").strip()
    parts = [t.get("lastName"), t.get("firstName"), t.get("middleName") or t.get("patronymic")]
    parts = [str(x).strip() for x in parts if x and str(x).strip()]
    return " ".join(parts) or str(t.get("fullName") or t.get("name") or "").strip()


def _teachers(lst) -> str:
    out = []
    for t in lst or []:
        n = _teacher(t)
        if n and n not in out:
            out.append(n)
    return ", ".join(out)


def _short_date(s: str) -> str:
    d = _parse_date(str(s))
    return d.strftime("%d.%m") if d else str(s)


def parse_week(root: dict, group_id, group_code: str = "") -> list[Lesson]:
    rows = root.get("rows") or {}
    wr = (rows.get("week") or {}).get("weekRussia") or {}
    week_type = {"upper": "верхняя", "lower": "нижняя"}.get(wr.get("type"), wr.get("type") or "")
    day_dates: dict[int, dt.date] = {}
    for d in wr.get("days") or []:
        date = _parse_date(str(d.get("date", "")))
        if date and d.get("weekDayNumber") is not None:
            day_dates[int(d["weekDayNumber"])] = date

    my_code = normalize_code(group_code) if group_code else ""
    lessons: list[Lesson] = []
    seen = set()
    for org in rows.get("organizations") or []:
        chunks = [str(c) for c in org.get("lessonsTimeChunks") or []]
        for lo in org.get("lessons") or []:
            groups = [g for g in lo.get("groups") or [] if isinstance(g, dict)]
            mine = any(str(g.get("id")) == str(group_id) for g in groups) or (
                my_code and any(normalize_code(str(g.get("code") or g.get("name") or "")).startswith(my_code)
                                for g in groups))
            if groups and not mine:
                continue
            tc = [int(x) for x in lo.get("timeChunks") or [] if str(x).lstrip("-").isdigit()]
            if not tc or tc[0] >= len(chunks) or lo.get("weekDayNumber") is None:
                continue
            start = chunks[tc[0]].split("-")[0].strip()
            end = chunks[min(tc[-1], len(chunks) - 1)].split("-")[-1].strip()
            wd = int(lo["weekDayNumber"])
            date = day_dates.get(wd)
            if not date:
                continue

            changes = lo.get("changes") if isinstance(lo.get("changes"), dict) else {}
            base_room, new_room = _rooms(lo.get("rooms")), _rooms(changes.get("rooms"))
            base_t, new_t = _teachers(lo.get("teachers")), _teachers(changes.get("teachers"))
            notes = []
            if new_room and new_room != base_room:
                notes.append(f"Аудитория: {base_room or '—'} → {new_room}")
            if new_t and new_t != base_t:
                notes.append(f"Преподаватель: {base_t or '—'} → {new_t}")
            if lo.get("movedFrom"):
                notes.append(f"Перенесено с {_short_date(lo['movedFrom'])}")
            if lo.get("isMoved") and lo.get("movedTo"):
                notes.append(f"Перенесено на {_short_date(lo['movedTo'])}")
            info = ((lo.get("course") or {}).get("additionalInfo") or "").strip()
            if info:
                notes.append(info)

            kind = str(lo.get("type") or "").strip()
            subject = str((lo.get("course") or {}).get("name") or lo.get("name") or kind or "Занятие").strip()
            try:
                sub = int(lo.get("subgroup") or 0)
            except (TypeError, ValueError):
                sub = 0
            les = Lesson(
                date=date.isoformat(), weekday=WEEKDAYS[date.weekday()], start=start, end=end,
                subject=subject, kind=kind, room=new_room or base_room, teacher=new_t or base_t,
                subgroup=sub, week_type=week_type,
                cancelled=bool(lo.get("isCanceled") or lo.get("isCancelled")), notes=notes,
            )
            key = (les.date, les.start, les.end, les.subject, les.kind, les.room, les.teacher,
                   les.subgroup, les.cancelled)
            if key not in seen:  # поток из нескольких групп приходит несколькими записями
                seen.add(key)
                lessons.append(les)
    lessons.sort(key=lambda x: (x.date, x.start))
    return lessons


def semester_bounds(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Осенний семестр: 1 сентября – 31 января; весенний: 1 февраля – 30 июня (включая сессию)."""
    today = today or dt.date.today()
    if today.month >= 8:
        return dt.date(today.year, 9, 1), dt.date(today.year + 1, 1, 31)
    if today.month == 1:
        return dt.date(today.year - 1, 9, 1), dt.date(today.year, 1, 31)
    return dt.date(today.year, 2, 1), dt.date(today.year, 6, 30)


def fetch_range(client: GubkinClient, group_id, group_code: str,
                start: dt.date, end: dt.date, progress=None) -> list[Lesson]:
    """Скачивает все недели, пересекающие [start, end]."""
    monday = start - dt.timedelta(days=start.weekday())
    out: list[Lesson] = []
    weeks = (end - monday).days // 7 + 1
    for i in range(weeks):
        day = monday + dt.timedelta(weeks=i)
        out += parse_week(client.week(day, group_id), group_id, group_code)
        if progress:
            progress(i + 1, weeks)
    s, e = start.isoformat(), end.isoformat()
    return [l for l in out if s <= l.date <= e]


# ---- форматы выгрузки

def to_json(lessons: list[Lesson], meta: dict | None = None) -> str:
    return json.dumps({"meta": meta or {}, "lessons": [asdict(l) for l in lessons]},
                      ensure_ascii=False, indent=2)


def to_csv(lessons: list[Lesson]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Дата", "День", "Начало", "Конец", "Предмет", "Тип", "Аудитория",
                "Преподаватель", "Подгруппа", "Неделя", "Отменено", "Примечания"])
    for l in lessons:
        w.writerow([l.date, l.weekday, l.start, l.end, l.subject, l.kind, l.room, l.teacher,
                    l.subgroup or "", l.week_type, "да" if l.cancelled else "", "; ".join(l.notes)])
    return "\ufeff" + buf.getvalue()  # BOM — чтобы Excel понял UTF-8


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    b = line.encode()
    if len(b) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        e = ch.encode()
        if len(cur) + len(e) > (75 if not parts else 74):
            parts.append(cur.decode())
            cur = b""
        cur += e
    parts.append(cur.decode())
    return "\r\n ".join(parts)


def to_ics(lessons: list[Lesson], calname: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//gubkin-schedule-bot//RU",
             "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_ics_escape(calname)}",
             "X-WR-TIMEZONE:Europe/Moscow"]
    for i, l in enumerate(lessons):
        d = l.date.replace("-", "")
        title = ("[ОТМЕНА] " if l.cancelled else "") + l.subject + (f" ({l.kind})" if l.kind else "")
        if l.subgroup:
            title += f" [{l.subgroup} п/г]"
        desc = "\n".join(x for x in [l.teacher, *l.notes] if x)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{d}-{l.start.replace(':', '')}-{i}@gubkin-schedule-bot",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Europe/Moscow:{d}T{l.start.replace(':', '')}00",
            f"DTEND;TZID=Europe/Moscow:{d}T{(l.end or l.start).replace(':', '')}00",
            f"SUMMARY:{_ics_escape(title)}",
            f"LOCATION:{_ics_escape(l.room)}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_ics_fold(x) for x in lines) + "\r\n"


def format_lesson(l: Lesson) -> str:
    head = f"{l.start}–{l.end}  {l.subject}"
    if l.kind:
        head += f" ({l.kind})"
    if l.subgroup:
        head += f" [{l.subgroup} п/г]"
    if l.cancelled:
        head = "❌ ОТМЕНА: " + head
    tail = [x for x in [f"ауд. {l.room}" if l.room else "", l.teacher] if x]
    out = head + ("\n    " + " · ".join(tail) if tail else "")
    for n in l.notes:
        out += f"\n    ⚠️ {n}"
    return out


def to_text(lessons: list[Lesson], empty: str = "Занятий нет") -> str:
    if not lessons:
        return empty
    out, cur, prev_end = [], None, None
    for l in lessons:
        if l.date != cur:
            cur, prev_end = l.date, None
            d = dt.date.fromisoformat(l.date)
            wt = f" ({l.week_type} неделя)" if l.week_type else ""
            out.append(f"\n📅 {l.weekday}, {d.strftime('%d.%m.%Y')}{wt}")
        elif prev_end is not None and not l.cancelled:
            gap = _minutes(l.start) - prev_end
            if gap > 0:
                out.append(break_text(gap))
        out.append(format_lesson(l))
        if not l.cancelled and l.end:
            prev_end = max(prev_end or 0, _minutes(l.end))
    return "\n".join(out).strip()


def _minutes(hhmm: str) -> int:
    """'10:30' -> 630 (минут от полуночи)."""
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def break_text(gap: int, lang: str = "ru") -> str:
    """Строка о перерыве между парами: короткий — перемена, длинный — «окно»."""
    if gap <= 40:
        return "  " + i18n.t("break", lang, n=gap)
    return "  " + i18n.t("gap", lang, len=i18n.duration(gap, lang))


# ---- красивый вид для чата Telegram (HTML-разметка)

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]

KIND_ICONS = [  # (часть названия типа занятия, значок)
    ("лекц", "📖"), ("семин", "✍️"), ("практ", "✍️"), ("лаб", "🔬"),
    ("экзам", "📝"), ("зач", "✅"), ("консул", "💬"),
]


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def kind_icon(kind: str) -> str:
    k = (kind or "").lower()
    for part, icon in KIND_ICONS:
        if part in k:
            return icon
    return "📌"


def short_name(full: str) -> str:
    """'Петров Пётр Петрович' -> 'Петров П. П.' (несколько имён через запятую тоже)."""
    out = []
    for person in (full or "").split(","):
        parts = person.split()
        if len(parts) >= 2:
            out.append(parts[0] + " " + " ".join(p[0] + "." for p in parts[1:]))
        elif parts:
            out.append(parts[0])
    return ", ".join(out)


def day_title(date_iso: str, weekday: str, week_type: str = "", today: dt.date | None = None,
              lang: str = "ru") -> str:
    d = dt.date.fromisoformat(date_iso)
    title = f"📅 <b>{i18n.weekday_date(d, lang, weekday)}</b>"
    if today and d == today:
        title += " · " + i18n.t("today", lang)
    elif today and d == today + dt.timedelta(days=1):
        title += " · " + i18n.t("tomorrow", lang)
    if week_type:
        wt = {"верхняя": "week_upper", "нижняя": "week_lower"}.get(week_type)
        if lang == "ru" or not wt:
            title += f"\n<i>{_esc(week_type)} неделя</i>" if lang == "ru" else ""
        else:
            title += f"\n<i>{i18n.t(wt, lang)}</i>"
    return title


def format_lesson_html(l: Lesson, lang: str = "ru") -> str:
    time = f"<b>{l.start}–{l.end}</b>" if l.end else f"<b>{l.start}</b>"
    name = i18n.subject(l.subject, lang)
    subject = f"<b>{_esc(name)}</b>"
    if l.cancelled:
        lines = [f"❌ <s>{l.start}–{l.end}  {_esc(name)}</s>", f"<i>{i18n.t('cancelled', lang)}</i>"]
    else:
        lines = [f"🕘 {time}", f"{kind_icon(l.kind)} {subject}"]
        info = []
        if l.kind:
            info.append(_esc(i18n.kind(l.kind, lang)))
        if l.subgroup:
            info.append(i18n.t("subgroup", lang, n=l.subgroup))
        if l.room:
            info.append(i18n.t("room", lang, room=f"<b>{_esc(l.room)}</b>"))
        if info:
            lines.append("      " + " · ".join(info))
        if l.teacher:
            lines.append(f"      👤 {_esc(short_name(l.teacher))}")
    for n in l.notes:
        lines.append(f"      ⚠️ <i>{_esc(i18n.note(n, lang))}</i>")
    return "\n".join(lines)


def to_html(lessons: list[Lesson], empty: str = "Занятий нет", today: dt.date | None = None,
            lang: str = "ru") -> str:
    """Расписание для чата: заголовок дня, пары блоками, перемены между ними.
    Каждая строка — законченный HTML (теги не переходят на следующую строку),
    поэтому длинный текст можно резать по переносам строк."""
    if not lessons:
        return _esc(empty)
    out, cur, prev_end = [], None, None
    for l in lessons:
        if l.date != cur:
            if cur is not None:
                out += ["", "━━━━━━━━━━━━━━", ""]
            cur, prev_end = l.date, None
            out += [day_title(l.date, l.weekday, l.week_type, today, lang), ""]
        else:
            gap = _minutes(l.start) - prev_end if prev_end is not None and not l.cancelled else 0
            out += ["", f"<i>{break_text(gap, lang).strip()}</i>", ""] if gap > 0 else [""]
        out.append(format_lesson_html(l, lang))
        if not l.cancelled and l.end:
            prev_end = max(prev_end or 0, _minutes(l.end))
    return "\n".join(out).strip()


# ---- где находится аудитория (как docs/campus.js)

BUILDING_NAMES = {b: names["ru"] for b, names in i18n.BUILDINGS.items()}


def locate_room(room: str, lang: str = "ru") -> str:
    """'2435' -> '3 корпус, 4 этаж'. Нумерация — по путеводителю первокурсника."""
    return i18n.locate_room(room, lang)
