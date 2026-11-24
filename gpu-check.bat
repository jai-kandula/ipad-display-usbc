@echo off
title iPad Display GPU check
cd /d "%~dp0"
rem ==========================================================================
rem  Shows what is using each graphics card (NVIDIA and Intel apart), by
rem  program and by engine, with memory on the card and shared memory apart.
rem  It only reads Windows' GPU counters; it changes nothing.
rem ==========================================================================
if not exist ".venv\Scripts\python.exe" (
  echo Put this file in your iPad Display folder, next to start.bat, and run
  echo start.bat once first.
  pause
  exit /b 1
)
rem Get the newest checker (keeps the one here if there's no internet).
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest 'https://raw.githubusercontent.com/jai-kandula/ipad-display-usbc/main/gpucheck.py' -OutFile 'gpucheck.py.new' -UseBasicParsing; Move-Item -Force 'gpucheck.py.new' 'gpucheck.py' } catch { Remove-Item 'gpucheck.py.new' -ErrorAction SilentlyContinue }"
if not exist "gpucheck.py" (
  echo Couldn't download the checker. Check the internet and try again.
  pause
  exit /b 1
)
echo.
echo  Set the PC up the way that shows the problem: start.bat running, the
echo  iPad connected, and your game running if that's when it happens.
echo.
echo  Press a key, then switch to the game. Measuring starts 10 seconds later
echo  and takes 60 seconds. A beep means it's done.
pause >nul
set "PYTHONIOENCODING=utf-8"
".venv\Scripts\python.exe" gpucheck.py 60 --wait 10
start "" explorer "%CD%\gpu-check"
echo.
echo  Send me the newest file from the gpu-check folder.
pause
