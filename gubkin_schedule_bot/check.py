"""Самопроверка заданий из my_commands.py.   Запуск:  python check.py

Интернет не нужен — используются выдуманные пары.
"""

import traceback

import my_commands as my
from schedule import Lesson


def lesson(date, weekday, start, subject, kind, room="100", cancelled=False):
    return Lesson(date=date, weekday=weekday, start=start, end="", subject=subject,
                  kind=kind, room=room, teacher="Иванов И. И.", cancelled=cancelled)


WEEK = [
    lesson("2026-10-05", "Понедельник", "08:45", "Общая геология", "Лекция", "1234"),
    lesson("2026-10-05", "Понедельник", "10:30", "Минералогия", "Лабораторная", "512"),
    lesson("2026-10-06", "Вторник", "08:45", "Общая геология", "Семинар", "301"),
    lesson("2026-10-08", "Четверг", "12:30", "Высшая математика", "Лекция", "1001"),
    lesson("2026-10-09", "Пятница", "08:45", "Общая геология", "Лекция", "1234"),
]
TOMORROW = [
    lesson("2026-10-06", "Вторник", "08:45", "Физкультура", "Практика", "Спортзал", cancelled=True),
    lesson("2026-10-06", "Вторник", "10:30", "Минералогия", "Лабораторная", "512"),
]


def need(result):
    assert result is not None, "ещё не сделано (функция возвращает None)"
    return result


def task1():
    t = need(my.about_text())
    assert isinstance(t, str) and t.strip(), "нужно вернуть непустую строку"


def task2():
    t = need(my.count_text(WEEK))
    assert isinstance(t, str), "нужно вернуть строку"
    assert "5" in t, f"в неделе 5 пар, а в ответе: {t!r}"


def task2_bonus():
    words = {1: "пара", 3: "пары", 5: "пар", 11: "пар", 12: "пар", 14: "пар",
             21: "пара", 22: "пары", 25: "пар", 111: "пар"}
    for n, word in words.items():
        t = need(my.count_text([WEEK[0]] * n))
        assert t.rstrip(" .!").endswith(f"{n} {word}"), f"для {n} ожидалось «{n} {word}», а получилось {t!r}"


def task3():
    r = need(my.only_kind(WEEK, "Лекция"))
    assert isinstance(r, list), "нужно вернуть список"
    assert [l.subject for l in r] == ["Общая геология", "Высшая математика", "Общая геология"], \
        f"лекций должно быть 3, а у вас {len(r)}"
    assert my.only_kind(WEEK, "Экзамен") == [], "если таких пар нет — пустой список"


def task4():
    assert need(my.first_lesson_text([])) == "Завтра пар нет, можно выспаться 😴", \
        "для пустого списка нужен текст «Завтра пар нет, можно выспаться 😴»"
    t = need(my.first_lesson_text(TOMORROW[1:]))
    assert "10:30" in t and "Минералогия" in t, f"ожидалось время 10:30 и Минералогия, а получилось {t!r}"


def task4_bonus():
    t = need(my.first_lesson_text(TOMORROW))
    assert "10:30" in t, f"Физкультуру отменили — первая пара в 10:30, а получилось {t!r}"


def task5():
    t = need(my.stats_text(WEEK))
    assert isinstance(t, str), "нужно вернуть строку"
    for line in ["Общая геология — 3", "Минералогия — 1", "Высшая математика — 1"]:
        assert line in t, f"в ответе нет строки «{line}». Ответ:\n{t}"


def task5_bonus():
    t = need(my.stats_text(WEEK))
    assert t.startswith("Общая геология"), "самый частый предмет (Общая геология) должен быть первым"


def task6():
    r = need(my.free_days(WEEK))
    assert r == ["Среда", "Суббота"], f"свободны Среда и Суббота, а у вас {r!r}"


TASKS = [
    ("Задание 1  /about", task1),
    ("Задание 2  /count", task2),
    ("Задание 2★ окончания", task2_bonus),
    ("Задание 3  /lectures", task3),
    ("Задание 4  /first", task4),
    ("Задание 4★ отмены", task4_bonus),
    ("Задание 5  /stats", task5),
    ("Задание 5★ сортировка", task5_bonus),
    ("Задание 6★ /free", task6),
]


def main():
    done = 0
    for name, test in TASKS:
        try:
            test()
            print(f"✅ {name}")
            done += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
        except Exception:
            print(f"💥 {name}: ошибка в коде —")
            print("   " + traceback.format_exc().strip().splitlines()[-1])
    print(f"\nГотово {done} из {len(TASKS)}")
    if done == len(TASKS):
        print("🎉 Все задания выполнены! Можно придумывать свои команды.")


if __name__ == "__main__":
    main()
