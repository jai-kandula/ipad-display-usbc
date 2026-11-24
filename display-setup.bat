@echo off
rem One-time setup: lets the host turn the iPad's virtual screen on and off
rem (when it starts and stops, when the iPad is plugged in or out, and for
rem profiles like Gaming) without an admin prompt every time.
rem Run it again after updating the app. "display-setup.bat remove" undoes it.
title iPad Display - automatic screen setup
cd /d "%~dp0"

net session >nul 2>nul
if not errorlevel 1 goto admin
rem Not admin yet: note which account this is (the one the host runs as),
rem then ask Windows for admin rights and run this file again.
echo Asking Windows for admin rights...
if /i "%~1"=="remove" (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'remove' -Verb RunAs"
  exit /b
)
for /f %%s in ('powershell -NoProfile -Command "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"') do set "ME=%%s"
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%ME%' -Verb RunAs"
exit /b

:admin
if /i "%~1"=="remove" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0display-setup.ps1" -Remove
  pause
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0display-setup.ps1" -UserSid "%~1"
if errorlevel 1 ( echo. & echo Setting up failed ^(see above^). Send me that message. & pause & exit /b 1 )

echo.
echo Done. From now on the host turns the iPad's virtual screen on and off by itself.
echo.
echo Testing: turning the virtual screen off and on once...
schtasks /run /tn "iPad Display\Screen off" >nul
timeout /t 4 /nobreak >nul
schtasks /run /tn "iPad Display\Screen on" >nul
timeout /t 4 /nobreak >nul
type "%ProgramFiles%\iPad Display\vdd.log" 2>nul
pause
