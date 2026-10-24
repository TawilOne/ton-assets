@echo off
rem One-time setup: key for the server, server offline mode, Windows autostart.
rem Everything is inside ( ) so cmd reads it before git pull can rewrite this file.
cd /d "%~dp0"
(
  git pull -q
  python sync.py --setup
  pause
  exit /b
)
