#!/usr/bin/env bash
# Проверка бота на сервере — всё на одном экране, токен скрыт:  bash /opt/gubkin-bot/deploy/doctor.sh
P=http://127.0.0.1:1080
hide() { sed -E 's/bot[0-9]+:[A-Za-z0-9_-]+/bot***/g' | cut -c1-160; }
t() { curl -s -o /dev/null -w "%{http_code} за %{time_total} с" --max-time 15 "$@" || true; }
echo "== Службы";        systemctl is-active gubkin-proxy gubkin-bot | paste -sd' '
echo "== Telegram через прокси: $(t -x $P https://api.telegram.org)  $(t -x $P https://api.telegram.org)  $(t -x $P https://api.telegram.org)"
echo "== Сайт Губкина напрямую: $(t https://lk.gubkin.ru/schedule/)"
echo "== Сайт Губкина через прокси: $(t -x $P https://lk.gubkin.ru/schedule/)"
echo "== Прокси (последние строки)"; journalctl -u gubkin-proxy -n 8 --no-pager -o cat | hide
echo "== Бот (последние строки)";    journalctl -u gubkin-bot -n 15 --no-pager -o short-iso | hide
