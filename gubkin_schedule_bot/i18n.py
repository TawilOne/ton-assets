"""Переводы бота: русский (основной), английский, французский. Тексты — в TEXT."""
from __future__ import annotations

import json
import pathlib

LANGS = ("ru", "en", "fr")
LANG_NAMES = {"ru": "🇷🇺 Русский", "en": "🇬🇧 English", "fr": "🇫🇷 Français"}
NBSP = "\u00a0"  # во французском перед «:», «!», «?» ставится неразрывный пробел

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
    "welcome": {"ru": "🎮 Привет! Здесь мини-игры: шахматы онлайн с рейтингом.\n\n"
                      "/games — открыть игры\n/settings — язык",
                "en": "🎮 Hi! This bot has mini games: online chess with a rating.\n\n"
                      "/games — open the games\n/settings — language",
                "fr": "🎮 Salut ! Ce bot propose des mini-jeux : échecs en ligne avec classement.\n\n"
                      "/games — ouvrir les jeux\n/settings — langue"},
    "schedule_gone": {"ru": "Расписания и карты в боте больше нет. Остались мини-игры: /games",
                      "en": "The timetable and the map are no longer available. Mini games are still here: /games",
                      "fr": "L'emploi du temps et le plan ne sont plus disponibles. Les mini-jeux restent\u00a0: /games"},
    "settings": {"ru": "⚙️ <b>Настройки</b>\nЯзык: {name}\n\nВыберите язык:",
                 "en": "⚙️ <b>Settings</b>\nLanguage: {name}\n\nChoose a language:",
                 "fr": f"⚙️ <b>Paramètres</b>\nLangue{NBSP}: {{name}}\n\nChoisissez une langue{NBSP}:"},
    "lang_set": {"ru": "✅ Язык: русский", "en": "✅ Language: English", "fr": f"✅ Langue{NBSP}: français"},
    "too_fast": {
        "ru": "🐢 Слишком много сообщений подряд. Подождите полминуты и попробуйте снова.",
        "en": "🐢 Too many messages in a row. Please wait half a minute and try again.",
        "fr": "🐢 Trop de messages d'affilée. Attendez une trentaine de secondes et réessayez.",
    },
    "chess_invite": {"ru": "♟ Вас пригласили сыграть в шахматы!", "en": "♟ You've been invited to a game of chess!",
                     "fr": "♟ On vous invite à une partie d'échecs !"},
    "chess_open": {"ru": "♟ Открыть партию", "en": "♟ Open the game", "fr": "♟ Ouvrir la partie"},
    "games_msg": {"ru": "🎮 Мини-игры: шахматы онлайн.", "en": "🎮 Mini games: online chess.",
                  "fr": "🎮 Mini-jeux : échecs en ligne."},
    "chess_seek": {"ru": "♟ {who} ищет соперника в шахматах. Сыграем?\n\n"
                         "Отключить такие сообщения — кнопкой 🔔 в мини-играх.",
                   "en": "♟ {who} is looking for a chess opponent. Fancy a game?\n\n"
                         "To stop these messages, use the 🔔 button in the mini games.",
                   "fr": f"♟ {{who}} cherche un adversaire aux échecs. Une partie{NBSP}?\n\n"
                         f"Pour ne plus recevoir ces messages{NBSP}: bouton 🔔 dans les mini-jeux."},
    "chess_found": {"ru": "♟ Соперник нашёлся: {who}. Партия началась — заходите!",
                    "en": "♟ Opponent found: {who}. The game has started — come and play!",
                    "fr": f"♟ Adversaire trouvé{NBSP}: {{who}}. La partie a commencé, venez jouer{NBSP}!"},
    "chess_found_btn": {"ru": "♟ К партии", "en": "♟ Open the game", "fr": "♟ Ouvrir la partie"},
    "chess_seek_btn": {"ru": "♟ Играть", "en": "♟ Play", "fr": "♟ Jouer"},
    "games_open": {"ru": "🎮 Открыть игры", "en": "🎮 Open games", "fr": "🎮 Ouvrir les jeux"},
    "oops": {"ru": "😕 Что-то пошло не так. Попробуйте ещё раз чуть позже.",
             "en": "😕 Something went wrong. Please try again a bit later.",
             "fr": "😕 Un problème est survenu. Réessayez un peu plus tard."},
}


def t(key: str, lang: str = "ru", **kw) -> str:
    variants = TEXT[key]
    s = variants.get(lang) or variants.get("ru", "")
    return s.format(**kw) if kw else s


