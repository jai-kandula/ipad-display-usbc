"""Profiles and the button deck.

A profile says what the iPad is for right now: a second screen (streamed),
a full-screen button deck with nothing streamed (near-zero cost, e.g. while
gaming), or both (a screen with a strip of buttons). Profiles and their
buttons live in profiles.json next to this file and are edited from the iPad.

A button runs one action:
  {"keys": "ctrl+shift+m"}       press a key combination
  {"text": "gg wp"}              type text
  {"open": "obs64.exe" | "https://..." | "C:\\path\\app.exe"}   start something
  {"profile": "Gaming"}          switch profile
"""
import copy
import json
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "profiles.json")

DEFAULT = {
    "active": "Work",
    "profiles": [
        {"name": "Work", "icon": "💼", "screen": True, "stream": True, "strip": True,
         "cols": 4, "buttons": [
            {"label": "Copy", "icon": "📋", "keys": "ctrl+c"},
            {"label": "Paste", "icon": "📥", "keys": "ctrl+v"},
            {"label": "Undo", "icon": "↩️", "keys": "ctrl+z"},
            {"label": "Screenshot", "icon": "📸", "keys": "win+shift+s"},
            {"label": "Task view", "icon": "🗂️", "keys": "win+tab"},
            {"label": "Desktop", "icon": "🖥️", "keys": "win+d"},
            {"label": "Mute mic", "icon": "🎙️", "keys": "ctrl+shift+m"},
            {"label": "Play / pause", "icon": "⏯️", "keys": "playpause"},
            {"label": "Gaming", "icon": "🎮", "profile": "Gaming"},
         ]},
        {"name": "Gaming", "icon": "🎮", "screen": False, "stream": False, "strip": False,
         "cols": 4, "buttons": [
            {"label": "Mute mic", "icon": "🎙️", "keys": "ctrl+shift+m"},
            {"label": "Deafen", "icon": "🎧", "keys": "ctrl+shift+d"},
            {"label": "Save clip (NVIDIA)", "icon": "🎬", "keys": "alt+f10"},
            {"label": "Screenshot", "icon": "📸", "keys": "win+alt+printscreen"},
            {"label": "Volume down", "icon": "🔉", "keys": "voldown"},
            {"label": "Volume up", "icon": "🔊", "keys": "volup"},
            {"label": "Play / pause", "icon": "⏯️", "keys": "playpause"},
            {"label": "Next track", "icon": "⏭️", "keys": "next"},
            {"label": "Open Discord", "icon": "💬", "open": "discord://"},
            {"label": "Work", "icon": "💼", "profile": "Work"},
         ]},
    ],
}

_lock = threading.Lock()


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("profiles"), list) and data["profiles"]:
            return data
    except (OSError, ValueError):
        pass
    return copy.deepcopy(DEFAULT)


def save(data):
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list) or not data["profiles"]:
        raise ValueError("need at least one profile")
    for p in data["profiles"]:
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            raise ValueError("every profile needs a name")
        p["buttons"] = [b for b in p.get("buttons", []) if isinstance(b, dict)][:60]
        for b in p["buttons"]:
            if "keys" in b:
                parse_keys(b["keys"])     # say now if a key name is wrong, not on first tap
        p["cols"] = max(2, min(8, int(p.get("cols", 4))))
    names = [p["name"] for p in data["profiles"]]
    if data.get("active") not in names:
        data["active"] = names[0]
    with _lock:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, PATH)
    return data


def find(data, name):
    return next((p for p in data["profiles"] if p["name"] == name), None)


# ---------------------------------------------------------------- key combos
MODS = {"ctrl": "Control", "control": "Control", "shift": "Shift", "alt": "Alt",
        "win": "Win", "meta": "Win", "cmd": "Win", "super": "Win"}
NAMED = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pgup": 0x21, "pageup": 0x21, "pgdn": 0x22, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "pause": 0x13, "capslock": 0x14, "numlock": 0x90,
    "scrolllock": 0x91, "menu": 0x5D,
    "playpause": 0xB3, "play": 0xB3, "next": 0xB0, "prev": 0xB1, "previous": 0xB1,
    "stop": 0xB2, "volup": 0xAF, "volumeup": 0xAF, "voldown": 0xAE, "volumedown": 0xAE,
    "mute": 0xAD, "volmute": 0xAD,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE, ",": 0xBC,
    ".": 0xBE, "/": 0xBF, "\\": 0xDC, "`": 0xC0,
}
NAMED.update({f"f{i}": 0x6F + i for i in range(1, 25)})
NAMED.update({f"num{i}": 0x60 + i for i in range(10)})


def parse_keys(combo):
    """'ctrl+shift+m' -> (["Control", "Shift"], vk). Raises ValueError."""
    parts = [p.strip().lower() for p in str(combo).replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combination")
    mods, key = [], None
    for p in parts:
        if p in MODS:
            mods.append(MODS[p])
        elif key is None:
            key = p
        else:
            raise ValueError(f"more than one key in '{combo}'")
    if key is None:                       # e.g. just "win"
        return mods[:-1], {"Control": 0x11, "Shift": 0x10, "Alt": 0x12, "Win": 0x5B}[mods[-1]]
    if key in NAMED:
        return mods, NAMED[key]
    if len(key) == 1 and key.isalnum():
        return mods, ord(key.upper())
    raise ValueError(f"unknown key '{key}'")


def run(button, inp, switch_profile):
    """Run one button's action. inp is the server's Input; switch_profile(name)."""
    if "keys" in button:
        mods, vk = parse_keys(button["keys"])
        inp.combo(vk, mods)
    elif "text" in button:
        inp.text(str(button["text"]))
    elif "open" in button:
        target = str(button["open"]).strip()
        if target:
            os.startfile(target)          # Windows: apps, files, URLs, discord:// etc.
    elif "profile" in button:
        switch_profile(str(button["profile"]))
