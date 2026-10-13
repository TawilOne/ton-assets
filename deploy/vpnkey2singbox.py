#!/usr/bin/env python3
"""Превращает VPN-ключ (vless://, vmess://, trojan://, ss://, hysteria2://) или ссылку-подписку
(https://...) в конфиг sing-box: локальный прокси 127.0.0.1:1080, через который бот ходит в Telegram.

    python3 vpnkey2singbox.py 'vless://...'  > /etc/gubkin-bot/sing-box.json
    python3 vpnkey2singbox.py --probe 'https://...'   # на сервере: проверить каждый сервер подписки
"""
import base64
import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

LISTEN_PORT = 1080
SING_BOX = os.environ.get("SING_BOX", "/usr/local/bin/sing-box")


def b64decode(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "replace")


def tls_block(q: dict, default_sni: str, force=False) -> dict | None:
    security = q.get("security", "tls" if force else "")
    if security not in ("tls", "reality", "xtls") and not force:
        return None
    tls = {"enabled": True, "server_name": q.get("sni") or q.get("peer") or q.get("host") or default_sni}
    if q.get("alpn"):
        tls["alpn"] = q["alpn"].split(",")
    if q.get("allowInsecure") in ("1", "true") or q.get("insecure") in ("1", "true"):
        tls["insecure"] = True
    fp = q.get("fp")
    if fp or security == "reality":
        tls["utls"] = {"enabled": True, "fingerprint": fp or "chrome"}
    if security == "reality":
        tls["reality"] = {"enabled": True, "public_key": q.get("pbk", ""), "short_id": q.get("sid", "")}
    return tls


def transport_block(q: dict) -> dict | None:
    net = q.get("type") or q.get("net") or "tcp"
    if net == "ws":
        t = {"type": "ws", "path": q.get("path") or "/"}
        if q.get("host"):
            t["headers"] = {"Host": q["host"]}
        return t
    if net == "grpc":
        return {"type": "grpc", "service_name": q.get("serviceName") or q.get("path") or ""}
    if net in ("http", "h2"):
        t = {"type": "http", "path": q.get("path") or "/"}
        if q.get("host"):
            t["host"] = q["host"].split(",")
        return t
    if net in ("httpupgrade",):
        return {"type": "httpupgrade", "path": q.get("path") or "/", "host": q.get("host", "")}
    if net in ("tcp", "raw", "none", ""):
        return None
    raise ValueError(f"транспорт {net} не поддерживается sing-box")


def parse(link: str) -> dict:
    link = link.strip()
    scheme = link.split("://", 1)[0].lower()

    if scheme == "vmess":
        j = json.loads(b64decode(link[8:]))
        q = {k: str(v) for k, v in j.items()}
        out = {"type": "vmess", "server": q["add"], "server_port": int(q["port"]), "uuid": q["id"],
               "alter_id": int(q.get("aid") or 0), "security": q.get("scy") or "auto"}
        if q.get("tls") == "tls":
            out["tls"] = tls_block({**q, "security": "tls"}, q["add"])
        t = transport_block({"type": q.get("net"), "path": q.get("path"), "host": q.get("host"),
                             "serviceName": q.get("path")})
        if t:
            out["transport"] = t
        return out

    if scheme == "ss":
        body = link[5:].split("#", 1)[0]
        if "@" not in body:                       # ss://base64(method:password@host:port)
            body = b64decode(body.split("?", 1)[0])
        userinfo, hostport = body.rsplit("@", 1)
        if ":" not in userinfo:                   # ss://base64(method:password)@host:port
            userinfo = b64decode(urllib.parse.unquote(userinfo))
        method, password = userinfo.split(":", 1)
        host, port = hostport.split("?", 1)[0].rsplit(":", 1)
        return {"type": "shadowsocks", "server": host.strip("[]"), "server_port": int(port),
                "method": method, "password": password}

    u = urllib.parse.urlsplit(link)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    host, port = u.hostname, u.port or 443
    user = urllib.parse.unquote(u.username or "")

    if scheme == "vless":
        out = {"type": "vless", "server": host, "server_port": port, "uuid": user}
        if q.get("flow"):
            out["flow"] = q["flow"]
        tls = tls_block(q, host)
        if tls:
            out["tls"] = tls
        t = transport_block(q)
        if t:
            out["transport"] = t
        return out
    if scheme == "trojan":
        out = {"type": "trojan", "server": host, "server_port": port, "password": user,
               "tls": tls_block(q, host, force=True)}
        t = transport_block(q)
        if t:
            out["transport"] = t
        return out
    if scheme in ("hysteria2", "hy2"):
        return {"type": "hysteria2", "server": host, "server_port": port, "password": user,
                "tls": tls_block(q, host, force=True)}
    raise ValueError(f"неизвестный тип ключа: {scheme}://")


SUPPORTED = {"vless", "vmess", "trojan", "shadowsocks", "hysteria2", "tuic"}


def links_from(arg: str) -> list:
    """Ключи из аргумента: одна ссылка или подписка (список ссылок, base64 или готовый конфиг sing-box)."""
    if not arg.startswith(("http://", "https://")):
        return [arg]
    # «v2rayNG» — чтобы панель отдала обычный список ключей, а не конфиг под конкретное приложение
    req = urllib.request.Request(arg, headers={"User-Agent": "v2rayNG/1.8.19"})
    text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace").strip()
    if text.startswith("{"):                        # подписка сразу в формате sing-box
        return [o for o in json.loads(text).get("outbounds", []) if o.get("type") in SUPPORTED]
    if "://" not in text:
        text = b64decode(text)
    return [line.strip() for line in text.splitlines() if "://" in line]


