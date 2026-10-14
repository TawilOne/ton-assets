"""Ноутбук → сервер: скачивает расписание группы на семестр и отправляет его боту на сервер.

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

HERE = pathlib.Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

import bot  # noqa: E402  — та же группа, cache.json и cookies.json, что у бота на ноутбуке
from gubkin_api import CaptchaRequired, GubkinError  # noqa: E402
from schedule import semester_bounds  # noqa: E402

DEFAULT_SERVER = "root@153.76.167.68"
REMOTE = "/opt/gubkin-bot/gubkin_schedule_bot"
BRANCH = "claude/rgu-gubkina-schedule-bot-snqhke"
TASK = "GubkinSync"
LOG = HERE / "sync.log"
STATUS = HERE / "sync_status.json"
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
    opts = ["-i", str(KEY), "-o", "IdentitiesOnly=yes",
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


def solve_captcha() -> None:
    img = bot.client.captcha_image()
    pic = HERE / "captcha.png"
    pic.write_bytes(img)
    if os.name == "nt":
        os.startfile(pic)  # откроется в просмотрщике картинок
    else:
        print(f"Картинка: {pic}")
    for _ in range(3):
        if bot.client.validate_captcha(input("Введите символы с картинки: ")):
            print("✅ Капча принята")
            return
        print("Не подошло, попробуйте ещё раз.")
    raise CaptchaRequired("капча не пройдена")


def download(interactive: bool) -> int:
    """Скачивает все недели семестра. Отдельные недели сайт может не отдавать (например, летние —
    отвечает ошибкой 500): их пропускаем. Ошибка — только если не скачалась текущая неделя."""
    bot._ensure_group()
    gid = bot._group["id"]
    bot.client.cache_ttl = 60  # за свежим — на сайт; прошедшие недели всё равно помнятся 6 ч
    weeks = mondays(dt.date.today())
    this_monday = dt.date.today() - dt.timedelta(days=dt.date.today().weekday())
    failed: dict[dt.date, str] = {}
    for i, mon in enumerate(weeks, 1):
        bot.client.stale = False
        while True:
            try:
                bot.client.week(mon, gid)
                if bot.client.stale:  # сайт не ответил, вернулась старая копия
                    failed[mon] = "сайт не ответил"
                break
            except CaptchaRequired:
                if not interactive:
                    raise
                solve_captcha()
            except (GubkinError, OSError, ValueError) as e:
                failed[mon] = str(e)
                break
        if interactive:
            print(f"\r  неделя {i}/{len(weeks)}", end="", flush=True)
    if interactive:
        print()
    if failed:
        say("не скачались недели: " + ", ".join(f"{m:%d.%m} ({e})" for m, e in failed.items()))
    if this_monday in failed:
        raise GubkinError(f"не скачалась текущая неделя: {failed[this_monday]}")
    return len(weeks) - len(failed)


def run(interactive: bool) -> int:
    srv = server()
    try:
        prev = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    now = dt.datetime.now().isoformat(timespec="seconds")
    status = {"time": now, "ok": False, "error": "", "last_ok": prev.get("last_ok")}
    try:
        n = download(interactive)
        status.update(ok=True, last_ok=now)
        say(f"скачано недель: {n}")
    except CaptchaRequired:
        status["error"] = "captcha"
        say("сайт просит капчу — запустите sync_now.bat")
    except Exception as e:  # нет интернета, сайт лежит и т.п. — попробуем в следующий раз
        status["error"] = str(e)[:300]
        say(f"не удалось скачать: {e}")
    STATUS.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")

    try:
        if status["ok"] or bot.client.cache_file.exists():
            put(srv, "cache.json", bot.client.cache_file.read_bytes())
        if bot.GROUP_FILE.exists():
            put(srv, "group.json", bot.GROUP_FILE.read_bytes())
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
