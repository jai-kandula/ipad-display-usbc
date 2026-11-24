"""Turn the iPad's virtual screen (Virtual Display Driver) on while the host
runs and off when it stops, including when the window is closed with the X.

Switching a display adapter needs admin rights, so display-setup.bat creates
two scheduled tasks once (as admin, running as SYSTEM) and this module just
starts them. Windows hides SYSTEM tasks from normal programs and refuses to
start them, so display-setup.bat (display-setup.ps1) also lets your account
see and run those two tasks. Without that, nothing changes."""
import atexit
import ctypes
import subprocess
import sys
import time

TASK_ON = r"iPad Display\Screen on"
TASK_OFF = r"iPad Display\Screen off"
_NO_WINDOW = 0x08000000      # CREATE_NO_WINDOW
_handler_ref = None
_turned_on = False


def _schtasks(*args):
    try:
        return subprocess.run(["schtasks", *args], capture_output=True,
                              timeout=10, creationflags=_NO_WINDOW).returncode == 0
    except Exception:
        return False


def available():
    return sys.platform == "win32" and _schtasks("/query", "/tn", TASK_ON)


def is_on():
    """True if the Virtual Display Driver is enabled (reading this needs no admin)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-PnpDevice -Class Display | Where-Object FriendlyName -like "
             "'*Virtual Display*' | Select-Object -First 1).Status"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW).stdout
        return out.strip().upper() == "OK"
    except Exception:
        return False


def turn_on(count_screens, wait=10.0):
    """Start the 'on' task and wait (up to `wait` s) for a new screen to appear.
    count_screens() returns how many screens Windows currently has.
    Returns "already", "on" or "failed"."""
    global _turned_on
    _install_off_hooks()
    if is_on():
        _turned_on = True            # still switch it off when the host closes
        return "already"
    before = count_screens()
    if not _schtasks("/run", "/tn", TASK_ON):
        return "failed"
    _turned_on = True
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.5)
        if count_screens() > before:
            time.sleep(1.0)          # let Windows finish placing it
            return "on"
    return "on" if count_screens() > before else "failed"


def turn_off(force=False):
    """Turn the screen off if this host turned it on (or always, with force:
    a profile that doesn't use the iPad as a screen, e.g. while gaming)."""
    global _turned_on
    if _turned_on or (force and is_on()):
        _turned_on = False
        _schtasks("/run", "/tn", TASK_OFF)


def _install_off_hooks():
    """Turn off on normal exit, Ctrl+C, closing the window, log off or shutdown."""
    global _handler_ref
    if _handler_ref is not None:
        return
    atexit.register(turn_off)
    HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

    def handler(event):
        # 0 Ctrl+C, 1 Ctrl+Break, 2 window closed, 5 log off, 6 shutdown.
        if event in (2, 5, 6):
            turn_off()               # the process is killed right after this returns
        return 0                     # let Python's own Ctrl+C handling run too
    _handler_ref = HandlerRoutine(handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_ref, 1)
