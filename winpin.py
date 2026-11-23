"""Put app windows on the iPad: move one there now, or pin an app so its
windows go to the iPad whenever they open (Discord, Spotify, notes...).
Pinned apps are saved in pins.json next to this file. Windows-only."""
import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"
HERE = os.path.dirname(os.path.abspath(__file__))
PINS_PATH = os.path.join(HERE, "pins.json")
GW_OWNER, GWL_EXSTYLE, WS_EX_TOOLWINDOW = 4, -20, 0x80
SW_RESTORE, SW_MAXIMIZE = 9, 3
SWP_NOZORDER, SWP_NOACTIVATE = 0x4, 0x10
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SKIP_TITLES = {"Program Manager", "Windows Input Experience", "Settings"}

target = {"mon": None}           # the screen being streamed to the iPad (set by the host)
_lock = threading.Lock()


def load_pins():
    try:
        with open(PINS_PATH, encoding="utf-8") as f:
            pins = json.load(f)
        return [p for p in pins if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def set_pin(exe, on):
    with _lock:
        pins = [p for p in load_pins() if p.lower() != exe.lower()]
        if on:
            pins.append(exe)
        with open(PINS_PATH, "w", encoding="utf-8") as f:
            json.dump(pins, f)
    return pins


def _exe(hwnd):
    k32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value)
        return ""
    finally:
        k32.CloseHandle(h)


def list_windows():
    """Top-level app windows: [{hwnd, title, exe, here}] (here = on the iPad)."""
    if not IS_WINDOWS:
        return []
    user32 = ctypes.windll.user32
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindow.restype = ctypes.c_void_p
    dwm = ctypes.windll.dwmapi
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = ctypes.c_int(0)
        dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value in SKIP_TITLES:
            return True
        out.append({"hwnd": int(hwnd), "title": buf.value, "exe": _exe(hwnd), "here": _on(hwnd, target["mon"])})
        return True

    user32.EnumWindows(cb, 0)
    return out


def _on(hwnd, mon):
    if not mon:
        return False
    r = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    return mon["left"] <= cx < mon["left"] + mon["width"] and mon["top"] <= cy < mon["top"] + mon["height"]


def move_here(hwnd, mon=None):
    """Move a window to the iPad's screen and maximize it there."""
    mon = mon or target["mon"]
    if not IS_WINDOWS or not mon:
        return False
    user32 = ctypes.windll.user32
    hwnd = wintypes.HWND(hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)
    w, h = max(400, mon["width"] - 120), max(300, mon["height"] - 120)
    user32.SetWindowPos(hwnd, None, mon["left"] + 60, mon["top"] + 60, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    user32.ShowWindow(hwnd, SW_MAXIMIZE)
    return True


def _watch():
    """Every 2 s while the iPad is showing a screen: move newly opened
    windows of pinned apps there. Each window is moved once, so you can
    still drag it back to the laptop."""
    seen, last = set(), None
    while True:
        time.sleep(2.0)
        mon, pins = target["mon"], {p.lower() for p in load_pins()}
        if mon != last:              # the iPad (re)connected: bring pinned apps over again
            seen.clear()
            last = mon
        if not mon or not pins:
            continue
        try:
            for w in list_windows():
                if w["hwnd"] in seen:
                    continue
                seen.add(w["hwnd"])
                if w["exe"].lower() in pins and not w["here"]:
                    move_here(w["hwnd"], mon)
                    print(f"[apps] moved {w['exe']} to the iPad", flush=True)
        except Exception as e:
            print("[apps] error:", e, flush=True)
        if len(seen) > 5000:
            seen.clear()


def start():
    if IS_WINDOWS:
        threading.Thread(target=_watch, name="pins", daemon=True).start()
