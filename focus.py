"""Which app is in front on the PC, so a profile's buttons can follow it
(Photoshop, OBS, a game...). Windows-only; elsewhere nothing is ever in front."""
import ctypes
import sys
import threading
import time

IS_WINDOWS = sys.platform == "win32"
state = {"exe": "", "version": 0}
recent = []                        # last distinct apps in front, newest first
_started = False


def _front_exe():
    import winpin
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return winpin._exe(hwnd).lower() if hwnd else ""


def _watch():
    while True:
        try:
            exe = _front_exe()
        except Exception:
            exe = ""
        if exe and exe != state["exe"] and exe not in ("explorer.exe", "searchhost.exe",
                                                       "shellexperiencehost.exe", "pythonw.exe",
                                                       "python.exe", "startmenuexperiencehost.exe"):
            state["exe"] = exe
            state["version"] += 1
            if exe in recent:
                recent.remove(exe)
            recent.insert(0, exe)
            del recent[16:]
        time.sleep(0.4)


def start():
    global _started
    if IS_WINDOWS and not _started:
        _started = True
        threading.Thread(target=_watch, daemon=True, name="focus").start()


def match(profiles):
    """The profile whose apps list has the app in front, or None."""
    exe = state["exe"]
    if not exe:
        return None
    for p in profiles:
        apps = [str(a).lower().strip() for a in p.get("apps", []) if str(a).strip()]
        if exe in apps or any(a and not a.endswith(".exe") and a in exe for a in apps):
            return p["name"]
    return None
