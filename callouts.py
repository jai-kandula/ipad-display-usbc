"""Map callouts for Siege (or any game) from your own pictures, shown on the
iPad where you can zoom in and draw on them with the Pencil or a finger.

Put the pictures in the callouts folder next to this file, one folder per
map and one picture per floor:

    callouts/
      Bank/
        1 Basement.png
        2 First floor.png
        3 Second floor.png
        4 Roof.jpg
      Clubhouse/
        ...

Floors are shown in name order, so a number in front sets the order (the
number isn't shown). Pictures straight in callouts/ are a map of their own.
Another folder can be used instead with "callouts_folder" in settings.json.
Pictures are only served from this folder, by an id from the list."""
import hashlib
import os
import re
import time

import hostsettings

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(HERE, "callouts")
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}
hostsettings.register({"callouts_folder": ""})

_cache = {"at": 0.0, "key": None, "maps": [], "by_id": {}}


def folder():
    return hostsettings.get("callouts_folder").strip() or DEFAULT_DIR


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _label(name):
    stem = os.path.splitext(name)[0]
    return re.sub(r"^\d+[\s._-]*", "", stem).strip() or stem


def _id(path):
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace")).hexdigest()[:16]


def _floors(d):
    try:
        names = sorted((n for n in os.listdir(d) if os.path.splitext(n)[1].lower() in IMAGE_EXT), key=_natural)
    except OSError:
        return []
    out = []
    for n in names[:40]:
        p = os.path.join(d, n)
        if os.path.isfile(p):
            out.append({"id": _id(p), "name": _label(n), "path": p, "mtime": int(os.path.getmtime(p))})
    return out


def _scan(root):
    maps = []
    loose = _floors(root)
    try:
        subs = sorted((n for n in os.listdir(root) if not n.startswith(".")
                       and os.path.isdir(os.path.join(root, n))), key=_natural)
    except OSError:
        subs = []
    for n in subs[:200]:
        fl = _floors(os.path.join(root, n))
        if fl:
            maps.append({"name": _label(n), "floors": fl})
    if loose:
        maps.append({"name": "Other", "floors": loose})
    return maps


def listing():
    """[{name, floors: [{id, name, v}]}] (rescanned at most every 5 s)."""
    root = folder()
    if time.time() - _cache["at"] > 5 or _cache["key"] != root:
        maps = _scan(root)
        _cache.update(at=time.time(), key=root, maps=maps,
                      by_id={f["id"]: f for m in maps for f in m["floors"]})
    return {"folder": root, "exists": os.path.isdir(root),
            "maps": [{"name": m["name"], "floors": [{"id": f["id"], "name": f["name"], "v": f["mtime"]}
                                                    for f in m["floors"]]} for m in _cache["maps"]]}


def image(fid):
    """(path, content type) of a floor picture by id, or None."""
    listing()
    f = _cache["by_id"].get(fid)
    if not f or not os.path.isfile(f["path"]):
        return None
    return f["path"], IMAGE_EXT[os.path.splitext(f["path"])[1].lower()]


def open_folder():
    """Open the callouts folder in File Explorer on the PC (made if missing)."""
    root = folder()
    os.makedirs(root, exist_ok=True)
    os.startfile(root)
