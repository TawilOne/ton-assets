"""Сервер мини-игр для мини-приложения (пока — шахматы): партии, поиск соперника, приглашения, рейтинг.

    python games.py          — слушает 127.0.0.1:8090; снаружи HTTPS даёт Caddy (deploy/games.sh)

Кто играет, сервер узнаёт по подписи Telegram (initData мини-приложения, проверяется токеном бота) —
подделать чужое имя или рейтинг нельзя. Ходы проверяет python-chess: незаконный ход не пройдёт.
Факультет игрока берётся из группы, которую он выбрал в боте (user_groups.json, groups.json рядом).
Партии и рейтинг — в games.db (SQLite).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import pathlib
import random
import secrets
import sqlite3
import time
import urllib.parse

import asyncio
import shutil

import chess
import chess.engine
from aiohttp import web

from faculties import FACULTIES

HERE = pathlib.Path(__file__).resolve().parent
DB_FILE = HERE / "games.db"
# откуда открывается мини-приложение (GitHub Pages); для проверки на своём компьютере — GAMES_ORIGINS
ORIGINS = {"https://tawilone.github.io", *filter(None, os.environ.get("GAMES_ORIGINS", "").split())}
AUTH_MAX_AGE = 2 * 86400   # подпись Telegram действует двое суток с открытия мини-приложения
INACTIVE = 3 * 86400       # три дня без хода — тот, чей ход, проигрывает по времени
ABANDON = 3 * 60           # вышел из партии (закрыл мини-приложение/Telegram) и не вернулся за 3 минуты — поражение
CANCEL_RACE = 15           # соперник подсел за секунды до «Отменить» — отмена всё равно срабатывает
GRACE = 20                 # мини-приложение спрашивает сервер каждые 2–8 с; тишина дольше — человека нет на месте
WAIT_MAX = 86400           # приглашение и поиск соперника живут сутки
START_RATING, K = 1200, 32
BOT = 0  # «игрок» бот: id 0 в партии; партии с ботом не трогают ни рейтинг, ни статистику
# уровни бота: (название, Skill Level Stockfish 0–20, время на ход, доля случайных ходов)
BOT_LEVELS = {1: ("Новичок", 0, 0.05, 0.35), 2: ("Средний", 6, 0.2, 0.0), 3: ("Сильный", 15, 0.6, 0.0)}

log = logging.getLogger("games")


def bot_token() -> str:
    tok = os.environ.get("BOT_TOKEN")
    if not tok:
        try:
            tok = next((l.strip() for l in (HERE / "token.txt").read_text(encoding="utf-8").splitlines() if l.strip()), "")
        except OSError:
            tok = ""
    return tok


def check_init_data(init: str, token: str, now: float | None = None) -> dict | None:
    """Проверка подписи Telegram (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).
    Возвращает пользователя {"id", "first_name", ...} или None, если подпись неверна или устарела."""
    if not init or not token:
        return None
    data = dict(urllib.parse.parse_qsl(init, keep_blank_values=True))
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got):
        return None
    try:
        if (now or time.time()) - int(data.get("auth_date", 0)) > AUTH_MAX_AGE:
            return None
        user = json.loads(data.get("user") or "{}")
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and isinstance(user.get("id"), int) else None


def short_name(user: dict) -> str:
    """«Иван П.» — имя и первая буква фамилии: в общем рейтинге полную фамилию не показываем."""
    first = str(user.get("first_name") or "").strip()[:24]
    last = str(user.get("last_name") or "").strip()
    name = f"{first} {last[0]}." if first and last else first or (last[:24] if last else "")
    return name or "Игрок"


def faculty_of(uid: int) -> str:
    """Факультет по группе, выбранной в боте; пусто — группа не выбрана."""
    try:
        chosen = json.loads((HERE / "user_groups.json").read_text(encoding="utf-8"))
        code = chosen.get(str(uid))
        if not code:
            return ""
        facs = json.loads((HERE / "groups.json").read_text(encoding="utf-8")).get("faculties") or []
    except (OSError, ValueError, AttributeError):
        return ""
    key = next((f.get("key") for f in facs for g in f.get("groups") or [] if g.get("code") == code), None)
    return next((f["ru"] for f in FACULTIES if f["key"] == key), "")


class GameError(Exception):
    """Понятная игроку причина отказа (чужой ход, незаконный ход, партия уже закончена…)."""


class NotSigned(Exception):
    """Запрос не из Telegram (нет подписи или она неверна)."""


class Store:
    def __init__(self, path: pathlib.Path | str = DB_FILE):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS players (id INTEGER PRIMARY KEY, name TEXT, rating INTEGER,
                wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, seen REAL);
            CREATE TABLE IF NOT EXISTS games (id TEXT PRIMARY KEY, kind TEXT, creator INTEGER,
                white INTEGER, black INTEGER, moves TEXT DEFAULT '', status TEXT, result TEXT DEFAULT '',
                reason TEXT DEFAULT '', draw_offer INTEGER DEFAULT 0, created REAL, updated REAL);
            CREATE INDEX IF NOT EXISTS games_status ON games(status, kind);
        """)
        for table, column in (("games", "level INTEGER DEFAULT 0"),  # уровень бота
                              ("players", "photo TEXT DEFAULT ''"),  # аватарка из Telegram (photo_url)
                              ("games", "seen_w REAL DEFAULT 0"), ("games", "seen_b REAL DEFAULT 0")):  # кто когда был в партии
            try:  # у старой базы колонки ещё нет
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            except sqlite3.OperationalError:
                pass
        self.db.commit()

    # ---- игроки

    def touch(self, user: dict) -> None:
        uid, now = user["id"], time.time()
        photo = str(user.get("photo_url") or "")
        if not photo.startswith("https://"):  # только картинки Telegram по https — ничего другого в <img> не пускаем
            photo = ""
        self.db.execute("INSERT INTO players (id, name, rating, seen, photo) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(id) DO UPDATE SET name = excluded.name, seen = excluded.seen, photo = excluded.photo",
                        (uid, short_name(user), START_RATING, now, photo[:300]))
        self.db.commit()

    def player(self, uid: int | None) -> dict | None:
        if not uid:
            return None
        r = self.db.execute("SELECT * FROM players WHERE id = ?", (uid,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "name": r["name"], "rating": r["rating"], "wins": r["wins"],
                "losses": r["losses"], "draws": r["draws"], "faculty": faculty_of(r["id"]), "photo": r["photo"] or ""}

    def top(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute("SELECT id FROM players WHERE wins + losses + draws > 0 "
                               "ORDER BY rating DESC, wins DESC LIMIT ?", (limit,)).fetchall()
        return [self.player(r["id"]) for r in rows]

    def searching(self, uid: int) -> int:
        """Сколько человек прямо сейчас ищут соперника (кроме самого uid)."""
        return self.db.execute("SELECT COUNT(DISTINCT g.creator) FROM games g JOIN players p ON p.id = g.creator "
                               "WHERE g.status = 'wait' AND g.kind = 'search' AND g.creator != ? AND p.seen > ?",
                               (uid, time.time() - GRACE)).fetchone()[0]

    # ---- партии

    def _get(self, gid: str) -> sqlite3.Row:
        r = self.db.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
        if not r:
            raise GameError("Партия не найдена")
        return self._expire(r)

    def _expire(self, g: sqlite3.Row) -> sqlite3.Row:
        """Брошенные партии: приглашение/поиск — сутки, без хода — 3 дня (проигрывает тот, чей ход)."""
        now = time.time()
        if g["status"] == "wait" and now - g["created"] > WAIT_MAX:
            self.db.execute("DELETE FROM games WHERE id = ?", (g["id"],))
            self.db.commit()
            raise GameError("Приглашение устарело")
        if g["status"] == "play" and now - g["updated"] > INACTIVE:
            board = self._board(g)
            loser_white = board.turn == chess.WHITE
            self._finish(g, "0-1" if loser_white else "1-0", "time")
            return self.db.execute("SELECT * FROM games WHERE id = ?", (g["id"],)).fetchone()
        if g["status"] == "play" and g["kind"] != "bot" and g["seen_w"] and g["seen_b"]:
            # один вышел и не вернулся за ABANDON, пока второй был на месте, — вышедший проиграл.
            # Время, когда на месте не было никого, не считается (это делает here())
            gap = g["seen_b"] - g["seen_w"]
            if abs(gap) > ABANDON:
                self._finish(g, "0-1" if gap > 0 else "1-0", "abandon")
                return self.db.execute("SELECT * FROM games WHERE id = ?", (g["id"],)).fetchone()
        return g

    @staticmethod
    def _board(g) -> chess.Board:
        board = chess.Board()
        for uci in (g["moves"] or "").split():
            board.push_uci(uci)
        return board

    def create(self, uid: int, kind: str, level: int = 0) -> str:
        """kind: "invite" — партия по ссылке для друга; "search" — открытый поиск соперника;
        "bot" — с ботом уровня level (цвет — случайно; партия сразу начинается)."""
        if kind != "search":
            self._drop_search(uid)
        if kind == "bot":
            if level not in BOT_LEVELS:
                raise GameError("Непонятный уровень бота")
            gid, now = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y"), time.time()
            white, black = (uid, BOT) if random.random() < 0.5 else (BOT, uid)
            self.db.execute("INSERT INTO games (id, kind, creator, white, black, status, level, created, updated) "
                            "VALUES (?, 'bot', ?, ?, ?, 'play', ?, ?, ?)", (gid, uid, white, black, level, now, now))
            self.db.commit()
            return gid
        if kind == "search":
            # уже ищем — не плодим второй поиск; есть чужой поиск — садимся к нему
            mine = self.db.execute("SELECT id FROM games WHERE status = 'wait' AND kind = 'search' AND creator = ?",
                                   (uid,)).fetchone()
            # садимся только к тем, кто сейчас в мини-приложении, — а не к тому, кто искал час назад и ушёл
            for g in self.db.execute("SELECT g.* FROM games g JOIN players p ON p.id = g.creator "
                                     "WHERE g.status = 'wait' AND g.kind = 'search' AND g.creator != ? AND p.seen > ? "
                                     "ORDER BY g.created", (uid, time.time() - GRACE)).fetchall():
                try:
                    self._expire(g)
                except GameError:
                    continue
                if mine:
                    self.db.execute("DELETE FROM games WHERE id = ?", (mine["id"],))
                return self.join(uid, g["id"])
            if mine:
                return mine["id"]
        gid = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        now = time.time()
        self.db.execute("INSERT INTO games (id, kind, creator, status, created, updated) VALUES (?, ?, ?, 'wait', ?, ?)",
                        (gid, kind, uid, now, now))
        self.db.commit()
        return gid

    def join(self, uid: int, gid: str) -> str:
        g = self._get(gid)
        if g["status"] != "wait":
            if uid in (g["white"], g["black"]):
                return gid  # уже в этой партии — просто откроем её
            raise GameError("В эту партию уже играют")
        if g["creator"] == uid:
            return gid  # своё приглашение — ждём друга
        self._drop_search(uid)
        white, black = (uid, g["creator"]) if random.random() < 0.5 else (g["creator"], uid)
        now = time.time()
        # автор приглашения мог давно закрыть приложение: пока он не откроет партию, уход ему не считаем (0)
        p = self.db.execute("SELECT seen FROM players WHERE id = ?", (g["creator"],)).fetchone()
        seen = {uid: now, g["creator"]: now if p and (p["seen"] or 0) > now - GRACE else 0}
        self.db.execute("UPDATE games SET white = ?, black = ?, status = 'play', updated = ?, seen_w = ?, seen_b = ? "
                        "WHERE id = ?", (white, black, now, seen[white], seen[black], gid))
        self.db.commit()
        return gid

    def _drop_search(self, uid: int) -> None:
        """Начал другую партию — его открытый поиск снимаем: иначе к нему подсядут, пока он играет
        в другой, и через 3 минуты засчитают уход. Внутри join поиск подсевшего тоже снимается."""
        self.db.execute("DELETE FROM games WHERE status = 'wait' AND kind = 'search' AND creator = ?", (uid,))
        self.db.commit()

    def cancel(self, uid: int, gid: str) -> None:
        g = self._get(gid)
        if g["creator"] != uid or g["status"] == "over":
            raise GameError("Эту партию отменить нельзя")
        if g["status"] == "play":
            # экран поиска обновляется раз в 2 с: соперник мог подсесть прямо перед нажатием «Отменить».
            # Пока ходов не было — отмена срабатывает, а подсевший снова ищет соперника (в той же партии)
            if g["moves"] or time.time() - g["updated"] > CANCEL_RACE:
                raise GameError("Соперник уже нашёлся — партия началась")
            if g["kind"] == "search":
                other = g["black"] if g["white"] == uid else g["white"]
                now = time.time()
                self.db.execute("UPDATE games SET creator = ?, white = NULL, black = NULL, status = 'wait', "
                                "draw_offer = 0, seen_w = 0, seen_b = 0, created = ?, updated = ? WHERE id = ?",
                                (other, now, now, gid))
                self.db.commit()
                return
        self.db.execute("DELETE FROM games WHERE id = ?", (gid,))
        self.db.commit()

    def _mine(self, g, uid: int) -> chess.Color:
        if g["status"] != "play":
            raise GameError("Партия уже закончилась" if g["status"] == "over" else "Соперник ещё не пришёл")
        if uid not in (g["white"], g["black"]):
            raise GameError("Вы не играете в этой партии")
        return chess.WHITE if uid == g["white"] else chess.BLACK

    def move(self, uid: int, gid: str, uci: str, _bot=None) -> None:
        g = _bot or self._get(gid)
        color = self._mine(g, uid) if _bot is None else (chess.WHITE if g["white"] == BOT else chess.BLACK)
        board = self._board(g)
        if board.turn != color:
            raise GameError("Сейчас ход соперника")
        try:
            mv = chess.Move.from_uci(uci)
        except ValueError:
            raise GameError("Непонятный ход") from None
        if mv not in board.legal_moves:
            raise GameError("Так ходить нельзя")
        board.push(mv)
        moves = ((g["moves"] or "") + " " + uci).strip()
        self.db.execute("UPDATE games SET moves = ?, draw_offer = 0, updated = ? WHERE id = ?",
                        (moves, time.time(), gid))
        self.db.commit()
        out = board.outcome(claim_draw=True)  # мат, пат, троекратное повторение, мало фигур, 50 ходов
        if out:
            result = "1-0" if out.winner == chess.WHITE else "0-1" if out.winner == chess.BLACK else "1/2-1/2"
            self._finish(self._get(gid), result, out.termination.name.lower())

    def bot_to_move(self, gid: str) -> tuple[str, int] | None:
        """Если в партии с ботом сейчас его ход — (FEN позиции, уровень), иначе None."""
        g = self._get(gid)
        if g["kind"] != "bot" or g["status"] != "play":
            return None
        board = self._board(g)
        bot_white = g["white"] == BOT
        return (board.fen(), g["level"]) if (board.turn == chess.WHITE) == bot_white else None

    def bot_move(self, gid: str, uci: str) -> None:
        g = self._get(gid)
        self.move(BOT, gid, uci, _bot=g)

    def resign(self, uid: int, gid: str) -> None:
        g = self._get(gid)
        color = self._mine(g, uid)
        self._finish(g, "0-1" if color == chess.WHITE else "1-0", "resign")

    def draw(self, uid: int, gid: str, action: str) -> None:
        """action: offer — предложить ничью, accept — принять, decline — отказаться."""
        g = self._get(gid)
        self._mine(g, uid)
        if g["kind"] == "bot":
            raise GameError("Бот на ничью не соглашается — доиграйте или сдайтесь")
        if action == "offer":
            self.db.execute("UPDATE games SET draw_offer = ? WHERE id = ?", (uid, gid))
        elif action == "accept" and g["draw_offer"] and g["draw_offer"] != uid:
            self._finish(g, "1/2-1/2", "agreement")
            return
        elif action == "decline" and g["draw_offer"] and g["draw_offer"] != uid:
            self.db.execute("UPDATE games SET draw_offer = 0 WHERE id = ?", (gid,))
        else:
            raise GameError("Ничью сейчас нельзя")
        self.db.commit()

    def _finish(self, g, result: str, reason: str) -> None:
        if g["status"] != "play":
            return
        self.db.execute("UPDATE games SET status = 'over', result = ?, reason = ?, draw_offer = 0, updated = ? "
                        "WHERE id = ?", (result, reason, time.time(), g["id"]))
        w, b = self.player(g["white"]), self.player(g["black"])
        if w and b and g["kind"] != "bot":  # рейтинг Эло; партии с ботом — не в счёт
            score = {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5)
            exp = 1 / (1 + 10 ** ((b["rating"] - w["rating"]) / 400))
            dw = round(K * (score - exp))
            col = {1.0: ("wins", "losses"), 0.0: ("losses", "wins")}.get(score, ("draws", "draws"))
            self.db.execute(f"UPDATE players SET rating = rating + ?, {col[0]} = {col[0]} + 1 WHERE id = ?", (dw, w["id"]))
            self.db.execute(f"UPDATE players SET rating = rating - ?, {col[1]} = {col[1]} + 1 WHERE id = ?", (dw, b["id"]))
        self.db.commit()

    def here(self, uid: int, gid: str) -> None:
        """Игрок сейчас в мини-приложении (открыта партия или список игр и спрашивает сервер)."""
        try:
            g = self._get(gid)
        except GameError:
            return
        if g["status"] != "play" or uid not in (g["white"], g["black"]):
            return
        now = time.time()
        mine, other = ("seen_w", "seen_b") if uid == g["white"] else ("seen_b", "seen_w")
        upd = {mine: now}
        if g["kind"] != "bot" and g[other] and (not g[mine] or now - g[mine] > GRACE):
            # меня не было: пока меня не было, отсутствие соперника не считается — его отсчёт стоял на паузе
            upd[other] = now if not g[mine] else min(now, g[other] + (now - g[mine]))
        self.db.execute(f"UPDATE games SET {', '.join(f'{k} = ?' for k in upd)} WHERE id = ?", (*upd.values(), gid))
        self.db.commit()

    def view(self, uid: int, gid: str) -> dict:
        g = self._get(gid)
        board = self._board(g)
        you = "white" if uid == g["white"] else "black" if uid == g["black"] else ""
        my_turn = g["status"] == "play" and you and (board.turn == chess.WHITE) == (you == "white")
        moves = (g["moves"] or "").split()
        return {
            "id": g["id"], "kind": g["kind"], "status": g["status"], "result": g["result"], "reason": g["reason"],
            "fen": board.fen(), "moves": moves, "last": moves[-1] if moves else "",
            "turn": "white" if board.turn == chess.WHITE else "black", "check": board.is_check(),
            "you": you, "mine": g["creator"] == uid, "your_turn": bool(my_turn),
            "legal": [m.uci() for m in board.legal_moves] if my_turn else [],
            "white": self._side(g, "white"), "black": self._side(g, "black"), "level": g["level"] or 0,
            "creator": self.player(g["creator"]) if g["status"] == "wait" else None,
            "draw_offer": "you" if g["draw_offer"] == uid else "them" if g["draw_offer"] else "",
            # сколько секунд соперника нет в партии (вышел из мини-приложения) и через сколько ему засчитают поражение
            "opp_away": self._away(g, "black" if you == "white" else "white") if you and g["kind"] != "bot" else 0,
            "abandon": ABANDON,
        }

    @staticmethod
    def _away(g, color: str) -> int:
        seen = g["seen_w" if color == "white" else "seen_b"]
        if g["status"] != "play" or not seen:
            return 0
        return max(0, int(time.time() - seen))

    def _side(self, g, color: str) -> dict | None:
        if g["kind"] == "bot" and g[color] == BOT:
            return {"id": BOT, "bot": True, "name": "🤖 " + BOT_LEVELS.get(g["level"], ("Бот",))[0],
                    "rating": None, "faculty": "", "wins": 0, "losses": 0, "draws": 0}
        return self.player(g[color])

    def my_games(self, uid: int, here: bool = False) -> list[dict]:
        """here — игрок в списке игр мини-приложения: это тоже «на месте», уходом не считается."""
        rows = self.db.execute("SELECT id FROM games WHERE (white = ? OR black = ? OR creator = ?) "
                               "AND status != 'over' ORDER BY updated DESC", (uid, uid, uid)).fetchall()
        out = []
        for r in rows:
            if here:
                self.here(uid, r["id"])
            try:
                out.append(self.view(uid, r["id"]))
            except GameError:
                continue
        return out


# ---- движок для партий с ботом

def stockfish_path() -> str | None:
    """Stockfish из пакета Ubuntu лежит в /usr/games — у служб systemd этой папки нет в PATH."""
    return shutil.which("stockfish") or next((p for p in ("/usr/games/stockfish", "/usr/bin/stockfish")
                                              if os.path.exists(p)), None)


_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def simple_move(board: chess.Board, level: int) -> chess.Move:
    """Запасной ход без Stockfish: смотрит на 2 полухода вперёд по материалу (и мат), с долей случайности."""
    moves = list(board.legal_moves)
    rnd = BOT_LEVELS.get(level, BOT_LEVELS[1])[3]
    if random.random() < rnd:
        return random.choice(moves)

    def material(b: chess.Board, me: chess.Color) -> float:
        if b.is_checkmate():
            return -1000 if b.turn == me else 1000
        return sum(_VALUES[p.piece_type] * (1 if p.color == me else -1) for p in b.piece_map().values())

    me, best, best_score = board.turn, [], None
    for mv in moves:
        board.push(mv)
        replies = list(board.legal_moves)
        if not replies:
            score = material(board, me)
        else:
            score = None
            for r in replies:
                board.push(r)
                v = material(board, me)
                board.pop()
                score = v if score is None else min(score, v)
        board.pop()
        if best_score is None or score > best_score:
            best, best_score = [mv], score
        elif score == best_score:
            best.append(mv)
    return random.choice(best)


class Engine:
    """Один Stockfish на весь сервер (ходы бота — по очереди); нет Stockfish — simple_move."""

    def __init__(self):
        self.engine = None
        self.lock = asyncio.Lock()

    def _think(self, fen: str, level: int) -> str:
        board = chess.Board(fen)
        name, skill, think, rnd = BOT_LEVELS.get(level, BOT_LEVELS[1])
        path = stockfish_path()
        if path is None:
            return simple_move(board, level).uci()
        if random.random() < rnd:  # новичок иногда ходит «как попало» — чтобы его можно было обыграть
            return random.choice(list(board.legal_moves)).uci()
        try:
            if self.engine is None:
                self.engine = chess.engine.SimpleEngine.popen_uci(path)
            self.engine.configure({"Skill Level": skill})
            return self.engine.play(board, chess.engine.Limit(time=think)).move.uci()
        except (chess.engine.EngineError, OSError):  # движок упал — перезапустим в следующий раз
            log.exception("stockfish")
            try:
                self.engine and self.engine.quit()
            except Exception:  # noqa: BLE001
                pass
            self.engine = None
            return simple_move(board, level).uci()

    async def move(self, fen: str, level: int) -> str:
        async with self.lock:
            return await asyncio.to_thread(self._think, fen, level)


async def bot_reply(store: Store, engine: Engine, gid: str) -> None:
    """Если теперь ход бота — бот ходит."""
    todo = store.bot_to_move(gid)
    if todo:
        store.bot_move(gid, await engine.move(*todo))


# ---- HTTP

def make_app(store: Store, token: str, engine: Engine | None = None) -> web.Application:
    engine = engine or Engine()
    @web.middleware
    async def cors(request, handler):
        if request.method == "OPTIONS":
            resp = web.Response()
        else:
            try:
                resp = await handler(request)
            except NotSigned:  # и у отказов — заголовки CORS, иначе браузер не покажет причину
                resp = web.json_response({"error": "Откройте игру из Telegram"}, status=401)
            except GameError as e:
                resp = web.json_response({"error": str(e)}, status=400)
        origin = request.headers.get("Origin", "")
        if origin in ORIGINS:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Vary"] = "Origin"
        return resp

    def who(request) -> int:
        user = check_init_data(request.headers.get("X-Init-Data", ""), token)
        if not user:
            raise NotSigned
        store.touch(user)
        return user["id"]

    async def body(request) -> dict:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    async def ping(request):
        return web.json_response({"ok": True})

    async def me(request):
        uid = who(request)
        return web.json_response({"me": store.player(uid), "games": store.my_games(uid, here=True),
                                  "searching": store.searching(uid)})

    async def top(request):
        who(request)
        return web.json_response({"top": store.top()})

    async def new(request):
        uid = who(request)
        d = await body(request)
        kind = d.get("kind")
        if kind not in ("invite", "search", "bot"):
            raise GameError("Непонятный вид партии")
        try:
            level = int(d.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        gid = store.create(uid, kind, level)
        if kind == "bot":
            await bot_reply(store, engine, gid)  # бот играет белыми — сразу его первый ход
        return web.json_response(store.view(uid, gid))

    async def game(request):
        uid, gid = who(request), request.query.get("id", "")
        store.here(uid, gid)
        return web.json_response(store.view(uid, gid))

    async def act(request):
        uid = who(request)
        d = await body(request)
        gid, action = str(d.get("id", "")), d.get("action")
        store.here(uid, gid)
        if action == "join":
            gid = store.join(uid, gid)
        elif action == "cancel":
            store.cancel(uid, gid)
            return web.json_response({"ok": True})
        elif action == "move":
            store.move(uid, gid, str(d.get("uci", "")))
            await bot_reply(store, engine, gid)  # в партии с ботом — сразу его ответ
        elif action == "resign":
            store.resign(uid, gid)
        elif action in ("offer", "accept", "decline"):
            store.draw(uid, gid, action)
        else:
            raise GameError("Непонятное действие")
        return web.json_response(store.view(uid, gid))

    app = web.Application(middlewares=[cors], client_max_size=16 * 1024)
    app.router.add_get("/api/ping", ping)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/top", top)
    app.router.add_get("/api/game", game)
    app.router.add_post("/api/new", new)
    app.router.add_post("/api/act", act)
    app.router.add_route("OPTIONS", "/api/{tail:.*}", ping)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    token = bot_token()
    if not token:
        raise SystemExit("Нет токена бота (token.txt рядом или BOT_TOKEN) — без него не проверить игроков")
    web.run_app(make_app(Store(), token), host="127.0.0.1", port=int(os.environ.get("GAMES_PORT", "8090")),
                print=None)


if __name__ == "__main__":
    main()
