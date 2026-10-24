@echo off
rem Remove sync from Windows autostart.
powershell -NoProfile -Command "Unregister-ScheduledTask -TaskName GubkinSync -Confirm:$false"
echo Sync removed from autostart.
pause
