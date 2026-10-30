"""
RootCamp — CTF-платформа для тренировки: категории, задания, флаги,
«первая кровь», рейтинг и админка. Плюс встроенные учебные веб-лабы (/lab/...).
"""

import base64
import codecs
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
import zlib
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, make_response, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv("DATA_DIR", BASE / "data"))
FILES = DATA / "files"
DB_PATH = DATA / "ctf.db"

DATA.mkdir(parents=True, exist_ok=True)
FILES.mkdir(exist_ok=True)
_secret_path = DATA / "secret.key"
if not _secret_path.exists():
    _secret_path.write_bytes(secrets.token_bytes(32))
SECRET = _secret_path.read_bytes()

app = Flask(__name__)
app.secret_key = SECRET
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  MAX_CONTENT_LENGTH=32 * 1024 * 1024)

DIFFICULTIES = {"easy": "Легко", "medium": "Средне", "hard": "Сложно"}


# ---------------------------------------------------------------- db

def db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY, slug TEXT UNIQUE, name TEXT, icon TEXT, description TEXT, ord INTEGER
);
CREATE TABLE IF NOT EXISTS challenges (
    id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    title TEXT NOT NULL, difficulty TEXT NOT NULL, points INTEGER NOT NULL,
    description TEXT NOT NULL, address TEXT DEFAULT '', attachment TEXT DEFAULT '',
    flag_hash TEXT NOT NULL, visible INTEGER DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS solves (
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    challenge_id INTEGER REFERENCES challenges(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL, PRIMARY KEY (user_id, challenge_id)
);
"""

CATEGORIES = [
    ("web", "Веб", "web", "Уязвимости веб-приложений: инъекции, обходы авторизации, логика."),
    ("crypto", "Криптография", "key", "Шифры, кодировки, слабые хэши и ошибки реализации."),
    ("stego", "Стеганография", "image", "Данные, спрятанные в картинках и файлах."),
    ("forensics", "Форензика", "search", "Логи, дампы и следы атак."),
    ("reverse", "Реверс", "cpu", "Разбор чужих программ и обход проверок."),
    ("machines", "Машины", "server", "Полноценные цели для пентеста по IP-адресу."),
]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def flag_hash(flag: str) -> str:
    return hashlib.sha256(flag.strip().encode()).hexdigest()


def dyn(slug: str, n: int = 10) -> str:
    """Секрет, уникальный для этой инсталляции: флаги не лежат в исходниках."""
    return hmac.new(SECRET, slug.encode(), "sha256").hexdigest()[:n]


def dyn_flag(slug: str) -> str:
    return f"FLAG{{{slug.replace('-', '_')}_{dyn(slug)}}}"


# ---------------------------------------------------------------- seed challenges

def make_png(path: Path, comment: str) -> None:
    w = h = 96
    raw = b"".join(b"\x00" + b"".join(bytes((x, 110 + y, 70)) for x in range(w)) for y in range(h))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"tEXt", b"Comment\x00" + comment.encode())
           + chunk(b"IDAT", zlib.compress(raw))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def seed_challenges() -> list[dict]:
    out = []

    def add(slug, cat, title, diff, pts, desc, flag, address="", attachment=""):
        out.append(dict(slug=slug, cat=cat, title=title, difficulty=diff, points=pts,
                        description=desc, flag=flag, address=address, attachment=attachment))

    # --- crypto
    f = dyn_flag("base-camp")
    add("base-camp", "crypto", "Базовый лагерь", "easy", 50,
        "Перехватили странную строку. Похоже, её даже не шифровали.\n\n"
        + base64.b64encode(f.encode()).decode(), f)

    f = dyn_flag("caesar-salad")
    add("caesar-salad", "crypto", "Салат Цезаря", "easy", 75,
        "Римляне сдвигали буквы. Насколько далеко сдвинули тут?\n\n" + codecs.encode(f, "rot13"), f)

    f = dyn_flag("one-byte")
    key = int(dyn("one-byte-key", 2), 16) or 0x42
    add("one-byte", "crypto", "Один байт", "medium", 150,
        "Флаг зашифровали XOR с ключом длиной ровно один байт. Вот hex:\n\n"
        + bytes(b ^ key for b in f.encode()).hex() + "\n\nПодсказка: ты знаешь, с чего начинается флаг.", f)

    pin = int(dyn("weak-hash-pin", 8), 16) % 10000
    f = f"FLAG{{{pin:04d}}}"
    add("weak-hash", "crypto", "Слабый хэш", "medium", 150,
        "Сотрудник защитил панель четырёхзначным PIN-кодом и хранит его MD5:\n\n"
        + hashlib.md5(f"{pin:04d}".encode()).hexdigest() + "\n\nФлаг: FLAG{PIN}, например FLAG{0000}.", f)

    # --- web (лабы встроены в платформу, см. /lab/...)
    add("view-source", "web", "Смотри глубже", "easy", 50,
        "Разработчик клянётся, что на странице нет ничего секретного.", dyn_flag("view-source"),
        address="/lab/source")
    add("robots", "web", "Восстание машин", "easy", 75,
        "Поисковым роботам сюда нельзя. А тебе?", dyn_flag("robots"), address="/")
    add("cookie-monster", "web", "Печенька", "medium", 150,
        "Админка доверяет тому, что ты о себе рассказываешь.", dyn_flag("cookie-monster"),
        address="/lab/cookie")
    add("localhost-only", "web", "Только для своих", "medium", 200,
        "Внутренний сервис открывается только с localhost. Или сервису так кажется.",
        dyn_flag("localhost-only"), address="/lab/internal")

    # --- stego
    f = dyn_flag("pixel-whisper")
    make_png(FILES / "postcard.png", base64.b64encode(f.encode()).decode())
    add("pixel-whisper", "stego", "Открытка", "medium", 150,
        "Обычная зелёная открытка. Или не обычная? Посмотри не только на пиксели.", f,
        attachment="postcard.png")

    # --- forensics
    h = int(dyn("brute-ip", 8), 16)
    ip = f"185.{h % 200 + 20}.{(h >> 8) % 250}.{(h >> 16) % 250 + 1}"
    rng = __import__("random").Random(h)
    lines = []
    for i in range(1500):
        t = f"Oct 30 0{rng.randint(0, 9)}:{rng.randint(10, 59)}:{rng.randint(10, 59)}"
        if rng.random() < 0.08:
            lines.append(f"{t} srv sshd[{1000 + i}]: Failed password for root from {ip} port {rng.randint(30000, 65000)} ssh2")
        else:
            other = f"10.0.{rng.randint(0, 5)}.{rng.randint(2, 250)}"
            msg = rng.choice([f"Accepted publickey for deploy from {other}",
                              f"Failed password for invalid user test from {other}",
                              f"Disconnected from {other}"])
            lines.append(f"{t} srv sshd[{1000 + i}]: {msg} port {rng.randint(30000, 65000)} ssh2")
    (FILES / "auth.log").write_text("\n".join(lines) + "\n")
    add("ssh-noise", "forensics", "Шум в логах", "easy", 100,
        "Кто-то настойчиво подбирал пароль к root. Найди его IP.\nФлаг: FLAG{IP}, например FLAG{1.2.3.4}.",
        f"FLAG{{{ip}}}", attachment="auth.log")

    # --- reverse
    f = dyn_flag("crackme")
    enc = [ord(c) ^ ((i * 7 + 13) & 0xFF) for i, c in enumerate(f)]
    (FILES / "crackme.py").write_text(
        "import sys\n\n"
        f"DATA = {enc}\n\n\n"
        "def check(p):\n"
        "    return len(p) == len(DATA) and all(\n"
        "        (ord(c) ^ ((i * 7 + 13) & 0xFF)) == d for i, (c, d) in enumerate(zip(p, DATA)))\n\n\n"
        "print('Доступ разрешён' if check(input('Пароль: ')) else 'Неверно')\n")
    add("crackme", "reverse", "Крякми", "medium", 200,
        "Программа знает пароль, но не говорит. Заставь её.", f, attachment="crackme.py")
    return out


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    for i, (slug, name, icon, desc) in enumerate(CATEGORIES):
        conn.execute("INSERT OR IGNORE INTO categories (slug, name, icon, description, ord) VALUES (?, ?, ?, ?, ?)",
                     (slug, name, icon, desc, i))
    if conn.execute("SELECT COUNT(*) FROM challenges").fetchone()[0] == 0:
        cats = {r[0]: r[1] for r in conn.execute("SELECT slug, id FROM categories")}
        for c in seed_challenges():
            conn.execute(
                "INSERT INTO challenges (slug, category_id, title, difficulty, points, description, address,"
                " attachment, flag_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c["slug"], cats[c["cat"]], c["title"], c["difficulty"], c["points"], c["description"],
                 c["address"], c["attachment"], flag_hash(c["flag"]), now()))
    admin_pw = os.getenv("ADMIN_PASSWORD")
    if admin_pw and conn.execute("SELECT 1 FROM users WHERE username = 'admin'").fetchone() is None:
        conn.execute("INSERT INTO users (username, pw_hash, is_admin, created_at) VALUES ('admin', ?, 1, ?)",
                     (generate_password_hash(admin_pw), now()))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- auth & csrf

@app.before_request
def load_user():
    g.user = None
    if "uid" in session:
        g.user = db().execute("SELECT * FROM users WHERE id = ?", (session["uid"],)).fetchone()
    if request.method == "POST" and not request.path.startswith("/lab/"):
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "")):
            abort(400)


@app.template_filter("plural")
def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "user": g.user, "DIFF": DIFFICULTIES}


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if g.user is None or not g.user["is_admin"]:
            abort(403)
        return fn(*a, **kw)
    return wrapper


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if not (3 <= len(name) <= 24) or not name.replace("_", "").isalnum():
            flash("Ник: 3–24 символа, буквы, цифры и _", "error")
        elif len(pw) < 8:
            flash("Пароль минимум 8 символов", "error")
        else:
            try:
                cur = db().execute("INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
                                   (name, generate_password_hash(pw), now()))
                db().commit()
                session.clear()
                session["uid"] = cur.lastrowid
                return redirect(url_for("index"))
            except sqlite3.IntegrityError:
                flash("Такой ник уже занят", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        row = db().execute("SELECT * FROM users WHERE username = ?",
                           (request.form.get("username", "").strip(),)).fetchone()
        if row and check_password_hash(row["pw_hash"], request.form.get("password", "")):
            session.clear()
            session["uid"] = row["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
        flash("Неверный ник или пароль", "error")
    return render_template("auth.html", mode="login")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------- pages

def user_solved_ids() -> set:
    if g.user is None:
        return set()
    return {r[0] for r in db().execute("SELECT challenge_id FROM solves WHERE user_id = ?", (g.user["id"],))}


SCORE_SQL = """
SELECT u.id, u.username, COALESCE(SUM(c.points), 0) AS score, COUNT(s.challenge_id) AS solved,
       MAX(s.created_at) AS last
FROM users u LEFT JOIN solves s ON s.user_id = u.id LEFT JOIN challenges c ON c.id = s.challenge_id
WHERE u.is_admin = 0 GROUP BY u.id ORDER BY score DESC, last ASC
"""


@app.route("/")
def index():
    cats = db().execute("""
        SELECT cat.*, COUNT(c.id) AS total FROM categories cat
        LEFT JOIN challenges c ON c.category_id = cat.id AND c.visible = 1
        GROUP BY cat.id ORDER BY cat.ord""").fetchall()
    recent = db().execute("""
        SELECT u.username, c.title, c.slug, s.created_at FROM solves s
        JOIN users u ON u.id = s.user_id JOIN challenges c ON c.id = s.challenge_id
        ORDER BY s.created_at DESC LIMIT 8""").fetchall()
    stats = {
        "challenges": db().execute("SELECT COUNT(*) FROM challenges WHERE visible = 1").fetchone()[0],
        "users": db().execute("SELECT COUNT(*) FROM users WHERE is_admin = 0").fetchone()[0],
        "solves": db().execute("SELECT COUNT(*) FROM solves").fetchone()[0],
    }
    top = db().execute(SCORE_SQL + " LIMIT 5").fetchall()
    return render_template("index.html", cats=cats, recent=recent, stats=stats, top=top)


@app.route("/categories")
def categories():
    return redirect(url_for("index") + "#categories")


@app.route("/categories/<slug>")
def category(slug):
    cat = db().execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone() or abort(404)
    chals = db().execute("""
        SELECT c.*, (SELECT COUNT(*) FROM solves s WHERE s.challenge_id = c.id) AS solves
        FROM challenges c WHERE c.category_id = ? AND c.visible = 1 ORDER BY c.points, c.id""",
                         (cat["id"],)).fetchall()
    return render_template("category.html", cat=cat, chals=chals, solved=user_solved_ids())


def get_challenge(slug):
    row = db().execute("""SELECT c.*, cat.slug AS cat_slug, cat.name AS cat_name, cat.icon AS icon
                          FROM challenges c JOIN categories cat ON cat.id = c.category_id
                          WHERE c.slug = ?""", (slug,)).fetchone()
    if row is None or (not row["visible"] and not (g.user and g.user["is_admin"])):
        abort(404)
    return row


ATTEMPTS: dict[int, deque] = defaultdict(deque)


@app.route("/challenges/<slug>", methods=["GET", "POST"])
def challenge(slug):
    ch = get_challenge(slug)
    if request.method == "POST":
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        q = ATTEMPTS[g.user["id"]]
        while q and q[0] < time.time() - 60:
            q.popleft()
        if len(q) >= 10:
            flash("Слишком много попыток. Подожди минуту.", "error")
        else:
            q.append(time.time())
            if hmac.compare_digest(flag_hash(request.form.get("flag", "")), ch["flag_hash"]):
                db().execute("INSERT OR IGNORE INTO solves VALUES (?, ?, ?)", (g.user["id"], ch["id"], now()))
                db().commit()
                flash(f"Флаг принят! +{ch['points']} очков", "ok")
            else:
                flash("Неверный флаг", "error")
        return redirect(url_for("challenge", slug=slug))

    solvers = db().execute("""SELECT u.username, s.created_at FROM solves s JOIN users u ON u.id = s.user_id
                              WHERE s.challenge_id = ? ORDER BY s.created_at""", (ch["id"],)).fetchall()
    address = ch["address"]
    if address.startswith("/"):
        address = request.host_url.rstrip("/") + address
    return render_template("challenge.html", ch=ch, solvers=solvers, address=address,
                           solved=ch["id"] in user_solved_ids())


@app.route("/files/<path:name>")
def files(name):
    return send_from_directory(FILES, name, as_attachment=True)


@app.route("/scoreboard")
def scoreboard():
    return render_template("scoreboard.html", rows=db().execute(SCORE_SQL).fetchall())


@app.route("/users/<username>")
def profile(username):
    u = db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone() or abort(404)
    solves = db().execute("""
        SELECT c.title, c.slug, c.points, cat.name AS cat, s.created_at,
               s.created_at = (SELECT MIN(created_at) FROM solves WHERE challenge_id = c.id) AS first
        FROM solves s JOIN challenges c ON c.id = s.challenge_id JOIN categories cat ON cat.id = c.category_id
        WHERE s.user_id = ? ORDER BY s.created_at DESC""", (u["id"],)).fetchall()
    rank = next((i + 1 for i, r in enumerate(db().execute(SCORE_SQL)) if r["id"] == u["id"]), None)
    return render_template("profile.html", u=u, solves=solves, rank=rank,
                           score=sum(s["points"] for s in solves))


# ---------------------------------------------------------------- admin

@app.route("/admin")
@admin_required
def admin():
    chals = db().execute("""SELECT c.*, cat.name AS cat_name,
                            (SELECT COUNT(*) FROM solves s WHERE s.challenge_id = c.id) AS solves
                            FROM challenges c JOIN categories cat ON cat.id = c.category_id
                            ORDER BY cat.ord, c.points""").fetchall()
    return render_template("admin.html", chals=chals)


@app.route("/admin/challenge", methods=["GET", "POST"])
@app.route("/admin/challenge/<int:cid>", methods=["GET", "POST"])
@admin_required
def admin_edit(cid=None):
    ch = db().execute("SELECT * FROM challenges WHERE id = ?", (cid,)).fetchone() if cid else None
    if cid and ch is None:
        abort(404)
    cats = db().execute("SELECT * FROM categories ORDER BY ord").fetchall()
    if request.method == "POST":
        f = request.form
        if f.get("delete") and ch:
            db().execute("DELETE FROM challenges WHERE id = ?", (cid,))
            db().commit()
            return redirect(url_for("admin"))
        attachment = ch["attachment"] if ch else ""
        upload = request.files.get("attachment")
        if upload and upload.filename:
            attachment = f"{secrets.token_hex(4)}_{Path(upload.filename).name}"
            upload.save(FILES / attachment)
        fields = (f["slug"].strip(), int(f["category_id"]), f["title"].strip(), f["difficulty"],
                  int(f["points"]), f["description"], f.get("address", "").strip(), attachment,
                  1 if f.get("visible") else 0)
        new_flag = f.get("flag", "").strip()
        try:
            if ch:
                db().execute("""UPDATE challenges SET slug=?, category_id=?, title=?, difficulty=?, points=?,
                                description=?, address=?, attachment=?, visible=? WHERE id=?""", (*fields, cid))
                if new_flag:
                    db().execute("UPDATE challenges SET flag_hash = ? WHERE id = ?", (flag_hash(new_flag), cid))
            else:
                if not new_flag:
                    flash("Укажи флаг", "error")
                    return render_template("admin_edit.html", ch=f, cats=cats)
                db().execute("""INSERT INTO challenges (slug, category_id, title, difficulty, points, description,
                                address, attachment, visible, flag_hash, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (*fields, flag_hash(new_flag), now()))
            db().commit()
            flash("Сохранено", "ok")
            return redirect(url_for("admin"))
        except sqlite3.IntegrityError:
            flash("Такой slug уже есть", "error")
    return render_template("admin_edit.html", ch=ch, cats=cats)


# ---------------------------------------------------------------- built-in vulnerable labs

@app.route("/robots.txt")
def robots():
    return app.response_class("User-agent: *\nDisallow: /lab/maintenance-panel-7f3a\n", mimetype="text/plain")


@app.route("/lab/maintenance-panel-7f3a")
def lab_robots():
    return render_template("lab.html", title="Панель обслуживания",
                           body=f"Ты нашёл скрытую панель. Флаг: {dyn_flag('robots')}")


@app.route("/lab/source")
def lab_source():
    return render_template("lab.html", title="Ничего интересного",
                           body="Здесь правда ничего нет. Совсем. Иди дальше.",
                           comment=f"TODO: убрать перед релизом {dyn_flag('view-source')}")


@app.route("/lab/cookie")
def lab_cookie():
    raw = request.cookies.get("profile")
    try:
        profile = json.loads(base64.b64decode(raw)) if raw else None
    except ValueError:
        profile = None
    if not isinstance(profile, dict):
        profile = {"user": "guest", "is_admin": False}
    if profile.get("is_admin") is True:
        body = f"Привет, администратор {profile.get('user')}! Флаг: {dyn_flag('cookie-monster')}"
    else:
        body = f"Привет, {profile.get('user')}. Админка доступна только администраторам."
    resp = make_response(render_template("lab.html", title="Админка", body=body))
    if raw is None:
        resp.set_cookie("profile", base64.b64encode(json.dumps(profile).encode()).decode(), path="/lab/")
    return resp


@app.route("/lab/internal")
def lab_internal():
    # «Прокси» передаёт настоящий IP клиента в X-Forwarded-For — и сервис ему слепо верит.
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded == "127.0.0.1":
        body = f"Добро пожаловать во внутренний сервис. Флаг: {dyn_flag('localhost-only')}"
    else:
        body = f"Доступ разрешён только с 127.0.0.1. Твой адрес (по данным прокси): {forwarded or 'неизвестен'}"
    return render_template("lab.html", title="Внутренний сервис", body=body)


@app.errorhandler(403)
@app.errorhandler(404)
def errors(e):
    return render_template("error.html", code=e.code), e.code


init_db()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 8000)))
