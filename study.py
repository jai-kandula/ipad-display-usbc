"""Study mode: a focus timer on the iPad (25 minutes of work, 5 of break, or
your own lengths), and while you focus the PC keeps distractions away: if an
app on your block list (Discord, Steam, a game...) or a browser tab whose
title has a blocked word (YouTube, Twitch, Reddit...) comes to the front, it
is minimised straight away and the iPad counts it.

Nothing is watched outside a focus block. The lists are edited on the iPad
and kept in settings.json."""
import ctypes
import sys
import threading
import time

import hostsettings

IS_WINDOWS = sys.platform == "win32"
hostsettings.register({
    "study_apps": ["discord.exe", "steam.exe", "epicgameslauncher.exe", "rainbowsix.exe", "rainbowsix_be.exe",
                   "minecraftlauncher.exe"],
    "study_titles": ["YouTube", "Twitch", "Reddit", "Instagram", "TikTok", "Netflix", "Discord"],
    "study_focus": 25, "study_break": 5,
})
SW_MINIMIZE = 6
BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "opera_gx.exe", "vivaldi.exe", "arc.exe")

_lock = threading.Lock()
S = {"phase": "off", "ends": 0.0, "started": 0.0, "focus": 25, "brk": 5, "blocked": 0, "last": "",
     "done": 0, "paused_left": None, "gen": 0}


def _title(hwnd):
    n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _check_front():
    import winpin
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return
    exe = winpin._exe(hwnd).lower()
    apps = {a.lower().strip() for a in hostsettings.get("study_apps") or [] if str(a).strip()}
    hit = exe if exe in apps else ""
    if not hit and exe in BROWSERS:
        title = _title(hwnd)
        for w in hostsettings.get("study_titles") or []:
            if w and str(w).lower() in title.lower():
                hit = str(w).strip()
                break
    if hit:
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        with _lock:
            S["blocked"] += 1
            S["last"] = hit


def _run(gen):
    """Watches the app in front during focus, and moves focus -> break -> off."""
    while S["gen"] == gen and S["phase"] != "off":
        now = time.time()
        if S["phase"] in ("focus", "break") and now >= S["ends"]:
            with _lock:
                if S["phase"] == "focus":
                    S["done"] += 1
                    S.update(phase="break", started=now, ends=now + S["brk"] * 60)
                else:
                    S.update(phase="off", ends=0.0)
            continue
        if S["phase"] == "focus" and IS_WINDOWS:
            try:
                _check_front()
            except Exception as e:
                print("[study] couldn't check the app in front:", e, flush=True)
        time.sleep(0.5)


def start(focus=None, brk=None):
    focus = max(1, min(180, int(focus or hostsettings.get("study_focus") or 25)))
    brk = max(1, min(60, int(brk or hostsettings.get("study_break") or 5)))
    now = time.time()
    with _lock:
        S["gen"] += 1
        S.update(phase="focus", started=now, ends=now + focus * 60, focus=focus, brk=brk,
                 blocked=0, last="", paused_left=None)
        gen = S["gen"]
    threading.Thread(target=_run, args=(gen,), daemon=True, name="study").start()
    return state()


def stop():
    with _lock:
        S["gen"] += 1
        S.update(phase="off", ends=0.0, paused_left=None)
    return state()


def pause():
    """Pause or resume the timer (blocking pauses too)."""
    with _lock:
        if S["phase"] in ("focus", "break"):
            S["paused_left"] = (S["phase"], max(0.0, S["ends"] - time.time()))
            S["phase"] = "paused"
        elif S["phase"] == "paused" and S["paused_left"]:
            ph, left = S["paused_left"]
            S.update(phase=ph, ends=time.time() + left, paused_left=None)
            S["gen"] += 1
            threading.Thread(target=_run, args=(S["gen"],), daemon=True, name="study").start()
    return state()


def skip():
    """End this focus block or break now."""
    with _lock:
        if S["phase"] in ("focus", "break"):
            S["ends"] = time.time()
    time.sleep(0.6)
    return state()


def state():
    with _lock:
        ph = S["phase"]
        left = S["paused_left"][1] if ph == "paused" and S["paused_left"] else max(0.0, S["ends"] - time.time())
        total = (S["focus"] if (ph == "focus" or (ph == "paused" and S["paused_left"] and S["paused_left"][0] == "focus"))
                 else S["brk"]) * 60
        return {"phase": ph, "left": round(left) if ph != "off" else 0, "total": total, "focus": S["focus"],
                "break": S["brk"], "blocked": S["blocked"], "last": S["last"], "done": S["done"],
                "paused_in": S["paused_left"][0] if ph == "paused" and S["paused_left"] else None}
