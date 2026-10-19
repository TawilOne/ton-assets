"""Ноутбук → сервер: скачивает расписание групп факультетов (bot.FACULTIES) и отправляет его боту на сервер.

Что скачивается (чем меньше запросов, тем реже сайт просит капчу):
  1) группы, которые выбрали пользователи бота, — текущая и 2 следующие недели, каждый раз;
  2) все остальные группы факультетов — одна неделя (пн–чт текущая, пт–вс следующая), раз в сутки
     (чтобы новичок сразу увидел расписание своей группы);
  3) выбранные группы — остальные недели семестра: будущие раз в 3 дня, прошедшие — если их нет.
По расписанию — не дольше 11 минут за запуск (что не успели, докачается в следующий раз);
вручную (sync_now.bat) — без ограничения по времени, пока не скачает весь план.

Сайт Губкина не пускает серверы хостингов, а домашний интернет пускает. Поэтому расписание
скачивает ноутбук, а бот на сервере (GUBKIN_OFFLINE=1) отвечает по присланному файлу.

    python sync.py --setup         один раз: ключ для входа на сервер, настройка сервера, автозапуск
    python sync.py --interactive   вручную (sync_now.bat): с выводом и вводом капчи
    pythonw sync.py                тихо, по расписанию Windows (так запускает автозапуск)

Адрес сервера — в файле server.txt (по умолчанию root@153.76.167.68).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

import requests  # noqa: E402

import bot  # noqa: E402  — та же группа, cache.json и cookies.json, что у бота на ноутбуке
from gubkin_api import CaptchaRequired, GubkinError, normalize_code, write_atomic  # noqa: E402
import guard  # noqa: E402
from schedule import parse_week, semester_bounds, slim_week  # noqa: E402

DEFAULT_SERVER = "root@153.76.167.68"
REMOTE = "/opt/gubkin-bot/gubkin_schedule_bot"
BRANCH = "claude/rgu-gubkina-schedule-bot-snqhke"
TASK = "GubkinSync"
LOG = HERE / "sync.log"
STATUS = HERE / "sync_status.json"
FAILED = HERE / "sync_failed.json"  # недели, на которые сайт ответил ошибкой: {"понедельник|id группы": когда}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # ssh из pythonw не должен мигать чёрным окном
# свой ключ без пароля только для sync — чтобы не зависеть от других ключей на ноутбуке
KEY = pathlib.Path.home() / ".ssh" / "gubkin_sync_ed25519"


def say(msg: str) -> None:
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    if sys.stdout:  # у pythonw нет консоли
        try:
            print(line)
        except OSError:
            pass
    try:
        old = LOG.read_text(encoding="utf-8").splitlines()[-300:] if LOG.exists() else []
        LOG.write_text("\n".join(old + [line]) + "\n", encoding="utf-8")
    except OSError:
        pass


def server() -> str:
    f = HERE / "server.txt"
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip()
    f.write_text(DEFAULT_SERVER, encoding="utf-8")
    return DEFAULT_SERVER


def ssh(args: list[str], data: bytes | None = None, batch: bool = True, timeout: int = 90):
    opts = ["-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "Compression=yes",
            "-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=accept-new"]
    if batch:
        opts += ["-o", "BatchMode=yes"]  # без вопросов: только по ключу
    # скрытое окно — только для тихой работы: иначе запрос пароля уйдёт в невидимое окно
    # когда спрашиваем пароль — ждём сколько угодно: человек может отвлечься
    return subprocess.run(["ssh", *opts, *args], input=data, capture_output=batch,
                          timeout=timeout if batch else None,
                          creationflags=NO_WINDOW if batch else 0)


def put(srv: str, name: str, data: bytes) -> None:
    """Кладёт файл рядом с ботом на сервере (атомарно: сначала .tmp, потом переименование)."""
    path = f"{REMOTE}/{name}"
    r = ssh([srv, f"cat > {path}.tmp && chown gubkin: {path}.tmp && mv {path}.tmp {path}"], data)
    if r.returncode:
        raise RuntimeError((r.stderr or b"").decode(errors="replace").strip() or "ошибка ssh")


# ---- скачивание

def mondays(today: dt.date) -> list[dt.date]:
    start, end = semester_bounds(today)
    end = max(end, today + dt.timedelta(days=28))  # на стыке семестров — ещё 4 недели вперёд
    mon = start - dt.timedelta(days=start.weekday())
    out = []
    while mon <= end:
        out.append(mon)
        mon += dt.timedelta(weeks=1)
    return out


# капча — русские буквы и цифры; набранное в английской раскладке переводим в русскую (wtvtlr -> цемедк)
_EN_TO_RU = str.maketrans("qwertyuiop[]asdfghjkl;'zxcvbnm,.`QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>~",
                          "йцукенгшщзхъфывапролджэячсмитьбюёЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮЁ")


def captcha_text(typed: str) -> str:
    return "".join(typed.split()).translate(_EN_TO_RU)


def solve_captcha() -> None:
    """Показывает капчу и просит ввести код. После неверного кода сайт меняет капчу —
    поэтому каждая попытка с новой картинкой."""
    for attempt in range(3):
        # у каждой попытки свой файл: просмотрщик Windows может показывать старую картинку из того же файла
        pic = HERE / ("captcha.png" if attempt == 0 else f"captcha{attempt + 1}.png")
        pic.write_bytes(bot.client.captcha_image())
        if os.name == "nt":
            os.startfile(pic)  # откроется в просмотрщике картинок
        else:
            print(f"Картинка: {pic}")
        code = captcha_text(input("Введите символы с картинки (русские буквы и цифры): "))
        if bot.client.validate_captcha(code):
            print("✅ Капча принята")
            return
        if attempt < 2:
            print("Не подошло — открываю новую картинку, введите код с неё.")
    raise CaptchaRequired("капча не пройдена")


STOP_AFTER = 10  # столько неудач подряд — значит, связи с сайтом нет: останавливаемся, а не перебираем всё


def _short(e: Exception) -> str:
    """Понятная короткая причина вместо длинного текста ошибки requests."""
    if isinstance(e, requests.exceptions.ProxyError):
        return "не удалось подключиться через прокси/VPN"
    if isinstance(e, requests.exceptions.Timeout):
        return "сайт не ответил вовремя"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "нет связи с сайтом"
    return str(e)[:150]


NO_LINK = {"не удалось подключиться через прокси/VPN", "сайт не ответил вовремя", "нет связи с сайтом",
           "сайт не ответил"}

DAY = 20 * 3600  # «раз в сутки»: запуски идут каждые 3 часа, 20 часов — с запасом


def _with_captcha(interactive: bool, func, *args):
    """Вызывает func; если сайт просит капчу — вручную показываем её, по расписанию — сдаёмся."""
    while True:
        try:
            return func(*args)
        except CaptchaRequired:
            if not interactive:
                raise
            solve_captcha()


def _group_rows(rows) -> list[dict]:
    return [{"id": str(g["id"]), "code": normalize_code(str(g.get("code") or g.get("name") or ""))}
            for g in rows or [] if isinstance(g, dict) and g.get("id") and (g.get("code") or g.get("name"))]


def _fetch_faculties(interactive: bool) -> list[dict]:
    """Факультеты из bot.FACULTIES и их группы с сайта: 1 запрос на список факультетов + по одному
    на каждый факультет. Не найденный по названию факультет пропускаем (пишем в журнал)."""
    site = _with_captcha(interactive, bot.client.faculties)
    name = lambda f: str(f.get("name") or f.get("title") or "")
    out = []
    for cfg in bot.FACULTIES:
        words = [cfg["hint"]] if isinstance(cfg["hint"], str) else cfg["hint"]
        matches = [f for f in site if all(w in name(f).lower() for w in words)]
        # «Факультет …» раньше похожих названий (институты, кафедры)
        matches.sort(key=lambda f: not name(f).lower().startswith("факультет"))
        chosen = None
        for f in matches:
            groups = _group_rows(_with_captcha(interactive, bot.client.groups, f["id"]))
            # у факультета группы по умолчанию проверяем, что это он и есть
            if groups and (cfg["key"] != bot.DEFAULT_FACULTY or any(g["code"] == bot.GROUP_CODE for g in groups)):
                chosen = {"key": cfg["key"], "id": f["id"], "name": name(f), "groups": groups}
                break
        if chosen is None and cfg["key"] == bot.DEFAULT_FACULTY:  # не нашли по названию — ищем по группе
            f, rows, _ = _with_captcha(interactive, bot.client.find_faculty, bot.GROUP_CODE, bot.FACULTY_HINT)
            chosen = {"key": cfg["key"], "id": f["id"], "name": name(f), "groups": _group_rows(rows)}
        if chosen is None:
            say(f"не найден факультет «{' + '.join(words)}». Факультеты на сайте: " + "; ".join(name(f) for f in site))
            continue
        out.append(chosen)
        say(f"{chosen['name']}: групп {len(chosen['groups'])}")
    return out


def refresh_groups(interactive: bool) -> list[dict]:
    """Списки групп факультетов (groups.json): с сайта раз в сутки — это 2–4 запроса."""
    try:
        data = json.loads(bot.GROUPS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    fresh = time.time() - data.get("time", 0) < DAY
    # искали ли уже все факультеты из bot.FACULTIES (в старом формате поля нет; добавили факультет — ищем сразу)
    tried = set(data.get("tried") or [])
    if not fresh or not {f["key"] for f in bot.FACULTIES} <= tried:
        try:
            facs = _fetch_faculties(interactive)
            if facs:
                data = {"faculties": facs, "time": time.time(), "tried": [f["key"] for f in bot.FACULTIES]}
                write_atomic(bot.GROUPS_FILE, json.dumps(data, ensure_ascii=False))
        except CaptchaRequired:
            raise
        except (GubkinError, OSError, ValueError, KeyError, TypeError) as e:
            say(f"не удалось обновить список групп: {_short(e)}")  # работаем со старым списком
    return bot._faculty_groups()


def wanted_codes(srv: str) -> set[str]:
    """Группы, которые выбрали пользователи бота (файл user_groups.json на сервере)."""
    codes = {bot.GROUP_CODE}
    sources = []
    try:
        r = ssh([srv, f"cat {REMOTE}/user_groups.json 2>/dev/null || echo '{{}}'"])
        if r.returncode == 0:
            sources.append(r.stdout.decode("utf-8", errors="replace"))
        else:
            say("не удалось узнать с сервера, какие группы выбраны, — обновляю как обычные")
    except (OSError, subprocess.SubprocessError) as e:
        say(f"не удалось узнать с сервера, какие группы выбраны: {e}")
    if bot.USER_GROUPS_FILE.exists():  # если бот запускали и на ноутбуке
        sources.append(bot.USER_GROUPS_FILE.read_text(encoding="utf-8"))
    for text in sources:
        try:
            codes |= {normalize_code(str(c)) for c in json.loads(text or "{}").values() if c}
        except (ValueError, AttributeError):
            pass
    return codes


def _age(mon: dt.date, gid) -> float | None:
    hit = bot.client._cache.get((mon, str(gid)))
    return None if hit is None else time.time() - hit[0]


def _load_failed() -> dict[str, float]:
    try:
        data = json.loads(FAILED.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if time.time() - v < 8 * 86400} if isinstance(data, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _save_failed(data: dict) -> None:
    try:
        write_atomic(FAILED, json.dumps(data))
    except OSError:
        pass


FAR_AGE = 3 * 86400 - 4 * 3600  # дальние недели выбранных групп — раз в 3 дня (почти не меняются)


def plan(groups: list[dict], wanted: set[str], weeks: list[dt.date], this_monday: dt.date,
         others_limit: int | None = None, failed: dict | None = None,
         today: dt.date | None = None) -> list[tuple]:
    """Что скачать в этот раз, по порядку важности: [(понедельник, группа, выбрана ли)].
    others_limit — сколько недель невыбранных групп брать за раз (по расписанию — порциями,
    чтобы не делать сотню запросов подряд и не нарваться на капчу).
    failed — недели, на которые сайт недавно ответил ошибкой (например, летние — 500):
    прошедшие не спрашиваем неделю, будущие — сутки, чтобы не тратить на них запросы каждый раз."""
    failed = failed or {}
    chosen = [g for g in groups if g["code"] in wanted]
    others = [g for g in groups if g["code"] not in wanted]
    near = [this_monday + dt.timedelta(weeks=k) for k in range(3)]
    tasks = []
    # 1) выбранные группы: текущая и 2 следующие недели — каждый раз (там бывают переносы)
    for g in chosen:
        tasks += [(m, g, True) for m in near if m in weeks]
    # 2) остальные группы: одна неделя — та, которую будут смотреть: с понедельника по четверг текущая,
    #    с пятницы — следующая; если её нет или она старше суток. Сначала те, которых нет совсем
    today = today or dt.date.today()
    others_week = near[1] if today.weekday() >= 4 else near[0]
    stale = []
    for k, m in enumerate([others_week]):
        for g in others:
            age = _age(m, g["id"])
            if m in weeks and (age is None or age > DAY):
                stale.append((age is not None, k, -(age or 0), m, g))
    stale.sort(key=lambda x: x[:3])
    tasks += [(m, g, False) for *_, m, g in stale[:others_limit]]
    # 3) выбранные группы: остальные недели семестра — будущие раз в 3 дня, прошедшие — только если их нет
    for g in chosen:
        for m in weeks:
            if m in near:
                continue
            age = _age(m, g["id"])
            err = failed.get(f"{m.isoformat()}|{g['id']}")
            if err and time.time() - err < (7 * 86400 if m < this_monday else DAY):
                continue
            if age is None or (m > this_monday and age > FAR_AGE):
                tasks.append((m, g, True))
    return tasks


SUSPICIOUS_STOP = 5  # столько подозрительных недель за запуск (и не меньше 30 % скачанных) — останавливаемся


def _check_saved() -> guard.Vocabulary:
    """Проверяет всё сохранённое расписание и собирает «словарь» настоящих предметов и преподавателей.
    Подозрительные недели только показывает в журнале и НЕ удаляет: если проверка ошиблась, удалённое
    настоящее расписание пропало бы, а показывать подозрительное пользователям бот и так не станет."""
    vocab = guard.Vocabulary()
    removed = []
    for (mon, gid), (_, data) in list(bot.client._cache.items()):
        try:
            lessons = parse_week(data, gid)
        except Exception:  # noqa: BLE001 — сломанная запись: тоже выбрасываем
            lessons, problems = [], ["не читается"]
        else:
            problems = guard.check_lessons(lessons)
        if problems:
            removed.append(f"{gid} {mon:%d.%m}: {problems[0]}")
        else:
            vocab.add(lessons)
    if removed:
        say(f"⚠️ в сохранённом расписании подозрительные недели ({len(removed)}), бот их не покажет: "
            + "; ".join(removed[:3]))
    return vocab


def _validator(vocab: guard.Vocabulary, stats: dict):
    """Проверка каждой скачанной недели перед сохранением (bot.client.validate)."""
    def validate(monday, gid, new_root, old_root):
        new = parse_week(new_root, gid)
        problems = guard.check_lessons(new)
        reason = problems[0] if problems else None
        if not reason and old_root is not None:
            reason = guard.check_update(new, parse_week(old_root, gid), vocab)
        if reason:
            stats["suspicious"] += 1
            if len(stats["examples"]) < 3:
                stats["examples"].append(f"{gid} {monday:%d.%m}: {reason}")
            raise guard.SuspiciousData("подозрительные данные — не сохранил, оставил прежние")
        vocab.add(new)
    return validate


def compact(weeks: list[dt.date]) -> None:
    """Ужимает кэш (только нужные поля) и выбрасывает прошлые семестры — файл уходит на сервер."""
    first = weeks[0] - dt.timedelta(weeks=1)
    cache = bot.client._cache
    for key, (ts, data) in list(cache.items()):
        if key[0] < first:
            del cache[key]
        else:
            cache[key] = (ts, slim_week(data))
    bot.client._save_cache()


def download(interactive: bool, srv: str) -> int:
    """Скачивает расписание по плану (см. plan). Отдельные недели сайт может не отдавать (летние —
    ошибкой 500): их пропускаем. Ошибка — только если у выбранной группы не скачалась текущая неделя."""
    # к сайту Губкина — напрямую, мимо прокси из настроек Windows: выключенный VPN оставляет там
    # свой адрес (127.0.0.1:…), и тогда ни один запрос не проходит; российскому сайту VPN не нужен
    bot.client.s.trust_env = False
    _with_captcha(interactive, bot._ensure_group)
    bot.client.cache_ttl = 60  # то, что решили обновить, — берём с сайта, а не из кэша
    bot.client.delay = 1.5     # частые запросы подряд сайт принимает за робота и просит капчу
    today = dt.date.today()
    weeks = mondays(today)
    this_monday = today - dt.timedelta(days=today.weekday())
    groups = refresh_groups(interactive)
    # защита от подмены: проверяем сохранённое и каждую скачанную неделю
    stats = {"suspicious": 0, "examples": []}
    bot.client.validate = _validator(_check_saved(), stats)
    wanted = wanted_codes(srv)
    bad_weeks = _load_failed()
    tasks = plan(groups, wanted, weeks, this_monday, others_limit=None if interactive else 60, failed=bad_weeks,
                 today=today)
    say(f"групп: {len(groups)}, выбраны пользователями: {len(wanted & {g['code'] for g in groups})}, "
        f"запросов в плане: {len(tasks)}")
    # по расписанию задача Windows живёт не дольше 15 минут; вручную — качаем, сколько нужно
    deadline = None if interactive else time.monotonic() + 11 * 60
    sent_at = time.monotonic()  # когда последний раз отправили расписание на сервер
    failed: dict[tuple, str] = {}
    fetched = 0
    def fetch(mon, g, chosen) -> None:
        key = (mon, g["code"], chosen)
        failed.pop(key, None)
        bot.client.stale = False
        try:
            _with_captcha(interactive, bot.client.week, mon, g["id"])
            if bot.client.stale:  # сайт не ответил, вернулась старая копия
                failed[key] = "сайт не ответил"
            else:
                bad_weeks.pop(f"{mon.isoformat()}|{g['id']}", None)
        except CaptchaRequired:
            compact(weeks)
            _save_failed(bad_weeks)
            raise
        except (GubkinError, OSError, ValueError) as e:
            failed[key] = _short(e)
            if isinstance(e, GubkinError):  # сайт ответил ошибкой — запомним, чтобы не спрашивать каждый раз
                bad_weeks[f"{mon.isoformat()}|{g['id']}"] = time.time()

    in_a_row = 0  # неудач подряд
    for i, (mon, g, chosen) in enumerate(tasks, 1):
        if deadline is not None and time.monotonic() > deadline:
            say(f"время вышло, осталось запросов: {len(tasks) - i + 1} — докачаю в следующий раз")
            break
        if in_a_row >= STOP_AFTER:
            if interactive:
                print()
            say(f"{STOP_AFTER} запросов подряд не прошли — похоже, нет связи с сайтом. Остановился, "
                "попробую в следующий раз")
            break
        _touch_lock()  # долгий запуск: замок свежий, чтобы второй sync не решил, что он брошен
        if interactive and time.monotonic() - sent_at > SEND_EVERY:
            # долгий ручной запуск: отдаём боту уже скачанное, не дожидаясь конца
            sent_at = time.monotonic()
            try:
                put(srv, "cache.json", bot.client.cache_file.read_bytes())
            except Exception as e:  # noqa: BLE001 — не вышло сейчас, отправим в конце
                print(f"\n  (не удалось отправить промежуточно: {_short(e)})")
        fetched += 1
        fetch(mon, g, chosen)
        if stats["suspicious"] >= SUSPICIOUS_STOP and stats["suspicious"] * 10 >= fetched * 3:
            if interactive:
                print()
            compact(weeks)
            _save_failed(bad_weeks)
            say("⚠️ сайт отдаёт подозрительное расписание: " + "; ".join(stats["examples"]))
            raise guard.SuspiciousData(f"сайт отдаёт подозрительное расписание ({stats['suspicious']} недель) — "
                                       "остановился, на сервере осталось прежнее")
        # считаем только «нет связи»; ответ сайта с ошибкой (500 на летние недели) — это связь есть
        no_link = failed.get((mon, g["code"], chosen)) in NO_LINK
        in_a_row = in_a_row + 1 if no_link else 0
        if interactive:
            print(f"\r  {i}/{len(tasks)}  {g['code']:<14}", end="", flush=True)
    if interactive and tasks:
        print()
    # сайт иногда не отвечает на первые запросы (перед капчей) — выбранным группам даём второй шанс
    again = [k for k, e in failed.items() if e == "сайт не ответил" and k[2]]
    if again and (deadline is None or time.monotonic() < deadline + 60):
        time.sleep(5)
        by_code = {g["code"]: g for g in groups}
        for mon, code, chosen in again:
            if code in by_code:
                fetch(mon, by_code[code], chosen)
    compact(weeks)
    _save_failed(bad_weeks)
    if stats["suspicious"]:
        say("⚠️ подозрительные недели (не сохранены): " + "; ".join(stats["examples"]))
    if failed:
        by_error: dict[str, list[str]] = {}
        for (mon, code, _), e in failed.items():
            by_error.setdefault(e, []).append(f"{code} {mon:%d.%m}")
        say("не скачались: " + "; ".join(
            f"{e}: {', '.join(v[:8])}{f' и ещё {len(v) - 8}' if len(v) > 8 else ''}" for e, v in by_error.items()))
    bad = sorted({code for (mon, code, chosen) in failed if chosen and mon == this_monday})
    if bad:
        raise GubkinError(f"не скачалась текущая неделя: {', '.join(bad)}")
    return fetched - len(failed)


LOCK = HERE / "sync.lock"
SEND_EVERY = 10 * 60  # долгий ручной запуск отправляет скачанное на сервер раз в 10 минут


def _touch_lock() -> None:
    try:
        os.utime(LOCK)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Работает ли ещё программа с этим номером процесса (окно могли закрыть крестиком)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.OpenProcess.restype = wintypes.HANDLE
            k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ctypes.get_last_error() == 5  # нет доступа — процесс есть, но чужой; иначе его нет
            code = wintypes.DWORD()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return not ok or code.value == 259  # STILL_ACTIVE
        except Exception:  # noqa: BLE001 — не смогли проверить: считаем живым, сработает правило 40 минут
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _take_lock() -> bool:
    """Только одна копия sync за раз: две копии мешают друг другу (каждая получает свою капчу,
    и коды не подходят). Замок брошен, если программы, которая его поставила, уже нет (окно закрыли
    крестиком) или он не обновлялся 40 минут (работающий sync обновляет его перед каждым запросом)."""
    try:
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 40 * 60:
            try:
                pid = int(LOCK.read_text(encoding="utf-8").strip() or 0)
            except ValueError:
                pid = 0
            if _pid_alive(pid):
                return False
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except OSError:
        return True


def run(interactive: bool) -> int:
    if not _take_lock():
        say("sync уже работает в другом окне (или по расписанию) — дождитесь, пока он закончит. "
            "Если других окон точно нет — удалите файл sync.lock или подождите 40 минут.")
        return 1
    try:
        return _run(interactive)
    finally:
        try:
            if LOCK.read_text(encoding="utf-8").strip() == str(os.getpid()):  # снимаем только свой замок
                LOCK.unlink()
        except OSError:
            pass


def _run(interactive: bool) -> int:
    srv = server()
    try:
        prev = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    now = dt.datetime.now().isoformat(timespec="seconds")
    status = {"time": now, "ok": False, "error": "", "last_ok": prev.get("last_ok")}
    try:
        n = download(interactive, srv)
        status.update(ok=True, last_ok=now)
        say(f"обновлено недель: {n}")
    except CaptchaRequired:
        status["error"] = "captcha"
        say("сайт просит капчу — запустите sync_now.bat")
    except guard.SuspiciousData as e:
        status["error"] = "suspicious: " + str(e)[:250]
        say(f"не удалось скачать: {e}")
    except Exception as e:  # нет интернета, сайт лежит и т.п. — попробуем в следующий раз
        status["error"] = str(e)[:300]
        say(f"не удалось скачать: {e}")
    write_atomic(STATUS, json.dumps(status, ensure_ascii=False))

    try:
        if status["ok"] or bot.client.cache_file.exists():
            put(srv, "cache.json", bot.client.cache_file.read_bytes())
        if bot.GROUP_FILE.exists():
            put(srv, "group.json", bot.GROUP_FILE.read_bytes())
        if bot.GROUPS_FILE.exists():
            put(srv, "groups.json", bot.GROUPS_FILE.read_bytes())
        put(srv, "sync_status.json", STATUS.read_bytes())
        say(f"отправлено на сервер {srv}")
    except Exception as e:
        say(f"не удалось отправить на сервер: {e}")
        return 1
    return 0 if status["ok"] else 1


# ---- первичная настройка

def setup() -> None:
    srv = server()
    print(f"Сервер: {srv}  (другой адрес — впишите его в server.txt)\n")

    key = KEY
    if not key.exists():
        print("1) Создаю ключ для входа на сервер без пароля...")
        key.parent.mkdir(exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    else:
        print("1) Ключ уже есть.")

    if ssh([srv, "true"]).returncode != 0:
        print("2) Добавляю ключ на сервер — введите ПАРОЛЬ ОТ СЕРВЕРА (символы не видны):")
        # ключ передаём в самой команде, а не через ввод: иначе ssh в Windows не может спросить пароль
        pub = key.with_suffix(".pub").read_text(encoding="utf-8").strip().replace("'", "")
        r = ssh([srv, f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{pub}' >> ~/.ssh/authorized_keys "
                      "&& chmod 600 ~/.ssh/authorized_keys"], batch=False)
        if r.returncode != 0 or ssh([srv, "true"]).returncode != 0:
            sys.exit("❌ Не получилось войти на сервер. Проверьте пароль и запустите ещё раз.")
    print("2) Вход на сервер по ключу работает.")

    print("3) Включаю на сервере режим «расписание присылает ноутбук»...")
    remote = (
        f"git -C /opt/gubkin-bot fetch -q origin {BRANCH} && "
        f"git -C /opt/gubkin-bot reset -q --hard origin/{BRANCH} && "
        "mkdir -p /etc/systemd/system/gubkin-bot.service.d && "
        "printf '[Service]\\nEnvironment=GUBKIN_OFFLINE=1\\nEnvironment=GUBKIN_PROXY_ALWAYS=\\n' "
        "> /etc/systemd/system/gubkin-bot.service.d/offline.conf && "
        f"chown -R gubkin: {REMOTE} && systemctl daemon-reload"
    )
    if ssh([srv, remote], timeout=180).returncode != 0:
        sys.exit("❌ Не удалось настроить сервер.")

    print("4) Скачиваю расписание и отправляю на сервер (около минуты)...")
    code = run(interactive=True)
    ssh([srv, "systemctl restart gubkin-bot"])

    print("5) Добавляю sync в автозапуск Windows...")
    exe = pathlib.Path(sys.executable)
    pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
    ps = (
        f"$a = New-ScheduledTaskAction -Execute '{pyw}' -Argument '\"{HERE / 'sync.py'}\"' "
        f"-WorkingDirectory '{HERE}'\n"
        "$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME\n"
        "$t1.Delay = 'PT2M'\n"
        "$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) "
        "-RepetitionInterval (New-TimeSpan -Hours 3) -RepetitionDuration (New-TimeSpan -Days 3650)\n"
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)\n"
        f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t1,$t2 -Settings $s -Force | Out-Null\n"
    ).replace("\n", "; ")  # одной строкой: перевод строки в аргументе командной строки Windows ненадёжен
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps])
    if r.returncode != 0:
        sys.exit("❌ Не удалось добавить в автозапуск. Запустите sync_setup.bat правой кнопкой → "
                 "«Запуск от имени администратора».")

    print("\n✅ Готово! Ноутбук будет сам присылать расписание: через 2 минуты после включения "
          "и каждые 3 часа.")
    if code:
        print("⚠️  Но первая отправка не удалась — посмотрите сообщения выше (или sync.log).")
    print("Бота на ноутбуке (start_bot.bat) больше запускать не нужно — он работает на сервере.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--setup", action="store_true")
    p.add_argument("--interactive", action="store_true")
    a = p.parse_args()
    if a.setup:
        setup()
    else:
        sys.exit(run(a.interactive))


if __name__ == "__main__":
    main()
