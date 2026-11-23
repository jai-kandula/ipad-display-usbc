@echo off
rem Run the iPad Display host automatically in the background whenever you
rem log in to Windows, so you only have to plug in the iPad and open the app.
rem Run "autostart-setup.bat remove" to turn it off again.
title iPad Display - auto-start
cd /d "%~dp0"
if /i "%~1"=="remove" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Mode off
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Mode on
  echo.
  echo  - The host now starts by itself when you log in. There's no window: its
  echo    messages go to host.log in this folder.
  echo  - Plug in the iPad and open the iPad Display app. If you ran
  echo    display-setup.bat, the virtual screen turns on while the iPad is
  echo    plugged in and off when you unplug it.
  echo  - You don't need start.bat any more. To stop auto-start, run
  echo    "autostart-setup.bat remove".
)
pause
