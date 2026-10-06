"""Клиент публичного API расписания РГУ нефти и газа им. Губкина (lk.gubkin.ru/schedule).

Логин не нужен. Сайт требует «визит» на /schedule/ в той же сессии (cookie PHPSESSID),
иначе защита отвечает HTML-страницей вместо JSON. Иногда сайт просит капчу (HTTP 429).
"""

from __future__ import annotations

import datetime as dt
import json
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
    def __init__(self, timeout: float = 40, delay: float = 0.5):
        self.timeout = timeout
        self.delay = delay  # пауза между запросами, чтобы не злить WAF
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": BASE + "schedule/",
        })

    # ---- сессия

    def _visit(self, force: bool = False) -> None:
        if not force and self.s.cookies.get("PHPSESSID"):
            return
        self.s.get(BASE + "schedule/", headers={"Accept": "text/html,*/*"}, timeout=self.timeout)

    def _get(self, params: dict) -> dict:
        self._visit()
        for attempt in range(2):
            if self.delay:
                time.sleep(self.delay)
            r = self.s.get(API, params=params, timeout=self.timeout)
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
        """Сырой ответ за неделю, содержащую day."""
        return self._get({"act": "schedule", "date": f"{day.day}-{day.month}-{day.year}",
                          "groupId": group_id})

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
