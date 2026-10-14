"""Переводы бота: русский (основной), английский, французский.

Тексты бота — в TEXT, названия предметов — в SUBJECTS (словарь, переведён вручную).
Предмет, которого нет в словаре, показывается по-русски, как на сайте университета;
список таких предметов — команда администратора /untranslated.

Этажи: в России 1 этаж — это уровень земли. В английском взят американский счёт
(1st floor = уровень земли, совпадает с русским). Во французском 1 этаж — rez-de-chaussée,
2 этаж — 1er étage и т. д., как принято во Франции.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

LANGS = ("ru", "en", "fr")
LANG_NAMES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "fr": "🇫🇷 Français"}
NBSP = " "  # во французском перед «:», «!», «?» ставится неразрывный пробел

# ---- выбор языка (запоминается для каждого пользователя)

LANG_FILE = pathlib.Path(__file__).resolve().parent / "lang.json"
_langs: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _langs
    if _langs is None:
        try:
            _langs = json.loads(LANG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _langs = {}
    return _langs


def get_lang(user_id) -> str:
    return _load().get(str(user_id), "ru")


def set_lang(user_id, lang: str) -> None:
    if lang not in LANGS:
        return
    data = _load()
    data[str(user_id)] = lang
    try:
        LANG_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


# ---- тексты

TEXT = {
    "help": {
        "en": "📅 Gubkin University timetable, group {group}\n\n"
              "/today — today's classes\n"
              "/tomorrow — tomorrow's classes\n"
              "/week — this week\n"
              "/settings — language",
        "fr": "📅 Emploi du temps de l'Université Gubkin, groupe {group}\n\n"
              "/today — les cours d'aujourd'hui\n"
              "/tomorrow — les cours de demain\n"
              "/week — cette semaine\n"
              "/settings — langue",
    },
    "beta": {
        "ru": "⚠️ Бот и карта аудиторий ещё в разработке. Возможны ошибки в расписании и на карте — "
              "не опирайтесь на них полностью и сверяйтесь с официальным расписанием: lk.gubkin.ru/schedule",
        "en": "⚠️ The bot and the room map are still in development. The timetable and the map may contain "
              "errors, so don't rely on them completely and check the official timetable: lk.gubkin.ru/schedule",
        "fr": "⚠️ Le bot et le plan des salles sont encore en développement. L'emploi du temps et le plan "
              "peuvent contenir des erreurs. Ne vous y fiez pas entièrement et consultez l'emploi du temps "
              f"officiel{NBSP}: lk.gubkin.ru/schedule",
    },
    "btn_today": {"ru": "📅 Сегодня", "en": "📅 Today", "fr": "📅 Aujourd'hui"},
    "btn_tomorrow": {"ru": "➡️ Завтра", "en": "➡️ Tomorrow", "fr": "➡️ Demain"},
    "btn_week": {"ru": "🗓 Неделя", "en": "🗓 This week", "fr": "🗓 Cette semaine"},
    "btn_next": {"ru": "⏭ След. неделя", "en": "⏭ Next week", "fr": "⏭ Semaine prochaine"},
    "btn_map": {"ru": "🗺 Где пара", "en": "🗺 Where's my class", "fr": "🗺 Où est mon cours"},
    "btn_settings": {"ru": "⚙️ Настройки", "en": "⚙️ Settings", "fr": "⚙️ Paramètres"},
    "settings": {"ru": "⚙️ <b>Настройки</b>\nЯзык: {name}\n\nВыберите язык:",
                 "en": "⚙️ <b>Settings</b>\nLanguage: {name}\n\nChoose a language:",
                 "fr": f"⚙️ <b>Paramètres</b>\nLangue{NBSP}: {{name}}\n\nChoisissez une langue{NBSP}:"},
    "lang_set": {"ru": "✅ Язык: русский", "en": "✅ Language: English", "fr": f"✅ Langue{NBSP}: français"},
    "empty_today": {"ru": "Сегодня занятий нет 🎉", "en": "No classes today 🎉", "fr": "Pas de cours aujourd'hui 🎉"},
    "empty_tomorrow": {"ru": "Завтра занятий нет 🎉", "en": "No classes tomorrow 🎉", "fr": "Pas de cours demain 🎉"},
    "empty_week": {"ru": "На этой неделе занятий нет", "en": "No classes this week",
                   "fr": "Pas de cours cette semaine"},
    "empty_day": {"ru": "В этот день занятий нет", "en": "No classes on this day", "fr": "Pas de cours ce jour-là"},
    "empty": {"ru": "Занятий нет", "en": "No classes", "fr": "Pas de cours"},
    "date_usage": {"ru": "Формат: /date 15.10.2026", "en": "Format: /date 15.10.2026",
                   "fr": f"Format{NBSP}: /date 15.10.2026"},
    "stale_offline": {
        "ru": "⚠️ Расписание обновлялось {ago} — ноутбук с sync давно не выходил в сеть. "
              "Возможны изменения, сверяйтесь с lk.gubkin.ru/schedule.",
        "en": "⚠️ The timetable was last updated {ago}. There may have been changes since then, "
              "so check lk.gubkin.ru/schedule.",
        "fr": "⚠️ L'emploi du temps a été mis à jour {ago}. Il a pu changer depuis{NBSP}: "
              "vérifiez sur lk.gubkin.ru/schedule.".replace("{NBSP}", NBSP),
    },
    "stale_site": {
        "ru": "⚠️ Сайт Губкина сейчас не отвечает — показываю последнее сохранённое расписание.",
        "en": "⚠️ The Gubkin University website isn't responding, so here is the last saved timetable.",
        "fr": f"⚠️ Le site de l'Université Gubkin ne répond pas{NBSP}: voici le dernier emploi du temps enregistré.",
    },
    "captcha_needed": {
        "ru": "Сайт попросил капчу. Отправьте /captcha и введите код с картинки.",
        "en": "The website is asking for a captcha. Send /captcha and type the code from the picture.",
        "fr": "Le site demande un captcha. Envoyez /captcha et saisissez le code de l'image.",
    },
    "site_down": {
        "ru": "⏳ Сайт Губкина не отвечает.\n\n"
              "Если на компьютере с ботом включён VPN — сайт может не открываться через него. "
              "Попробуйте позже или настройте VPN так, чтобы российские сайты шли напрямую.",
        "en": "⏳ The Gubkin University website isn't responding. Please try again later.",
        "fr": "⏳ Le site de l'Université Gubkin ne répond pas. Veuillez réessayer plus tard.",
    },
    "fetch_failed": {"ru": "Не удалось получить расписание: {e}", "en": "Couldn't get the timetable: {e}",
                     "fr": f"Impossible de récupérer l'emploi du temps{NBSP}: {{e}}"},
    "no_classes_2days": {"ru": "Ни сегодня, ни завтра пар нет 🎉", "en": "No classes today or tomorrow 🎉",
                         "fr": "Pas de cours aujourd'hui ni demain 🎉"},
    "now": {"ru": "🔴 Сейчас", "en": "🔴 Now", "fr": "🔴 En cours"},
    "next": {"ru": "⏭ Следующая", "en": "⏭ Next", "fr": "⏭ Ensuite"},
    "room": {"ru": "ауд. {room}", "en": "room {room}", "fr": "salle {room}"},
    "no_room": {"ru": "аудитория не указана", "en": "room not specified", "fr": "salle non précisée"},
    "today_title": {"ru": "Сегодня, {date}", "en": "Today, {date}", "fr": "Aujourd'hui, {date}"},
    "tomorrow_title": {"ru": "Завтра, {date}", "en": "Tomorrow, {date}", "fr": "Demain, {date}"},
    "open_map": {"ru": "🗺 Открыть карту", "en": "🗺 Open the map", "fr": "🗺 Ouvrir le plan"},
    "map_note": {"ru": "⚠️ Карта ещё находится в разработке и может быть неточной.",
                 "en": "⚠️ The map is still in development and may be inaccurate.",
                 "fr": "⚠️ Le plan est encore en développement et peut être inexact."},
    "subscribed": {
        "ru": "Готово! Каждое утро в {time} пришлю расписание на день (если есть пары). Отписаться — /unsubscribe",
        "en": "Done! Every morning at {time} I'll send you the day's timetable (if you have classes). "
              "To unsubscribe, send /unsubscribe",
        "fr": f"C'est fait{NBSP}! Chaque matin à {{time}}, je vous enverrai l'emploi du temps du jour "
              f"(s'il y a des cours). Pour vous désabonner, envoyez /unsubscribe",
    },
    "unsubscribed": {"ru": "Вы отписались от утренней рассылки.",
                     "en": "You've unsubscribed from the morning timetable.",
                     "fr": "Vous ne recevrez plus l'emploi du temps du matin."},
    "morning": {"ru": "Доброе утро! ☀️ Расписание на сегодня:", "en": "Good morning! ☀️ Here's today's timetable:",
                "fr": f"Bonjour{NBSP}! ☀️ Voici votre emploi du temps d'aujourd'hui{NBSP}:"},
    "today": {"ru": "сегодня", "en": "today", "fr": "aujourd'hui"},
    "tomorrow": {"ru": "завтра", "en": "tomorrow", "fr": "demain"},
    "cancelled": {"ru": "пара отменена", "en": "class cancelled", "fr": "cours annulé"},
    "subgroup": {"ru": "{n} подгруппа", "en": "subgroup {n}", "fr": "sous-groupe {n}"},
    "break": {"ru": "☕ перемена {n} мин", "en": "☕ {n}-minute break", "fr": "☕ pause de {n}{NBSP}min".replace(
        "{NBSP}", NBSP)},
    "gap": {"ru": "🕐 окно {len}", "en": "🕐 free period: {len}", "fr": f"🕐 créneau libre{NBSP}: {{len}}"},
    "week_upper": {"ru": "верхняя неделя", "en": "Upper (odd) week", "fr": "Semaine impaire"},
    "week_lower": {"ru": "нижняя неделя", "en": "Lower (even) week", "fr": "Semaine paire"},
    "ago_hours": {"ru": "{n} ч назад", "en": "{n} h ago", "fr": f"il y a {{n}}{NBSP}h"},
    "long_ago": {"ru": "давно", "en": "a long time ago", "fr": "il y a longtemps"},
}


def t(key: str, lang: str = "ru", **kw) -> str:
    variants = TEXT[key]
    s = variants.get(lang) or variants.get("ru", "")
    return s.format(**kw) if kw else s


def duration(minutes: int, lang: str = "ru") -> str:
    h, m = divmod(minutes, 60)
    hu, mu = ("ч", "мин") if lang == "ru" else ("h", "min")
    sp = NBSP if lang == "fr" else " "
    if h and m:
        return f"{h}{sp}{hu} {m}{sp}{mu}"
    return f"{h}{sp}{hu}" if h else f"{m}{sp}{mu}"


def days_ago(days: int, lang: str) -> str:
    if lang == "en":
        return "1 day ago" if days == 1 else f"{days} days ago"
    if lang == "fr":
        return "il y a 1 jour" if days == 1 else f"il y a {days} jours"
    word = "день" if days % 10 == 1 and days % 100 != 11 else \
        "дня" if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14 else "дней"
    return f"{days} {word} назад"


# ---- даты

WEEKDAYS = {
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
}
MONTHS = {
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"],
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"],
}


def day_month(d: dt.date, lang: str) -> str:
    """'14 октября' / 'October 14' / '14 octobre' (1er для первого числа во французском)."""
    if lang == "en":
        return f"{MONTHS['en'][d.month - 1]} {d.day}"
    if lang == "fr":
        return f"{'1er' if d.day == 1 else d.day} {MONTHS['fr'][d.month - 1]}"
    return f"{d.day} {MONTHS['ru'][d.month - 1]}"


def weekday_date(d: dt.date, lang: str, ru_weekday: str = "") -> str:
    """'Вторник, 14 октября' / 'Tuesday, October 14' / 'Mardi 14 octobre'."""
    if lang == "en":
        return f"{WEEKDAYS['en'][d.weekday()]}, {day_month(d, 'en')}"
    if lang == "fr":
        return f"{WEEKDAYS['fr'][d.weekday()].capitalize()} {day_month(d, 'fr')}"
    return f"{ru_weekday}, {day_month(d, 'ru')}"


# ---- корпуса и этажи

BUILDINGS = {
    "main": {"ru": "Главный корпус", "en": "Main building", "fr": "Bâtiment principal"},
    "k2": {"ru": "2 корпус", "en": "Building 2", "fr": "Bâtiment 2"},
    "k3": {"ru": "3 корпус", "en": "Building 3", "fr": "Bâtiment 3"},
    "lib": {"ru": "Библиотека", "en": "Library", "fr": "Bibliothèque"},
}
BAA = {"ru": "Большая академическая аудитория", "en": "Great Academic Hall", "fr": "Grand amphithéâtre"}


def _en_ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def floor_label(n: int, lang: str = "ru") -> str:
    if lang == "en":
        return "lower ground floor" if n == 0 else f"{_en_ordinal(n)} floor"
    if lang == "fr":
        if n == 0:
            return "demi-sous-sol"
        if n == 1:
            return "rez-de-chaussée"
        return "1er étage" if n == 2 else f"{n - 1}e étage"
    return "цокольный этаж" if n == 0 else f"{n} этаж"


def locate_room(room: str, lang: str = "ru") -> str:
    """'2435' -> '3 корпус, 4 этаж'. Нумерация — по путеводителю первокурсника."""
    low = (room or "").lower()
    if "баа" in low:
        return f"{BUILDINGS['main'][lang]}, {BAA[lang]}"
    m = re.match(r"\s*(\d{3,4})", room or "")
    if not m:
        return ""
    d = m.group(1)
    if len(d) == 3:
        building, floor = "main", int(d[0])
    else:
        building = {"1": "k2", "2": "k3", "5": "lib"}.get(d[0])
        floor = int(d[1])
    if not building:
        return ""
    return f"{BUILDINGS[building][lang]}, {floor_label(floor, lang)}"


# ---- типы занятий

KINDS = [  # (часть русского названия, английский, французский)
    ("дифференцированный зач", "Graded pass/fail test", "Épreuve de validation notée"),
    ("лекц", "Lecture", "Cours magistral"),
    ("семин", "Seminar", "TD"),
    ("практ", "Practical class", "TD"),
    ("лаб", "Lab", "TP"),
    ("экзам", "Exam", "Examen"),
    ("зач", "Pass/fail test", "Épreuve de validation"),
    ("консул", "Q&A session", "Séance de questions-réponses"),
    ("курсов", "Coursework", "Projet de semestre"),
]


def kind(name: str, lang: str = "ru") -> str:
    if lang == "ru" or not name:
        return name
    low = name.lower().replace("ё", "е")
    for part, en, fr in KINDS:
        if part.replace("ё", "е") in low:
            return en if lang == "en" else fr
    return name


# ---- предметы (переведены вручную; ключ — название как на сайте, без учёта регистра и «ё»)

SUBJECTS = {
    # естественные науки и математика
    "физика": ("Physics", "Physique"),
    "химия": ("Chemistry", "Chimie"),
    "общая химия": ("General Chemistry", "Chimie générale"),
    "неорганическая химия": ("Inorganic Chemistry", "Chimie inorganique"),
    "органическая химия": ("Organic Chemistry", "Chimie organique"),
    "аналитическая химия": ("Analytical Chemistry", "Chimie analytique"),
    "физическая химия": ("Physical Chemistry", "Chimie physique"),
    "химия нефти и газа": ("Petroleum and Gas Chemistry", "Chimie du pétrole et du gaz"),
    "математика": ("Mathematics", "Mathématiques"),
    "высшая математика": ("Higher Mathematics", "Mathématiques supérieures"),
    "математический анализ": ("Calculus", "Analyse mathématique"),
    "линейная алгебра": ("Linear Algebra", "Algèbre linéaire"),
    "аналитическая геометрия": ("Analytic Geometry", "Géométrie analytique"),
    "линейная алгебра и аналитическая геометрия": ("Linear Algebra and Analytic Geometry",
                                                   "Algèbre linéaire et géométrie analytique"),
    "дифференциальные уравнения": ("Differential Equations", "Équations différentielles"),
    "теория вероятностей и математическая статистика": ("Probability Theory and Mathematical Statistics",
                                                        "Probabilités et statistique mathématique"),
    "теоретическая механика": ("Theoretical Mechanics", "Mécanique théorique"),
    "информатика": ("Computer Science", "Informatique"),
    "программирование": ("Programming", "Programmation"),
    "экология": ("Ecology", "Écologie"),
    "биология": ("Biology", "Biologie"),
    # геология
    "общая геология": ("General Geology", "Géologie générale"),
    "геология": ("Geology", "Géologie"),
    "историческая геология": ("Historical Geology", "Géologie historique"),
    "структурная геология": ("Structural Geology", "Géologie structurale"),
    "геология нефти и газа": ("Petroleum Geology", "Géologie du pétrole et du gaz"),
    "кристаллография": ("Crystallography", "Cristallographie"),
    "минералогия": ("Mineralogy", "Minéralogie"),
    "кристаллография и минералогия": ("Crystallography and Mineralogy", "Cristallographie et minéralogie"),
    "петрография": ("Petrography", "Pétrographie"),
    "литология": ("Lithology", "Lithologie"),
    "палеонтология": ("Paleontology", "Paléontologie"),
    "геохимия": ("Geochemistry", "Géochimie"),
    "геофизика": ("Geophysics", "Géophysique"),
    "геодезия": ("Geodesy", "Géodésie"),
    "гидрогеология": ("Hydrogeology", "Hydrogéologie"),
    "геоморфология": ("Geomorphology", "Géomorphologie"),
    "геотектоника": ("Geotectonics", "Géotectonique"),
    "введение в специальность": ("Introduction to the Specialty", "Introduction à la spécialité"),
    "введение в профессию": ("Introduction to the Profession", "Introduction au métier"),
    # графика
    "начертательная геометрия": ("Descriptive Geometry", "Géométrie descriptive"),
    "инженерная графика": ("Engineering Graphics", "Dessin technique"),
    "начертательная геометрия и инженерная графика": ("Descriptive Geometry and Engineering Graphics",
                                                      "Géométrie descriptive et dessin technique"),
    "компьютерная графика": ("Computer Graphics", "Infographie"),
    # гуманитарные
    "история россии": ("History of Russia", "Histoire de la Russie"),
    "история": ("History", "Histoire"),
    "философия": ("Philosophy", "Philosophie"),
    "основы российской государственности": ("Foundations of Russian Statehood",
                                            "Fondements de l'État russe"),
    "русский язык и культура речи": ("Russian Language and Effective Communication",
                                     "Langue russe et expression orale"),
    "иностранный язык": ("Foreign Language", "Langue étrangère"),
    "английский язык": ("English", "Anglais"),
    "немецкий язык": ("German", "Allemand"),
    "французский язык": ("French", "Français"),
    "экономика": ("Economics", "Économie"),
    "правоведение": ("Law", "Droit"),
    "психология": ("Psychology", "Psychologie"),
    "социология": ("Sociology", "Sociologie"),
    "безопасность жизнедеятельности": ("Health and Safety", "Santé et sécurité"),
    "основы проектной деятельности": ("Introduction to Project Work", "Initiation au travail en mode projet"),
    # физкультура
    "физическая культура и спорт": ("Physical Education and Sport", "Éducation physique et sportive"),
    "физическая культура": ("Physical Education", "Éducation physique"),
    "элективные дисциплины по физической культуре и спорту": (
        "Elective Physical Education and Sport", "Éducation physique et sportive (option)"),
    "элективные курсы по физической культуре и спорту": (
        "Elective Physical Education and Sport", "Éducation physique et sportive (option)"),
}


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower().replace("ё", "е")).strip(" .")


_SUBJECT_KEYS = {_key(k): v for k, v in SUBJECTS.items()}


def subject(name: str, lang: str = "ru") -> str:
    if lang == "ru" or not name:
        return name
    tr = _SUBJECT_KEYS.get(_key(name))
    if not tr:
        return name
    return tr[0] if lang == "en" else tr[1]


def has_subject(name: str) -> bool:
    return _key(name) in _SUBJECT_KEYS


# ---- пометки об изменениях (их пишет schedule.parse_week)

_NOTES = [
    (r"^Аудитория: (.*)$", "Room: {0}", f"Salle{NBSP}: {{0}}"),
    (r"^Преподаватель: (.*)$", "Teacher: {0}", f"Enseignant{NBSP}: {{0}}"),
    (r"^Перенесено с (.*)$", "Moved from {0}", "Déplacé du {0}"),
    (r"^Перенесено на (.*)$", "Moved to {0}", "Déplacé au {0}"),
]


def note(text: str, lang: str = "ru") -> str:
    if lang == "ru":
        return text
    for pattern, en, fr in _NOTES:
        m = re.match(pattern, text)
        if m:
            return (en if lang == "en" else fr).format(m.group(1))
    return text
