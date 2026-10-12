"""Клиент публичного API расписания РГУ нефти и газа им. Губкина (lk.gubkin.ru/schedule).

Логин не нужен. Сайт требует «визит» на /schedule/ в той же сессии (cookie PHPSESSID),
иначе защита отвечает HTML-страницей вместо JSON. Иногда сайт просит капчу (HTTP 429).
"""

from __future__ import annotations

import datetime as dt
import json
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


class GubkinError(Exception):
    pass


class CaptchaRequired(GubkinError):
    """Сайт просит ввести капчу: см. GubkinClient.captcha_image / validate_captcha."""


class GubkinClient:
    def __init__(self, timeout: float | tuple = (10, 40), delay: float = 0.5,
                 cookie_file: str | None = None, cache_ttl: float = 30 * 60,
                 cache_file: str | None = None):
        self.timeout = timeout  # (подключение, ожидание ответа) в секундах
        self.stale = False  # True, если последний ответ взят из сохранённого кэша
        self.delay = delay  # пауза между запросами, чтобы не злить WAF
        self.cache_ttl = cache_ttl  # сколько секунд помнить скачанную неделю
        self._cache: dict[tuple, tuple[float, dict]] = {}
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

    def _load_cache(self) -> None:
        if not self.cache_file:
            return
        try:
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
            self.cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
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
            self.cookie_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    # ---- сессия

    def _visit(self, force: bool = False) -> None:
        if not force and self.s.cookies.get("PHPSESSID"):
            return
        self.s.get(BASE + "schedule/", headers={"Accept": "text/html,*/*"}, timeout=self.timeout)
        self._save_cookies()

    def _get(self, params: dict) -> dict:
        self._visit()
        for attempt in range(2):
            if self.delay:
                time.sleep(self.delay)
            r = self.s.get(API, params=params, timeout=self.timeout)
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
        r = self.s.get(API, params={"act": "Captcha", "method": "generateCaptcha"},
                       headers={"Accept": "image/*,*/*"}, timeout=self.timeout)
        if not r.ok:
            raise GubkinError(f"Не удалось загрузить капчу ({r.status_code})")
        return r.content

    def validate_captcha(self, code: str) -> bool:
        r = self.s.post(API, params={"act": "Captcha", "method": "validateCaptcha"},
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

    def week(self, day: dt.date, group_id) -> dict:
        """Сырой ответ за неделю, содержащую day. Неделя кэшируется на cache_ttl секунд,
        чтобы повторные /today не дёргали сайт (частые запросы вызывают капчу)."""
        monday = day - dt.timedelta(days=day.weekday())
        key = (monday, str(group_id))
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
        self._cache[key] = (time.time(), data)
        self._save_cache()
        return data

    def find_group(self, code: str, faculty_hint: str = "геолог") -> tuple[dict, dict]:
        """Ищет группу по коду. Сначала на факультетах, чьё имя содержит faculty_hint,
        затем на всех остальных. Возвращает (факультет, группа)."""
        wanted = normalize_code(code)
        facs = self.faculties()
        hint = faculty_hint.lower()
        facs.sort(key=lambda f: hint not in str(f.get("name") or f.get("title") or "").lower())
        for f in facs:
            for g in self.groups(f["id"]):
                if normalize_code(str(g.get("code") or g.get("name") or "")) == wanted:
                    return f, g
        raise GubkinError(f"Группа {code} не найдена в списке групп сайта")
