"""Quick capture: an inbox on the PC for things you grab from the iPad.

Notes (typed or written with Scribble) go into inbox/notes.md, newest at the
bottom, each under a "### date time" line, so the file reads fine in Notepad
or Obsidian. Photos from the iPad and PC screenshots are saved next to it.
The folder is "inbox" next to this file, or "inbox_folder" in settings.json.
Files are only ever served by their name inside the inbox."""
import os
import re
import threading
import time

import hostsettings

HERE = os.path.dirname(os.path.abspath(__file__))
hostsettings.register({"inbox_folder": ""})
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp")
HEAD = re.compile(r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*$", re.M)
_lock = threading.Lock()


def root():
    return hostsettings.get("inbox_folder").strip() or os.path.join(HERE, "inbox")


def _notes_path():
    return os.path.join(root(), "notes.md")


def _read_notes():
    try:
        with open(_notes_path(), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    heads = list(HEAD.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out.append({"id": m.group(1), "text": text[m.end():end].strip("\n")})
    return out


def _write_notes(notes):
    os.makedirs(root(), exist_ok=True)
    tmp = _notes_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for n in notes:
            f.write(f"### {n['id']}\n{n['text']}\n\n")
    os.replace(tmp, _notes_path())


def _stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def note(text):
    text = str(text or "").replace("\r\n", "\n").strip()[:20000]
    if not text:
        raise ValueError("the note is empty")
    text = HEAD.sub(lambda m: "#### " + m.group(1), text)     # a pasted header can't split the note
    with _lock:
        notes = _read_notes()
        nid = _stamp()
        while any(n["id"] == nid for n in notes):                # two notes in one second
            time.sleep(1.0)
            nid = _stamp()
        notes.append({"id": nid, "text": text})
        _write_notes(notes)
    return {"saved": nid}


def _new_file(prefix, ext):
    os.makedirs(root(), exist_ok=True)
    base = os.path.join(root(), time.strftime(f"{prefix} %Y-%m-%d %H-%M-%S"))
    p, n = base + ext, 2
    while os.path.exists(p):
        p, n = f"{base} ({n}){ext}", n + 1
    return p


def photo(filename, body):
    ext = os.path.splitext(str(filename or ""))[1].lower()
    if ext not in IMAGE_EXT:
        ext = ".jpg"
    if not body:
        raise ValueError("the photo is empty")
    if len(body) > 40 << 20:
        raise ValueError("that photo is too big")
    p = _new_file("Photo", ext)
    with open(p, "wb") as f:
        f.write(body)
    return {"saved": os.path.basename(p)}


def _cursor_monitor(monitors):
    """The mss monitor under the mouse (the one you're looking at), else the main one."""
    try:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        for m in monitors[1:]:
            if m["left"] <= pt.x < m["left"] + m["width"] and m["top"] <= pt.y < m["top"] + m["height"]:
                return m
    except Exception:
        pass
    return monitors[1] if len(monitors) > 1 else monitors[0]


def screenshot():
    """A PNG of the PC screen the mouse is on. One grab, nothing kept running."""
    import mss
    from PIL import Image
    with mss.mss() as sct:
        shot = sct.grab(_cursor_monitor(sct.monitors))
    p = _new_file("Screenshot", ".png")
    Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").save(p, optimize=False, compress_level=3)
    print(f"[capture] {os.path.basename(p)}", flush=True)
    return {"saved": os.path.basename(p)}


def file_path(name):
    """An inbox image, only by its name inside the inbox folder."""
    name = os.path.basename(str(name or ""))
    p = os.path.join(root(), name)
    return p if name and name.lower().endswith(IMAGE_EXT) and os.path.isfile(p) else None


def listing():
    notes = _read_notes()[::-1][:100]
    try:
        files = [n for n in os.listdir(root()) if n.lower().endswith(IMAGE_EXT)]
    except OSError:
        files = []
    files = sorted(files, key=lambda n: os.path.getmtime(os.path.join(root(), n)), reverse=True)[:60]
    return {"root": root(), "notes": notes, "files": files}


def delete(kind, ident):
    with _lock:
        if kind == "note":
            notes = _read_notes()
            keep = [n for n in notes if n["id"] != ident]
            if len(keep) == len(notes):
                raise ValueError("that note is gone")
            _write_notes(keep)
        else:
            p = file_path(ident)
            if not p:
                raise ValueError("that file is gone")
            os.remove(p)
    return {"ok": True}


def open_folder():
    os.makedirs(root(), exist_ok=True)
    os.startfile(root())
