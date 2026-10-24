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
