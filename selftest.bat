@echo off
title iPad Display self-test
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once first, so everything is installed.
  pause
  exit /b 1
)
echo Keep start.bat running and the iPad plugged in, showing the screen.
echo This takes about two minutes. Don't use the PC while it runs.
echo.
".venv\Scripts\python.exe" selftest.py %*
echo.
echo The report is in selftest-report.txt in this folder.
pause
