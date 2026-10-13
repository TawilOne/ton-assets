#!/usr/bin/env bash
# Установка бота расписания на сервер (Ubuntu/Debian). Запуск от root:
#   curl -fsSL https://raw.githubusercontent.com/TawilOne/ton-assets/claude/rgu-gubkina-schedule-bot-snqhke/deploy/install.sh | bash
# Повторный запуск безопасен: уже введённые токен и ключ не спрашиваются.
set -euo pipefail

REPO=https://github.com/TawilOne/ton-assets.git
BRANCH=claude/rgu-gubkina-schedule-bot-snqhke
DIR=/opt/gubkin-bot
BOT=$DIR/gubkin_schedule_bot
SB_VER=1.12.12

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
ask() { local v; read -r -p "$1" v </dev/tty; printf '%s' "$v"; }

[ "$(id -u)" = 0 ] || { echo "Запустите от root (или через sudo)."; exit 1; }

say "Ставлю нужные программы"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv curl ca-certificates >/dev/null

say "Скачиваю бота"
id gubkin >/dev/null 2>&1 || useradd -r -m -d /var/lib/gubkin -s /usr/sbin/nologin gubkin
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --depth 50 -b "$BRANCH" "$REPO" "$DIR"
fi
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q --upgrade pip
"$DIR/venv/bin/pip" install -q -r "$BOT/requirements.txt"

say "Настройки бота"
if [ ! -s "$BOT/token.txt" ]; then
  ask "Токен бота от @BotFather: " > "$BOT/token.txt"; echo
fi
if [ ! -s "$BOT/admin.txt" ]; then
  A=$(ask "Ваш id в Telegram для /users (узнать — /myid в боте; Enter — пропустить): "); echo
  [ -n "$A" ] && printf '%s' "$A" > "$BOT/admin.txt"
fi
chown -R gubkin: "$BOT"
chmod 600 "$BOT/token.txt"

say "Ставлю прокси для Telegram (sing-box $SB_VER)"
case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; *) echo "Неизвестная архитектура"; exit 1 ;; esac
if ! /usr/local/bin/sing-box version 2>/dev/null | grep -q "$SB_VER"; then
  curl -fsSL -o /tmp/sb.tgz "https://github.com/SagerNet/sing-box/releases/download/v$SB_VER/sing-box-$SB_VER-linux-$ARCH.tar.gz"
  tar xzf /tmp/sb.tgz -C /tmp
  install -m 755 "/tmp/sing-box-$SB_VER-linux-$ARCH/sing-box" /usr/local/bin/sing-box
fi
mkdir -p /etc/gubkin-bot
while [ ! -s /etc/gubkin-bot/sing-box.json ]; do
  K=$(ask "VPN-ключ или ссылка-подписка (vless://..., vmess://..., ss://..., https://...): "); echo
  if python3 "$DIR/deploy/vpnkey2singbox.py" "$K" > /tmp/sb.json && /usr/local/bin/sing-box check -c /tmp/sb.json; then
    install -m 644 /tmp/sb.json /etc/gubkin-bot/sing-box.json
  else
    echo "Ключ не подошёл — проверьте, что скопировали его целиком, и попробуйте ещё раз."
  fi
  rm -f /tmp/sb.json
done

say "Запускаю службы"
install -m 644 "$DIR/deploy/gubkin-proxy.service" "$DIR/deploy/gubkin-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable -q gubkin-proxy gubkin-bot
systemctl restart gubkin-proxy
code=000
for i in 1 2 3 4 5 6; do   # прокси нужно несколько секунд, чтобы проверить серверы подписки
  sleep 5
  code=$(curl -s -o /dev/null -w '%{http_code}' -x http://127.0.0.1:1080 --max-time 15 https://api.telegram.org || true)
  [ "$code" != "000" ] && break
done
if [ "$code" = "000" ]; then
  echo "⚠️  Через VPN-ключ Telegram не открывается. Журнал прокси:"
  journalctl -u gubkin-proxy -n 15 --no-pager || true
  echo "Ввести ключ заново: rm /etc/gubkin-bot/sing-box.json и запустите установку снова."
else
  echo "Telegram через прокси доступен (код $code)."
fi
systemctl restart gubkin-bot
sleep 5
systemctl --no-pager --lines=8 status gubkin-bot || true

say "Готово! Бот работает круглосуточно."
echo "  Логи бота:        journalctl -u gubkin-bot -f"
echo "  Обновить бота:    bash $DIR/deploy/update.sh"
echo "  Перезапустить:    systemctl restart gubkin-bot"
echo "Не забудьте остановить бота на ноутбуке — два бота с одним токеном мешают друг другу."
