@echo off
rem Download EVERYTHING at once (all weeks, all groups). Slow on purpose: pauses when the site asks for a break.
rem Everything is inside ( ) so cmd reads it before git pull can rewrite this file.
cd /d "%~dp0"
(
  git pull -q && echo Code is up to date.
  python sync.py --interactive --full
  pause
  exit /b
)
