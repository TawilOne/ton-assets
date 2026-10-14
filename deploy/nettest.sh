#!/usr/bin/env bash
# Проверяет, какие бесплатные «пересыльщики» для Telegram доступны с сервера.
# 200 КБ с Cloudflare — заодно ловим обрыв больших ответов (в РФ Cloudflare иногда режут после 16 КБ).
t() { printf '%-28s %s\n' "$1" "$(curl -s -o /dev/null -w '%{http_code}, скачано %{size_download} байт за %{time_total} с' --max-time 15 "$2" || true)"; }
t "Cloudflare (200 КБ)"   "https://speed.cloudflare.com/__down?bytes=200000"
t "Cloudflare Workers"    "https://workers.dev"
t "Deno Deploy"           "https://deno.com"
t "Vercel"                "https://vercel.com"
t "Netlify"               "https://www.netlify.com"
t "Telegram напрямую"     "https://api.telegram.org"
