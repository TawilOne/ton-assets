#!/usr/bin/env python3
"""Превращает VPN-ключ (vless://, vmess://, trojan://, ss://, hysteria2://) или ссылку-подписку
(https://...) в конфиг sing-box: локальный прокси 127.0.0.1:1080, через который бот ходит в Telegram.

    python3 vpnkey2singbox.py 'vless://...'  > /etc/gubkin-bot/sing-box.json
"""
import base64
import json
import sys
import urllib.parse
import urllib.request

LISTEN_PORT = 1080


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
    return None  # tcp


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


def links_from(arg: str) -> list[str]:
    if arg.startswith(("http://", "https://")):     # ссылка-подписка: список ключей (часто в base64)
        req = urllib.request.Request(arg, headers={"User-Agent": "sing-box"})
        text = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace").strip()
        if "://" not in text:
            text = b64decode(text)
        return [line.strip() for line in text.splitlines() if "://" in line]
    return [arg]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    errors = []
    for link in links_from(sys.argv[1]):
        try:
            outbound = parse(link)
            break
        except Exception as e:  # пробуем следующий ключ из подписки
            errors.append(f"{link[:30]}…: {e}")
    else:
        sys.exit("Не удалось разобрать ключ:\n" + "\n".join(errors))
    outbound["tag"] = "proxy"
    config = {
        "log": {"level": "warn"},
        "inbounds": [{"type": "mixed", "tag": "in", "listen": "127.0.0.1", "listen_port": LISTEN_PORT}],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
    }
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
