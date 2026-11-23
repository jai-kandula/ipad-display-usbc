@echo off
rem One-time setup: lets start.bat turn the iPad's virtual screen on when it
rem starts and off when you close it, without an admin prompt every time.
rem Run it again if you move this folder. Run "display-setup.bat remove" to undo.
title iPad Display - automatic screen setup
cd /d "%~dp0"

net session >nul 2>nul
if errorlevel 1 (
  echo Asking Windows for admin rights...
  if "%~1"=="" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  ) else (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%~1' -Verb RunAs"
  )
  exit /b
)

if /i "%~1"=="remove" (
  schtasks /delete /tn "iPad Display\Screen on" /f >nul 2>nul
  schtasks /delete /tn "iPad Display\Screen off" /f >nul 2>nul
  echo Removed. The virtual screen now stays as you leave it in Device Manager.
  pause
  exit /b 0
)

set "PS=powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%~dp0vdd.ps1\""
rem "on" only ever runs when start.bat asks for it. "off" also runs at boot,
rem so the screen is gone after a restart even if the laptop crashed.
schtasks /create /tn "iPad Display\Screen on"  /tr "%PS% -Mode on"  /sc once /st 00:00 /ru SYSTEM /rl highest /f >nul 2>nul
if errorlevel 1 ( echo Could not create the task. & pause & exit /b 1 )
schtasks /create /tn "iPad Display\Screen off" /tr "%PS% -Mode off" /sc onstart /ru SYSTEM /rl highest /f >nul 2>nul
if errorlevel 1 ( echo Could not create the task. & pause & exit /b 1 )

echo.
echo Done. From now on:
echo   - start.bat turns the iPad's virtual screen ON
echo   - closing the start.bat window turns it OFF again
echo.
echo Testing: turning the virtual screen off and on once...
schtasks /run /tn "iPad Display\Screen off" >nul
timeout /t 4 /nobreak >nul
schtasks /run /tn "iPad Display\Screen on" >nul
timeout /t 4 /nobreak >nul
type "%~dp0vdd.log" 2>nul
pause
