"""Тесты игрового сервера (games.py): подпись Telegram, поиск соперника, приглашение, ходы, рейтинг, HTTP."""
import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse

import pytest
from aiohttp.test_utils import TestClient, TestServer

import games

TOKEN = "123:secret"


def init_data(uid, first="Иван", last="Петров", token=TOKEN, auth=None):
    data = {"auth_date": str(int(auth or time.time())), "query_id": "q",
            "user": json.dumps({"id": uid, "first_name": first, "last_name": last}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "HERE", tmp_path)
    (tmp_path / "user_groups.json").write_text(json.dumps({"1": "ГМ-26-11", "2": "АА-26-01"}), encoding="utf-8")
    (tmp_path / "groups.json").write_text(json.dumps({"faculties": [
        {"key": "geo", "groups": [{"id": "1", "code": "ГМ-26-11"}]},
        {"key": "aivt", "groups": [{"id": "2", "code": "АА-26-01"}]}]}), encoding="utf-8")
    s = games.Store(tmp_path / "g.db")
    for uid, first in ((1, "Иван"), (2, "Мария"), (3, "Олег")):
        s.touch({"id": uid, "first_name": first, "last_name": "Тестов"})
    return s


def test_init_data_signature():
    user = games.check_init_data(init_data(7), TOKEN)
    assert user and user["id"] == 7
    assert games.check_init_data(init_data(7, token="999:other"), TOKEN) is None  # чужая подпись
    forged = init_data(7).replace("%22id%22%3A+7", "%22id%22%3A+8")
    assert games.check_init_data(forged, TOKEN) is None  # подменили id — подпись не сходится
    assert games.check_init_data(init_data(7, auth=time.time() - 3 * 86400), TOKEN) is None  # устарела
    assert games.check_init_data("", TOKEN) is None


def test_search_pairs_two_players_and_faculty_is_shown(store):
    a = store.create(1, "search")
    assert store.view(1, a)["status"] == "wait"
    assert store.create(1, "search") == a  # повторный поиск — тот же
    b = store.create(2, "search")
    assert a == b
    v = store.view(1, a)
    assert v["status"] == "play" and {v["white"]["id"], v["black"]["id"]} == {1, 2}
    them = v["black"] if v["you"] == "white" else v["white"]
    assert them["faculty"] == "💻 Автоматики и ВТ" and them["name"] == "Мария Т."


def test_invite_only_by_link(store):
    gid = store.create(1, "invite")
    assert store.create(2, "search") != gid  # приглашение в открытый поиск не попадает
    store.join(3, gid)
    v = store.view(3, gid)
    assert v["status"] == "play" and v["you"] in ("white", "black")
    with pytest.raises(games.GameError):
        store.join(2, gid)  # третьему места нет


def play(store, gid, *moves):
    for uci in moves:
        v = store.view(1, gid)
        uid = v["white"]["id"] if v["turn"] == "white" else v["black"]["id"]
        store.move(uid, gid, uci)


def test_moves_are_checked_and_mate_updates_rating(store):
    gid = store.create(1, "invite")
    store.join(2, gid)
    v = store.view(1, gid)
    white, black = v["white"]["id"], v["black"]["id"]
    with pytest.raises(games.GameError):
        store.move(black, gid, "e7e5")  # не его ход
    with pytest.raises(games.GameError):
        store.move(white, gid, "e2e5")  # так ходить нельзя
    assert "e2e4" in store.view(white, gid)["legal"] and store.view(black, gid)["legal"] == []
    play(store, gid, "f2f3", "e7e5", "g2g4", "d8h4")  # детский мат чёрным
    v = store.view(1, gid)
    assert v["status"] == "over" and v["result"] == "0-1" and v["reason"] == "checkmate"
    w, b = store.player(white), store.player(black)
    assert (w["losses"], b["wins"]) == (1, 1) and w["rating"] == 1184 and b["rating"] == 1216
    assert [p["id"] for p in store.top()] == [black, white]
    with pytest.raises(games.GameError):
        store.move(white, gid, "e2e4")  # партия закончена


def test_resign_draw_and_timeout(store, monkeypatch):
    gid = store.create(1, "invite")
    store.join(2, gid)
    store.draw(1, gid, "offer")
    assert store.view(2, gid)["draw_offer"] == "them"
    with pytest.raises(games.GameError):
        store.draw(1, gid, "accept")  # своё предложение принять нельзя
    store.draw(2, gid, "accept")
    v = store.view(1, gid)
    assert v["result"] == "1/2-1/2" and store.player(1)["draws"] == 1 and store.player(1)["rating"] == 1200

    gid = store.create(1, "invite")
    store.join(2, gid)
    store.resign(1, gid)
    assert store.player(1)["losses"] == 1 and store.player(2)["wins"] == 1

    gid = store.create(1, "invite")
    store.join(3, gid)
    later = time.time() + games.INACTIVE + 10
    monkeypatch.setattr(games.time, "time", lambda: later)
    v = store.view(3, gid)  # три дня без хода — тот, чей ход (белые), проиграл
    assert v["status"] == "over" and v["reason"] == "time" and v["result"] == "0-1"


def test_http_api(store):
    async def go():
        client = TestClient(TestServer(games.make_app(store, TOKEN)))
        await client.start_server()
        try:
            r = await client.get("/api/me")
            assert r.status == 401
            h1 = {"X-Init-Data": init_data(1), "Origin": "https://tawilone.github.io"}
            h2 = {"X-Init-Data": init_data(2, first="Мария")}
            r = await client.post("/api/new", json={"kind": "invite"}, headers=h1)
            assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == "https://tawilone.github.io"
            gid = (await r.json())["id"]
            r = await client.post("/api/act", json={"id": gid, "action": "join"}, headers=h2)
            v = await r.json()
            first = h1 if v["white"]["id"] == 1 else h2
            r = await client.post("/api/act", json={"id": gid, "action": "move", "uci": "e2e4"}, headers=first)
            assert (await r.json())["moves"] == ["e2e4"]
            r = await client.post("/api/act", json={"id": gid, "action": "move", "uci": "e2e4"}, headers=first)
            assert r.status == 400 and "соперника" in (await r.json())["error"]
            me = await (await client.get("/api/me", headers=h1)).json()
            assert me["me"]["faculty"] == "⛏ Геологии и геофизики" and [g["id"] for g in me["games"]] == [gid]
            assert me["searching"] == 0
            await client.post("/api/new", json={"kind": "search"}, headers=h2)
            assert (await (await client.get("/api/searching", headers=h1)).json()) == {"searching": 1}
            assert (await client.get("/api/searching")).status == 401
            r = await client.options("/api/act", headers={"Origin": "https://tawilone.github.io"})
            assert r.status == 200 and "X-Init-Data" in r.headers["Access-Control-Allow-Headers"]
        finally:
            await client.close()
    asyncio.run(go())


def test_bot_games_never_touch_rating_or_stats(store, monkeypatch):
    """Партия с ботом: бот отвечает сам, белыми ходит первым; итог не идёт ни в рейтинг, ни в статистику."""
    monkeypatch.setattr(games, "stockfish_path", lambda: None)  # без Stockfish — встроенный движок
    engine = games.Engine()
    for _ in range(6):  # цвет случайный — несколько партий, чтобы попались оба
        gid = store.create(1, "bot", 2)
        asyncio.run(games.bot_reply(store, engine, gid))
        v = store.view(1, gid)
        assert v["status"] == "play" and v["your_turn"]  # бот белыми уже сходил, иначе ход наш
        bot_side = v["black"] if v["you"] == "white" else v["white"]
        assert bot_side["bot"] and bot_side["rating"] is None and "Средний" in bot_side["name"]
        assert len(v["moves"]) == (1 if v["you"] == "black" else 0)
        store.move(1, gid, v["legal"][0])
        asyncio.run(games.bot_reply(store, engine, gid))
        v = store.view(1, gid)
        assert v["your_turn"] and len(v["moves"]) == (3 if v["you"] == "black" else 2)  # бот ответил
        with pytest.raises(games.GameError):
            store.draw(1, gid, "offer")  # на ничью бот не соглашается
        store.resign(1, gid)
        assert store.view(1, gid)["status"] == "over"
    me = store.player(1)
    assert (me["rating"], me["wins"], me["losses"], me["draws"]) == (1200, 0, 0, 0)
    assert store.top() == []  # в рейтинг вуза не попал
    with pytest.raises(games.GameError):
        store.create(1, "bot", 9)


def test_simple_engine_takes_mate_and_free_queen():
    import chess
    games.random.seed(1)
    mate = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")  # Rd8#
    assert games.simple_move(mate, 3).uci() == "d1d8"
    queen = chess.Board("4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1")  # ладья берёт незащищённого ферзя
    assert games.simple_move(queen, 3).uci() == "d1d5"


def test_http_bot_game(store, monkeypatch):
    monkeypatch.setattr(games, "stockfish_path", lambda: None)

    async def go():
        client = TestClient(TestServer(games.make_app(store, TOKEN)))
        await client.start_server()
        try:
            h = {"X-Init-Data": init_data(1)}
            v = await (await client.post("/api/new", json={"kind": "bot", "level": 1}, headers=h)).json()
            assert v["kind"] == "bot" and v["your_turn"]
            n = len(v["moves"])
            v = await (await client.post("/api/act", json={"id": v["id"], "action": "move", "uci": v["legal"][0]},
                                         headers=h)).json()
            assert len(v["moves"]) == n + 2 and v["your_turn"]  # наш ход и сразу ответ бота
        finally:
            await client.close()
    asyncio.run(go())


def test_avatar_from_telegram(store):
    """Аватарка — из подписанных данных Telegram (photo_url); не https — не сохраняем."""
    store.touch({"id": 1, "first_name": "Иван", "photo_url": "https://t.me/i/userpic/320/abc.jpg"})
    assert store.player(1)["photo"] == "https://t.me/i/userpic/320/abc.jpg"
    store.touch({"id": 2, "first_name": "Мария", "photo_url": "javascript:alert(1)"})
    assert store.player(2)["photo"] == ""
    store.touch({"id": 1, "first_name": "Иван"})  # убрал фото в Telegram — пропадёт и у нас
    assert store.player(1)["photo"] == ""


def test_leaving_the_game_loses_after_3_minutes(store, monkeypatch):
    """Вышел из мини-приложения и не вернулся за 3 минуты, а соперник на месте — поражение.
    Время, когда на месте нет никого, не считается. С ботом — не считается."""
    now = [time.time()]
    monkeypatch.setattr(games.time, "time", lambda: now[0])

    def wait(uid, gid, sec, lobby=False):  # uid сидит в мини-приложении sec секунд (опрос раз в 2 с)
        for _ in range(int(sec // 2)):
            now[0] += 2
            store.my_games(uid, here=True) if lobby else store.here(uid, gid)

    gid = store.create(1, "invite")
    store.join(2, gid)
    wait(1, gid, 60)
    assert store.view(1, gid)["opp_away"] == 60  # соперника нет минуту — показываем отсчёт
    wait(1, gid, 100, lobby=True)  # список игр — тоже «на месте»
    assert store.view(1, gid)["status"] == "play"  # 2:40 — ещё ждём
    wait(1, gid, 30)
    v = store.view(1, gid)  # 3:10 — ушедший проиграл
    assert v["status"] == "over" and v["reason"] == "abandon"
    assert v["result"] == ("0-1" if v["white"]["id"] == 2 else "1-0")
    assert store.player(2)["losses"] == 1 and store.player(1)["wins"] == 1

    gid = store.create(1, "invite")  # вышли оба — никому не засчитываем
    store.join(3, gid)
    now[0] += 3600
    wait(1, gid, 60)  # Иван вернулся через час: Олег не проиграл сразу, у него свежие 3 минуты
    assert store.view(1, gid)["status"] == "play" and store.view(1, gid)["opp_away"] == 58
    wait(3, gid, 10)  # Олег вернулся
    wait(1, gid, 300)  # теперь ушёл Олег, Иван на месте 5 минут
    assert store.view(1, gid)["reason"] == "abandon" and store.player(3)["losses"] == 1

    gid = store.create(1, "invite")  # друг принял приглашение, когда автор давно вышел из приложения
    now[0] += 3600
    store.join(2, gid)
    wait(2, gid, 600)
    assert store.view(2, gid)["status"] == "play"  # автор ещё не знает о партии — не проигрывает

    gid = store.create(1, "bot", 1)  # с ботом уход ничего не решает
    now[0] += 10 * 60
    assert store.view(1, gid)["status"] == "play"


def test_search_skips_players_who_left(store, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(games.time, "time", lambda: now[0])
    old = store.create(1, "search")
    now[0] += 3600  # Иван искал час назад и закрыл приложение
    store.touch({"id": 2, "first_name": "Мария"})
    new = store.create(2, "search")
    assert new != old and store.view(2, new)["status"] == "wait"
    assert store.searching(3) == 1 and store.searching(2) == 0  # Иван ушёл — его не считаем, себя тоже
    store.touch({"id": 3, "first_name": "Олег"})
    assert store.create(3, "search") == new  # Мария ищет прямо сейчас — к ней и садимся
    assert store.searching(3) == 0


def test_cancel_right_after_opponent_joined(store, monkeypatch):
    """Нажал «Отменить» поиск, а соперник подсел за секунду до этого: отмена срабатывает,
    подсевший снова ищет соперника. Если уже ходили или прошло время — отменить нельзя."""
    now = [time.time()]
    monkeypatch.setattr(games.time, "time", lambda: now[0])
    gid = store.create(1, "search")
    assert store.create(2, "search") == gid  # Мария подсела
    now[0] += 2
    store.cancel(1, gid)
    v = store.view(2, gid)
    assert v["status"] == "wait" and v["mine"] and v["kind"] == "search"  # Мария снова ищет
    assert not [g for g in store.my_games(1)]  # у Ивана партии нет
    assert store.create(3, "search") == gid  # к Марии подсел Олег
    with pytest.raises(games.GameError):
        store.cancel(1, gid)  # Иван к этой партии уже отношения не имеет

    gid = store.create(1, "invite")  # приглашение: друг открыл ссылку, а автор нажал «Отменить»
    store.join(2, gid)
    store.cancel(1, gid)
    with pytest.raises(games.GameError):
        store.view(2, gid)

    gid = store.create(1, "invite")  # прошло больше 15 с — партия идёт, отменить нельзя
    store.join(2, gid)
    now[0] += 30
    with pytest.raises(games.GameError):
        store.cancel(1, gid)
    assert store.view(1, gid)["status"] == "play"


def test_starting_another_game_drops_your_search(store):
    """Нажал «Найти соперника», вернулся и сел играть с ботом — поиск снимается, к нему никто не подсядет."""
    store.create(1, "search")
    assert store.searching(2) == 1
    store.create(1, "bot", 1)
    assert store.searching(2) == 0
    mine = store.create(2, "search")
    assert store.view(2, mine)["status"] == "wait"  # Марию ни к кому не посадили
    store.cancel(2, mine)

    store.create(1, "search")  # то же с приглашением друга и с переходом по чужой ссылке
    store.create(1, "invite")
    assert store.searching(3) == 0
    store.create(1, "search")
    store.join(1, store.create(3, "invite"))
    assert store.searching(2) == 0


def _daytime(monkeypatch, hour=13):
    """Часы «по Москве» — уведомления ночью не шлются."""
    from datetime import datetime
    now = [datetime(2026, 10, 24, hour, 0, tzinfo=games.MSK).timestamp()]
    monkeypatch.setattr(games.time, "time", lambda: now[0])
    return now


def test_notify_when_someone_searches(store, monkeypatch):
    """Включил «🔔 Сообщать, когда кто-то ищет игру» — бот напишет, когда кто-то начнёт поиск.
    Не пишем: самому ищущему, тем, кто сейчас в мини-приложении, ночью, без галочки,
    про того же ищущего чаще раза в 10 минут."""
    now = _daytime(monkeypatch)
    for uid in (1, 2, 3):
        store.touch({"id": uid, "first_name": "И"})
    store.set_notify(2, True)
    store.set_notify(3, True)
    assert store.notify_on(2) and not store.notify_on(1)
    now[0] += 60  # все трое вышли из приложения минуту назад
    store.touch({"id": 3, "first_name": "Олег"})  # Олег в приложении — он и так видит счётчик
    gid = store.create(1, "search")
    out = store.take_outbox()
    assert [(n["uid"], n["gid"]) for n in out] == [(2, gid)]
    assert out[0]["name"].startswith("И") and out[0]["faculty"] == "⛏ Геологии и геофизики"
    assert store.take_outbox() == []  # забрали — больше не отдаём

    store.cancel(1, gid)
    now[0] += 60
    gid = store.create(1, "search")
    # Иван жмёт «Найти»/«Отменить» по кругу — Марии про него повторно не пишем; Олегу (ещё не писали) — пишем
    assert [n["uid"] for n in store.take_outbox()] == [3]
    store.cancel(1, gid)
    store.touch({"id": 3, "first_name": "Олег"})
    now[0] += 60
    gid = store.create(3, "search")  # а вот ищет другой человек — Марии пишем сразу
    assert [n["uid"] for n in store.take_outbox()] == [2]
    store.cancel(3, gid)
    store.set_notify(3, False)

    now[0] += 10 * 60
    gid = store.create(1, "search")
    store.cancel(1, gid)  # успел отменить, пока бот не отправил — не шлём
    assert store.take_outbox() == []

    now[0] += 60
    store.set_notify(2, False)
    gid = store.create(1, "search")
    assert store.take_outbox() == []  # выключила
    store.cancel(1, gid)

    store.set_notify(2, True)
    _daytime(monkeypatch, hour=2)
    store.create(1, "search")
    assert store.take_outbox() == []  # ночью не будим


def test_http_notify_switch(store):
    async def go():
        client = TestClient(TestServer(games.make_app(store, TOKEN)))
        await client.start_server()
        try:
            h = {"X-Init-Data": init_data(1)}
            assert (await (await client.get("/api/me", headers=h)).json())["notify"] is False
            r = await client.post("/api/notify", json={"on": True}, headers=h)
            assert (await r.json()) == {"notify": True}
            assert (await (await client.get("/api/me", headers=h)).json())["notify"] is True
            assert (await client.post("/api/notify", json={"on": True})).status == 401
        finally:
            await client.close()
    asyncio.run(go())


def test_admin_stats_only_for_admin(store, tmp_path, monkeypatch):
    """Полная статистика бота (её пишет бот на /users) — только администратору, по подписи Telegram."""
    monkeypatch.setenv("ADMIN_ID", "1")

    async def go():
        client = TestClient(TestServer(games.make_app(store, TOKEN)))
        await client.start_server()
        try:
            admin, other = {"X-Init-Data": init_data(1)}, {"X-Init-Data": init_data(2)}
            assert (await client.get("/api/admin", headers=admin)).status == 404  # бот ещё не писал
            (tmp_path / "admin_stats.json").write_text('{"now": "x", "u": [["Иван"]]}', encoding="utf-8")
            r = await client.get("/api/admin", headers=admin)
            assert r.status == 200 and (await r.json())["u"] == [["Иван"]]
            assert (await client.get("/api/admin", headers=other)).status == 403
            assert (await client.get("/api/admin")).status == 401
        finally:
            await client.close()
    asyncio.run(go())
