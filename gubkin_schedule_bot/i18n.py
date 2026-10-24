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
        tmp = LANG_FILE.with_name(LANG_FILE.name + ".tmp")  # целиком или никак: обрывок стёр бы языки всех
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(LANG_FILE)
    except OSError:
        pass


# ---- тексты

TEXT = {
    "help": {
        "ru": "📅 Расписание РГУ им. Губкина\n{group_line}\n\n"
              "/today — на сегодня\n"
              "/tomorrow — на завтра\n"
              "/week — текущая неделя\n"
              "/group — выбрать группу\n"
              "/subscribe — присылать расписание в выбранное время\n"
              "/settings — язык",
        "en": "📅 Gubkin University timetable\n{group_line}\n\n"
              "/today — today's classes\n"
              "/tomorrow — tomorrow's classes\n"
              "/week — this week\n"
              "/group — choose your group\n"
              "/subscribe — get the timetable at a time you choose\n"
              "/settings — language",
        "fr": "📅 Emploi du temps de l'Université Gubkin\n{group_line}\n\n"
              "/today — les cours d'aujourd'hui\n"
              "/tomorrow — les cours de demain\n"
              "/week — cette semaine\n"
              "/group — choisir votre groupe\n"
              "/subscribe — recevoir l'emploi du temps à l'heure choisie\n"
              "/settings — langue",
    },
    # ---- выбор группы
    "btn_group": {"ru": "👥 Моя группа", "en": "👥 My group", "fr": "👥 Mon groupe"},
    "btn_group_change": {"ru": "👥 Сменить группу", "en": "👥 Change group", "fr": "👥 Changer de groupe"},
    "btn_back": {"ru": "⬅️ Назад", "en": "⬅️ Back", "fr": "⬅️ Retour"},
    "group_current": {"ru": "👥 Группа: {code}", "en": "👥 Group: {code}", "fr": f"👥 Groupe{NBSP}: {{code}}"},
    "group_none": {"ru": "👥 Группа не выбрана", "en": "👥 No group chosen", "fr": "👥 Aucun groupe choisi"},
    "group_choose_faculty": {"ru": "🏛 Выберите факультет:", "en": "🏛 Choose your faculty:",
                             "fr": f"🏛 Choisissez votre faculté{NBSP}:"},
    "group_choose_year": {"ru": "👥 Выберите курс:", "en": "👥 Choose your year:",
                          "fr": f"👥 Choisissez votre année{NBSP}:"},
    "group_choose": {"ru": "👥 Выберите группу:", "en": "👥 Choose your group:",
                     "fr": f"👥 Choisissez votre groupe{NBSP}:"},
    "group_first": {"ru": "Сначала выберите свою группу 👇", "en": "First, choose your group 👇",
                    "fr": "Choisissez d'abord votre groupe 👇"},
    "group_set": {
        "ru": "✅ Ваша группа: {code}\nТеперь расписание показывается для неё. "
              "Сменить группу — кнопка «👥 Моя группа» или /group",
        "en": "✅ Your group: {code}\nThe timetable is now shown for this group. "
              "To change it, tap “👥 My group” or send /group",
        "fr": f"✅ Votre groupe{NBSP}: {{code}}\nL'emploi du temps est maintenant celui de ce groupe. "
              f"Pour en changer, appuyez sur «{NBSP}👥 Mon groupe{NBSP}» ou envoyez /group",
    },
    "group_pending": {
        "ru": "⏳ Расписание группы {code} ещё загружается. Оно появится в течение нескольких часов, "
              "при следующем обновлении.",
        "en": "⏳ The timetable for group {code} is still loading. It will appear within a few hours, "
              "after the next update.",
        "fr": "⏳ L'emploi du temps du groupe {code} est en cours de chargement. Il apparaîtra d'ici "
              "quelques heures, lors de la prochaine mise à jour.",
    },
    "group_unknown": {
        "ru": "Не нашёл группу {code}. Выберите из списка 👇",
        "en": "I couldn't find group {code}. Choose from the list 👇",
        "fr": "Je n'ai pas trouvé le groupe {code}. Choisissez dans la liste 👇",
    },
    "group_list_loading": {
        "ru": "Список всех групп ещё загружается — загляните через несколько часов.",
        "en": "The full list of groups is still loading. Please check back in a few hours.",
        "fr": "La liste complète des groupes est en cours de chargement. "
              "Revenez dans quelques heures.",
    },
    "groups_other": {"ru": "Другие", "en": "Other", "fr": "Autres"},
    "beta": {
        "ru": "⚠️ Бот и карта аудиторий ещё в разработке. Возможны ошибки в расписании и на карте — "
              "не опирайтесь на них полностью и сверяйтесь с официальным расписанием: lk.gubkin.ru/schedule",
        "en": "⚠️ The bot and the room map are still in development. The timetable and the map may contain "
              "errors, so don't rely on them completely and check the official timetable: lk.gubkin.ru/schedule",
        "fr": "⚠️ Le bot et le plan des salles sont encore en développement. L'emploi du temps et le plan "
              "peuvent contenir des erreurs. Ne vous y fiez pas entièrement et consultez l'emploi du temps "
              f"officiel{NBSP}: lk.gubkin.ru/schedule",
    },
    "feedback": {
        "ru": "✉️ Если заметили ошибку в расписании — напишите @TawiOner",
        "en": "✉️ If you notice a mistake in the timetable, message @TawiOner",
        "fr": "✉️ Si vous remarquez une erreur dans l'emploi du temps, écrivez à @TawiOner",
    },
    "btn_today": {"ru": "📅 Сегодня", "en": "📅 Today", "fr": "📅 Aujourd'hui"},
    "btn_tomorrow": {"ru": "➡️ Завтра", "en": "➡️ Tomorrow", "fr": "➡️ Demain"},
    "btn_week": {"ru": "🗓 Неделя", "en": "🗓 This week", "fr": "🗓 Cette semaine"},
    "btn_next": {"ru": "⏭ След. неделя", "en": "⏭ Next week", "fr": "⏭ Semaine prochaine"},
    "btn_map": {"ru": "🗺 Где пара", "en": "🗺 Where's my class", "fr": "🗺 Où est mon cours"},
    "btn_digest": {"ru": "⏰ Рассылка", "en": "⏰ Daily timetable", "fr": "⏰ Envoi quotidien"},
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
        "ru": "⚠️ Расписание обновлялось {ago}. С тех пор могли быть изменения — "
              "сверяйтесь с lk.gubkin.ru/schedule.",
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
    "data_error": {
        "ru": "⚠️ Произошла ошибка: расписание с сайта выглядит неправильно, поэтому я его не показываю. "
              "Посмотрите расписание на lk.gubkin.ru/schedule — я уже разбираюсь.",
        "en": "⚠️ An error occurred: the timetable from the website looks wrong, so I'm not showing it. "
              "Please check lk.gubkin.ru/schedule. I'm already looking into it.",
        "fr": "⚠️ Une erreur s'est produite : l'emploi du temps reçu du site semble incorrect, je ne l'affiche donc "
              "pas. Consultez lk.gubkin.ru/schedule. Je m'en occupe déjà.",
    },
    "site_busy": {
        "ru": "⏳ Сайт Губкина временно ограничил запросы. Попробуйте через несколько минут.",
        "en": "⏳ The Gubkin University website is temporarily limiting requests. Please try again in a few minutes.",
        "fr": "⏳ Le site de l'Université Gubkin limite temporairement les requêtes. Réessayez dans quelques minutes.",
    },
    "too_fast": {
        "ru": "🐢 Слишком много сообщений подряд. Подождите полминуты и попробуйте снова.",
        "en": "🐢 Too many messages in a row. Please wait half a minute and try again.",
        "fr": "🐢 Trop de messages d'affilée. Attendez une trentaine de secondes et réessayez.",
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
    "chess_invite": {"ru": "♟ Вас пригласили сыграть в шахматы!", "en": "♟ You've been invited to a game of chess!",
                     "fr": "♟ On vous invite à une partie d'échecs !"},
    "chess_open": {"ru": "♟ Открыть партию", "en": "♟ Open the game", "fr": "♟ Ouvrir la partie"},
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
    "unsubscribed": {"ru": "🔕 Рассылка отключена. Включить снова — /settings",
                     "en": "🔕 The daily timetable is turned off. To turn it back on: /settings",
                     "fr": f"🔕 L'envoi quotidien est désactivé. Pour le réactiver{NBSP}: /settings"},
    "sub_off": {"ru": "⏰ Рассылка расписания: выключена", "en": "⏰ Daily timetable: off",
                "fr": f"⏰ Envoi de l'emploi du temps{NBSP}: désactivé"},
    "sub_on": {"ru": "⏰ Рассылка расписания: каждый день в {time}", "en": "⏰ Daily timetable: every day at {time}",
               "fr": f"⏰ Envoi de l'emploi du temps{NBSP}: tous les jours à {{time}}"},
    "btn_sub_set": {"ru": "⏰ Выбрать время рассылки", "en": "⏰ Set a time", "fr": "⏰ Choisir l'heure"},
    "btn_sub_change": {"ru": "⏰ Изменить время", "en": "⏰ Change the time", "fr": "⏰ Modifier l'heure"},
    "btn_sub_off": {"ru": "🔕 Отключить рассылку", "en": "🔕 Turn off", "fr": "🔕 Désactiver"},
    "sub_ask": {
        "ru": "Во сколько присылать расписание? Напишите время ответным сообщением (по Москве).\n\n"
              "Например: <b>7:00</b>, <b>07:30</b> или <b>21:15</b>\n\n"
              "Утром пришлю расписание на этот день, а если время вечернее (с 18:00) — на следующий день.",
        "en": "What time should I send you the timetable? Reply with a time (Moscow time).\n\n"
              "For example: <b>7:00</b>, <b>07:30</b> or <b>21:15</b>\n\n"
              "In the morning I'll send that day's timetable; if you choose an evening time (18:00 or later), "
              "I'll send the next day's.",
        "fr": f"À quelle heure voulez-vous recevoir l'emploi du temps{NBSP}? Répondez avec une heure "
              "(heure de Moscou).\n\n"
              f"Par exemple{NBSP}: <b>7:00</b>, <b>07:30</b> ou <b>21:15</b>\n\n"
              "Le matin, je vous enverrai l'emploi du temps du jour{NBSP}; si vous choisissez une heure du soir "
              "(à partir de 18:00), celui du lendemain.".replace("{NBSP}", NBSP),
    },
    "sub_bad": {"ru": "Не понял время 🤔 Напишите, например: <b>7:00</b>",
                "en": "I didn't understand the time 🤔 Please write it like this: <b>7:00</b>",
                "fr": f"Je n'ai pas compris l'heure 🤔 Écrivez-la par exemple ainsi{NBSP}: <b>7:00</b>"},
    "sub_done_today": {
        "ru": "✅ Готово! Каждый день в {time} буду присылать расписание на этот день (если есть пары).\n"
              "Изменить время или отключить — /settings",
        "en": "✅ Done! Every day at {time} I'll send you that day's timetable (if you have classes).\n"
              "To change the time or turn it off: /settings",
        "fr": f"✅ C'est fait{NBSP}! Tous les jours à {{time}}, je vous enverrai l'emploi du temps du jour "
              f"(s'il y a des cours).\nPour modifier l'heure ou désactiver l'envoi{NBSP}: /settings",
    },
    "sub_done_tomorrow": {
        "ru": "✅ Готово! Каждый день в {time} буду присылать расписание на следующий день (если есть пары).\n"
              "Изменить время или отключить — /settings",
        "en": "✅ Done! Every day at {time} I'll send you the next day's timetable (if you have classes).\n"
              "To change the time or turn it off: /settings",
        "fr": f"✅ C'est fait{NBSP}! Tous les jours à {{time}}, je vous enverrai l'emploi du temps du lendemain "
              f"(s'il y a des cours).\nPour modifier l'heure ou désactiver l'envoi{NBSP}: /settings",
    },
    "digest_today": {"ru": "📅 Расписание на сегодня:", "en": "📅 Today's timetable:",
                     "fr": f"📅 Votre emploi du temps d'aujourd'hui{NBSP}:"},
    "evening": {"ru": "Добрый вечер! 🌙 Расписание на завтра:", "en": "Good evening! 🌙 Here's tomorrow's timetable:",
                "fr": f"Bonsoir{NBSP}! 🌙 Voici votre emploi du temps de demain{NBSP}:"},
    "morning": {"ru": "Доброе утро! ☀️ Расписание на сегодня:", "en": "Good morning! ☀️ Here's today's timetable:",
                "fr": f"Bonjour{NBSP}! ☀️ Voici votre emploi du temps d'aujourd'hui{NBSP}:"},
    "captcha_failed": {"ru": "Не удалось загрузить капчу: {e}", "en": "Couldn't load the captcha: {e}",
                       "fr": f"Impossible de charger le captcha{NBSP}: {{e}}"},
    "captcha_prompt": {"ru": "Введите символы с картинки ответным сообщением",
                       "en": "Reply with the characters from the picture",
                       "fr": "Répondez en saisissant les caractères de l'image"},
    "captcha_ok": {"ru": "Капча принята ✅ Повторите команду.",
                   "en": "Captcha accepted ✅ Please repeat the command.",
                   "fr": "Captcha accepté ✅ Veuillez relancer la commande."},
    "captcha_bad": {"ru": "Неверно. Отправьте /captcha ещё раз.", "en": "Incorrect. Send /captcha again.",
                    "fr": "Incorrect. Envoyez à nouveau /captcha."},
    "export_wait": {"ru": "Выгружаю расписание {group} с {a} по {b}… это займёт около минуты.",
                    "en": "Exporting the {group} timetable from {a} to {b}… this will take about a minute.",
                    "fr": "J'exporte l'emploi du temps du groupe {group} du {a} au {b}… "
                          "cela prendra environ une minute."},
    "export_done": {"ru": "Готово: {n} занятий с {a} по {b}.", "en": "Done: {n} classes from {a} to {b}.",
                    "fr": f"C'est fait{NBSP}: {{n}} cours du {{a}} au {{b}}."},
    "export_title": {"ru": "Расписание {group}", "en": "{group} timetable", "fr": "Emploi du temps {group}"},
    "file_ics": {"ru": "календарь (Google/Apple/Outlook)", "en": "calendar (Google/Apple/Outlook)",
                 "fr": "calendrier (Google/Apple/Outlook)"},
    "file_csv": {"ru": "таблица для Excel", "en": "spreadsheet for Excel", "fr": "tableau pour Excel"},
    "file_json": {"ru": "данные", "en": "data", "fr": "données"},
    "file_txt": {"ru": "текст", "en": "plain text", "fr": "texte"},
    "today": {"ru": "сегодня", "en": "today", "fr": "aujourd'hui"},
    "tomorrow": {"ru": "завтра", "en": "tomorrow", "fr": "demain"},
    "oops": {"ru": "😕 Что-то пошло не так. Попробуйте ещё раз чуть позже.",
             "en": "😕 Something went wrong. Please try again a bit later.",
             "fr": "😕 Un problème est survenu. Réessayez un peu plus tard."},
    "cancelled": {"ru": "пара отменена", "en": "class cancelled", "fr": "cours annulé"},
    "chg_title": {"ru": "🔔 Изменение в расписании · {code}", "en": "🔔 Timetable change · {code}",
                  "fr": "🔔 Changement d'emploi du temps · {code}"},
    "chg_cancel": {"ru": "❌ Отмена", "en": "❌ Cancelled", "fr": "❌ Annulé"},
    "chg_move": {"ru": "🔁 Перенос", "en": "🔁 Moved", "fr": "🔁 Déplacé"},
    "chg_room": {"ru": "🚪 Другая аудитория", "en": "🚪 Room change", "fr": "🚪 Changement de salle"},
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


def course(n: int, lang: str = "ru") -> str:
    """Номер курса: «1 курс», «Year 1», «1re année» / «2e année»."""
    if lang == "en":
        return f"Year {n}"
    if lang == "fr":
        return f"{n}{'re' if n == 1 else 'e'} année"
    return f"{n} курс"


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
    ("мероприят", "Event", "Événement"),
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
    "введение в геологию": ("Introduction to Geology", "Introduction à la géologie"),
    "геодезия и топография": ("Geodesy and Topography", "Géodésie et topographie"),
    "топография": ("Topography", "Topographie"),
    "введение в профессию": ("Introduction to the Profession", "Introduction au métier"),
    # графика
    "начертательная геометрия": ("Descriptive Geometry", "Géométrie descriptive"),
    "инженерная графика": ("Engineering Graphics", "Dessin technique"),
    "начертательная геометрия и инженерная графика": ("Descriptive Geometry and Engineering Graphics",
                                                      "Géométrie descriptive et dessin technique"),
    "компьютерная графика": ("Computer Graphics", "Infographie"),
    "инженерно-геологическая графика": ("Engineering Geological Graphics",
                                        "Dessin technique en géologie de l'ingénieur"),
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
    "профессионально-личностное саморазвитие": ("Professional and Personal Self-Development",
                                                "Développement personnel et professionnel"),
    "социология": ("Sociology", "Sociologie"),
    "безопасность жизнедеятельности": ("Health and Safety", "Santé et sécurité"),
    "основы системного и критического мышления": ("Fundamentals of Systems and Critical Thinking",
                                                  "Fondements de la pensée systémique et critique"),
    "критическое мышление": ("Critical Thinking", "Pensée critique"),
    # не предметы, а виды занятий, которые сайт иногда пишет вместо названия
    "консультация": ("Q&A session", "Séance de questions-réponses"),
    "мероприятие": ("Event", "Événement"),
    "экзамен": ("Exam", "Examen"),
    "зачет": ("Pass/fail test", "Épreuve de validation"),
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


# «Иностранный язык (английский)» — язык в скобках; русский как иностранный — отдельное название
LANGUAGES = {
    "английский": ("English", "anglais"), "немецкий": ("German", "allemand"),
    "французский": ("French", "français"), "испанский": ("Spanish", "espagnol"),
    "итальянский": ("Italian", "italien"), "китайский": ("Chinese", "chinois"),
    "арабский": ("Arabic", "arabe"), "японский": ("Japanese", "japonais"),
}
_FOREIGN = re.compile(r"^иностранный язык\s*\(([^)]+)\)$")


def _foreign(name: str):
    m = _FOREIGN.match(_key(name))
    if not m:
        return None
    language = m.group(1).strip()
    if language == "русский":
        return ("Russian as a Foreign Language", "Russe langue étrangère")
    if language in LANGUAGES:
        en, fr = LANGUAGES[language]
        return (f"Foreign Language ({en})", f"Langue étrangère ({fr})")
    return None


def subject(name: str, lang: str = "ru") -> str:
    if lang == "ru" or not name:
        return name
    tr = _SUBJECT_KEYS.get(_key(name)) or _foreign(name)
    if not tr:
        return name
    return tr[0] if lang == "en" else tr[1]


def has_subject(name: str) -> bool:
    return _key(name) in _SUBJECT_KEYS or _foreign(name) is not None


# ---- пометки об изменениях (их пишет schedule.parse_week)

_NOTES = [
    (r"^Аудитория: (.*)$", "Room: {0}", f"Salle{NBSP}: {{0}}"),
    (r"^Преподаватель: (.*)$", "Teacher: {0}", f"Enseignant{NBSP}: {{0}}"),
    (r"^Перенесено с (.*)$", "Moved from {0}", "Déplacé du {0}"),
    (r"^Перенесено на (.*)$", "Moved to {0}", "Déplacé au {0}"),
]


# «Консультация по дисциплине "Введение в геологию"» и т. п. — вид занятия + предмет из словаря
_BY_SUBJECT = re.compile(r'^\s*(консультаци[яи]|экзамен|зач[её]т|лекци[яи]|семинар)\s+по\s+(?:дисциплине\s+)?'
                         r'["«“„]?(.+?)["»”“]?\s*\.?\s*$', re.IGNORECASE)


def note(text: str, lang: str = "ru") -> str:
    if lang == "ru":
        return text
    for pattern, en, fr in _NOTES:
        m = re.match(pattern, text)
        if m:
            return (en if lang == "en" else fr).format(m.group(1))
    m = _BY_SUBJECT.match(text)
    if m:
        what, subj = kind(m.group(1), lang), subject(m.group(2), lang)
        if subj != m.group(2) or has_subject(m.group(2)):
            return f"{what}{NBSP if lang == 'fr' else ''}: {subj}"
        q = ("«\u00a0", "\u00a0»") if lang == "fr" else ("“", "”")
        return f"{what}{NBSP if lang == 'fr' else ''}: {q[0]}{m.group(2)}{q[1]}"
    if has_subject(text.strip(" .")):
        return subject(text.strip(" ."), lang)
    return text
