"""Клиент публичного API расписания РГУ нефти и газа им. Губкина (lk.gubkin.ru/schedule).

Логин не нужен. Сайт требует «визит» на /schedule/ в той же сессии (cookie PHPSESSID),
иначе защита отвечает HTML-страницей вместо JSON. Иногда сайт просит капчу (HTTP 429).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time

import requests

BASE = "https://lk.gubkin.ru/"
API = BASE + "schedule/api/api.php"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_LATIN_TO_CYR = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "–": "-", "—": "-",
})


def normalize_code(code: str) -> str:
    """'гм-26-11', 'ГM-26-11' (латинская M), 'ГМ – 26 – 11' -> 'ГМ-26-11'."""
    return "".join(code.upper().translate(_LATIN_TO_CYR).split())


def write_atomic(path: pathlib.Path, text: str) -> None:
    """Записывает файл так, что он всегда целый: сначала во временный файл, потом мгновенная подмена.
    Если ноутбук выключится посреди записи, останется прежняя версия, а не обрывок."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class GubkinError(Exception):
    pass


class CaptchaRequired(GubkinError):
    """Сайт просит ввести капчу: см. GubkinClient.captcha_image / validate_captcha."""


class GubkinClient:
    def __init__(self, timeout: float | tuple = (10, 40), delay: float = 0.5,
                 cookie_file: str | None = None, cache_ttl: float = 30 * 60,
                 cache_file: str | None = None, fallback_proxy: str | None = None,
                 proxy: str | None = None, offline: bool = False):
        # если сайт не отвечает напрямую — запасной путь через этот прокси (на час, потом снова напрямую)
        self.fallback_proxy = fallback_proxy
        # offline: на сайт не ходим вообще, расписание берём только из cache_file,
        # который присылает ноутбук (sync.py) — для сервера, которого сайт не пускает
        self.offline = offline
        self._cache_mtime = 0.0
        self._proxy_until = 0.0
        self.timeout = timeout  # (подключение, ожидание ответа) в секундах
        self.stale = False  # True, если последний ответ взят из сохранённого кэша
        self.delay = delay  # пауза между запросами, чтобы не злить WAF
        self.cache_ttl = cache_ttl  # сколько секунд помнить скачанную неделю
        self._cache: dict[tuple, tuple[float, dict]] = {}
        # чем ужать ответ сайта перед сохранением в кэш (оставить только нужное) — задаёт бот
        self.slim = None
        # кэш сохраняется на диск: если сайт не отвечает, можно показать последнее расписание
        self.cache_file = pathlib.Path(cache_file) if cache_file else None
        self._load_cache()
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": BASE + "schedule/",
        })
        # cookie сохраняются в файл: пройденная капча и сессия переживают перезапуск
        self.cookie_file = pathlib.Path(cookie_file) if cookie_file else None
        self._load_cookies()
        self.proxy = proxy  # всегда ходить на сайт через этот прокси
        if proxy:
            self.s.proxies.update({"http": proxy, "https": proxy})

    def _load_cache(self) -> None:
        if not self.cache_file:
            return
        try:
            self._cache_mtime = self.cache_file.stat().st_mtime
            for k, (ts, data) in json.loads(self.cache_file.read_text(encoding="utf-8")).items():
                monday, group = k.split("|", 1)
                self._cache[(dt.date.fromisoformat(monday), group)] = (ts, data)
        except (OSError, ValueError, TypeError):
            pass

    def _save_cache(self) -> None:
        if not self.cache_file:
            return
        data = {f"{m.isoformat()}|{g}": v for (m, g), v in self._cache.items()}
        try:
            write_atomic(self.cache_file, json.dumps(data, ensure_ascii=False))
        except OSError:
            pass

    def _load_cookies(self) -> None:
        if not self.cookie_file:
            return
        try:
            for c in json.loads(self.cookie_file.read_text(encoding="utf-8")):
                self.s.cookies.set(c["name"], c["value"], domain=c.get("domain", ""), path=c.get("path", "/"))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_cookies(self) -> None:
        if not self.cookie_file:
            return
        data = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self.s.cookies]
        try:
            write_atomic(self.cookie_file, json.dumps(data))
        except OSError:
            pass

    # ---- сессия

    def _req(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        if not self.proxy and self.s.proxies and time.time() > self._proxy_until:
            self.s.proxies.clear()  # час прошёл — пробуем снова напрямую
        try:
            return self.s.request(method, url, **kw)
        except requests.ConnectionError:
            if not self.fallback_proxy or self.s.proxies:
                raise
            self.s.proxies.update({"http": self.fallback_proxy, "https": self.fallback_proxy})
            self._proxy_until = time.time() + 3600
            return self.s.request(method, url, **kw)

    def _visit(self, force: bool = False) -> None:
        if not force and self.s.cookies.get("PHPSESSID"):
            return
        self._req("GET", BASE + "schedule/", headers={"Accept": "text/html,*/*"})
        self._save_cookies()

    def _get(self, params: dict) -> dict:
        self._visit()
        for attempt in range(2):
            if self.delay:
                time.sleep(self.delay)
            r = self._req("GET", API, params=params)
            self._save_cookies()
            if r.status_code == 429:
                raise CaptchaRequired("Сайт просит капчу")
            body = r.text.lstrip()
            if r.ok and body.startswith("{"):
                return self._check(json.loads(body))
            if attempt == 0:
                self._visit(force=True)  # сессия протухла — пробуем ещё раз
                continue
            if not r.ok:
                raise GubkinError(f"Сайт ответил ошибкой {r.status_code}")
            raise GubkinError("Сайт вернул страницу вместо данных")
        raise AssertionError("unreachable")

    @staticmethod
    def _check(root: dict) -> dict:
        state = root.get("state")
        if state in (None, False, 0, "", "0"):
            reason = str(root.get("reason") or root.get("message") or "")
            if "капч" in reason.lower() or "captcha" in reason.lower():
                raise CaptchaRequired(reason)
            raise GubkinError(reason[:200] or "Сайт отказал в выдаче расписания")
        return root

    # ---- капча

    def captcha_image(self) -> bytes:
        self._visit()
        r = self._req("GET", API, params={"act": "Captcha", "method": "generateCaptcha"},
                       headers={"Accept": "image/*,*/*"}, timeout=self.timeout)
        if not r.ok:
            raise GubkinError(f"Не удалось загрузить капчу ({r.status_code})")
        return r.content

    def validate_captcha(self, code: str) -> bool:
        r = self._req("POST", API, params={"act": "Captcha", "method": "validateCaptcha"},
                        json={"key": code.strip()}, timeout=self.timeout)
        self._save_cookies()
        if r.status_code == 429:
            raise GubkinError("Сайт просит подождать минуту — попробуйте позже")
        try:
            self._check(r.json())
            return True
        except (ValueError, GubkinError):
            return False

    # ---- данные

    def faculties(self) -> list[dict]:
        return self._get({"act": "list", "method": "getFaculties"}).get("rows") or []

    def groups(self, faculty_id) -> list[dict]:
        return self._get({"act": "list", "method": "getFacultyGroups",
                          "facultyId": faculty_id}).get("rows") or []

    def cache_age(self) -> float | None:
        """Сколько секунд назад обновлялся файл кэша (для offline — когда ноутбук присылал расписание)."""
        try:
            return time.time() - self.cache_file.stat().st_mtime
        except (OSError, AttributeError):
            return None

    def _reload_if_changed(self) -> None:
        try:  # ноутбук прислал новый файл — перечитываем
            if self.cache_file.stat().st_mtime != self._cache_mtime:
                self._cache.clear()
                self._load_cache()
        except (OSError, AttributeError):
            pass

    def has_group(self, group_id) -> bool:
        """Есть ли в кэше хоть одна неделя этой группы (offline: прислал ли её ноутбук)."""
        if self.offline:
            self._reload_if_changed()
        gid = str(group_id)
        return any(g == gid for _, g in list(self._cache))

    def _offline_week(self, key: tuple) -> dict:
        self._reload_if_changed()
        hit = self._cache.get(key)
        if not hit:
            raise GubkinError("расписания на эту неделю ещё нет на сервере — его присылает ноутбук "
                              "(sync). Включите ноутбук с интернетом.")
        if (self.cache_age() or 0) > 36 * 3600:
            self.stale = True
        return hit[1]

    def week(self, day: dt.date, group_id) -> dict:
        """Сырой ответ за неделю, содержащую day. Неделя кэшируется на cache_ttl секунд,
        чтобы повторные /today не дёргали сайт (частые запросы вызывают капчу)."""
        monday = day - dt.timedelta(days=day.weekday())
        key = (monday, str(group_id))
        if self.offline:
            return self._offline_week(key)
        hit = self._cache.get(key)
        this_monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
        # прошедшие недели уже не меняются — их можно помнить дольше (для /stats и /all)
        ttl = self.cache_ttl if monday >= this_monday else max(self.cache_ttl, 6 * 3600)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            data = self._get({"act": "schedule", "date": f"{day.day}-{day.month}-{day.year}",
                              "groupId": group_id})
        except CaptchaRequired:
            raise
        except (GubkinError, OSError, ValueError):
            if hit:  # сайт не отвечает — отдаём последнее сохранённое расписание
                self.stale = True
                return hit[1]
            raise
        if self.slim:
            data = self.slim(data)
        self._cache[key] = (time.time(), data)
        self._save_cache()
        return data

    def find_faculty(self, code: str, faculty_hint: str = "геолог") -> tuple[dict, list[dict], dict]:
        """Ищет факультет, в котором есть группа code. Сначала смотрит факультеты, чьё имя содержит
        faculty_hint, затем остальные. Возвращает (факультет, все его группы, сама группа)."""
        if self.offline:
            raise GubkinError("группа ещё не известна серверу — запустите sync на ноутбуке")
        wanted = normalize_code(code)
        facs = self.faculties()
        hint = faculty_hint.lower()
        facs.sort(key=lambda f: hint not in str(f.get("name") or f.get("title") or "").lower())
        for f in facs:
            rows = self.groups(f["id"])
            for g in rows:
                if normalize_code(str(g.get("code") or g.get("name") or "")) == wanted:
                    return f, rows, g
        raise GubkinError(f"Группа {code} не найдена в списке групп сайта")

    def find_group(self, code: str, faculty_hint: str = "геолог") -> tuple[dict, dict]:
        """Ищет группу по коду. Возвращает (факультет, группа)."""
        f, _, g = self.find_faculty(code, faculty_hint)
        return f, g
