@echo off
title iPad Display host
cd /d "%~dp0"

rem Prefer Python 3.15, fall back to any Python 3.10+.
set "PY="
py -3.15 --version >nul 2>nul && set "PY=py -3.15"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python is not installed. Install Python 3.15 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting things up, this takes a minute...
  %PY% -m venv .venv
  if errorlevel 1 ( pause & exit /b 1 )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 ( echo Could not install the required packages. & pause & exit /b 1 )
rem Optional: GPU screen capture (faster). Skipped quietly if it can't install.
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "dxcam>=0.3.0" >nul 2>nul
rem Optional: H.264 video for smooth scrolling and dragging (uses the graphics card).
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "av>=14" >nul 2>nul
rem Optional: encrypted Wi-Fi (the host's own certificate).
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "cryptography>=44" >nul 2>nul
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "sympy>=1.14" "tzdata" >nul 2>nul
rem Optional: PC sound on the iPad.
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "soundcard>=0.4.6" >nul 2>nul
rem Optional: the icon next to the clock.
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check "pystray>=0.19.5" >nul 2>nul

".venv\Scripts\python.exe" server.py %*
pause