def usable(o: dict) -> bool:
    """Отсекаем «информационные» записи подписки (0.0.0.0, порт 1 и т.п.) и транспорты, которых нет в sing-box."""
    if o.get("server") in (None, "", "0.0.0.0", "127.0.0.1", "localhost") or int(o.get("server_port") or 0) < 2:
        return False
    return (o.get("transport") or {}).get("type") not in ("xhttp", "splithttp")  # для готовых конфигов


def key_name(link) -> str:
    if isinstance(link, dict):
        return str(link.get("tag", ""))
    if link.startswith("vmess://"):
        try:
            return json.loads(b64decode(link[8:])).get("ps", "")
        except ValueError:
            return ""
    return urllib.parse.unquote(link.split("#", 1)[1]) if "#" in link else ""


def curl(url: str, port: int | None = None) -> str:
    cmd = ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "10", url]
    if port:
        cmd += ["-x", f"http://127.0.0.1:{port}"]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip() or "000"


def probe_one(i: int, o: dict) -> tuple[bool, bool]:
    """Поднимает sing-box только с этим сервером и проверяет Telegram (3 раза подряд) и сайт Губкина."""
    port = 21000 + i
    cfg = {"log": {"level": "error"}, "outbounds": [dict(o, tag="x")],
           "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
    p = subprocess.Popen([SING_BOX, "run", "-c", f.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        tg = all(curl("https://api.telegram.org", port) != "000" for _ in range(3))
        gk = curl("https://lk.gubkin.ru/schedule/", port) == "200"
    finally:
        p.terminate()
        p.wait()
        os.unlink(f.name)
    return tg, gk


def probe(outbounds: list, names: list) -> tuple[list, list, bool]:
    """Какие серверы пускают в Telegram, какие — на сайт Губкина (и пускает ли он напрямую)."""
    print("Проверяю серверы подписки (до пары минут)...", file=sys.stderr)
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        res = list(ex.map(probe_one, range(len(outbounds)), outbounds))
    direct = curl("https://lk.gubkin.ru/schedule/") == "200"
    for (tg, gk), o, name in zip(res, outbounds, names):
        print(f"  {'✅' if tg else '❌'} Telegram  {'✅' if gk else '❌'} Губкин   {name or o['server']}",
              file=sys.stderr)
    print(f"  {'✅' if direct else '❌'} Губкин напрямую", file=sys.stderr)
    return [r[0] for r in res], [r[1] for r in res], direct


def main():
    args = [a for a in sys.argv[1:] if a != "--probe"]
    if len(args) != 1:
        sys.exit(__doc__)
    outbounds, names, errors, seen = [], [], [], set()
    for link in links_from(args[0]):
        try:
            o = dict(link) if isinstance(link, dict) else parse(link)
        except Exception as e:
            errors.append(f"{str(link)[:30]}…: {e}")
            continue
        o.pop("tag", None)
        key = json.dumps(o, sort_keys=True)
        if usable(o) and key not in seen:
            seen.add(key)
            outbounds.append(o)
            names.append(key_name(link))
    if not outbounds:
        sys.exit("Не удалось разобрать ключ:\n" + "\n".join(errors))
    for i, o in enumerate(outbounds):
        o["tag"] = f"key{i + 1}"
    tags = [o["tag"] for o in outbounds]
    tg_tags, gk_tags = tags, ["direct", *tags]
    if "--probe" in sys.argv:  # на сервере: заранее отсеиваем серверы, которые не пускают куда нужно
        tg_ok, gk_ok, direct = probe(outbounds, names)
        tg_tags = [t for t, ok in zip(tags, tg_ok) if ok] or tags
        gk_tags = (["direct"] if direct else []) + [t for t, ok in zip(tags, gk_ok) if ok] or gk_tags
        if not any(tg_ok):
            print("⚠️  Ни один сервер подписки не пустил в Telegram — оставляю все.", file=sys.stderr)
    # urltest сам проверяет серверы и ходит через тот, что отвечает быстрее
    group = {"type": "urltest", "tag": "proxy", "outbounds": tg_tags,
             "url": "https://api.telegram.org", "interval": "3m", "tolerance": 100}
    # сайт Губкина не пускает серверы хостингов — ходим к нему тем путём, где он открывается
    gubkin = {"type": "urltest", "tag": "gubkin", "outbounds": gk_tags,
              "url": "https://lk.gubkin.ru/schedule/", "interval": "10m", "tolerance": 300}
    print(f"Серверов: {len(outbounds)}, для Telegram: {len(tg_tags)}, для сайта Губкина: {len(gk_tags)}",
          file=sys.stderr)
    config = {
        "log": {"level": "warn"},
        "inbounds": [{"type": "mixed", "tag": "in", "listen": "127.0.0.1", "listen_port": LISTEN_PORT}],
        "outbounds": [group, gubkin, *outbounds, {"type": "direct", "tag": "direct"}],
        "route": {"rules": [{"domain_suffix": ["gubkin.ru"], "outbound": "gubkin"}], "final": "proxy"},
    }
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
