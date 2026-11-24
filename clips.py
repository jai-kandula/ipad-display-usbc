"""Replay clips from any clipping app, in one list on the iPad.

Built-in sources are the usual save folders of NVIDIA (ShadowPlay / NVIDIA
app), Medal, Insights Capture and Outplayed; folders that don't exist are
skipped. Any other app, including your own, is added in settings.json under
"clip_sources" in one of two ways:

  1. A folder:   {"name": "Ascent", "folder": "D:\\\\Clips\\\\Ascent"}
     Every video in it (and its subfolders) is a clip. A JSON file next to a
     clip with the same name (clip.mp4 + clip.json) can add details:
     {"title": "Ace on Bank", "game": "Rainbow Six Siege", "tags": ["ace"],
      "thumb": "clip.jpg"}

  2. A manifest: {"name": "My clipper", "manifest": "C:\\\\MyClipper\\\\clips.json"}
     Your tool keeps one JSON file listing its clips:
     {"clips": [{"file": "C:\\\\MyClipper\\\\2026-11-23 ace.mp4",
                 "title": "Ace on Bank", "game": "Rainbow Six Siege",
                 "created": "2026-11-23T20:14:05Z", "duration": 30.5,
                 "thumb": "C:\\\\MyClipper\\\\2026-11-23 ace.jpg", "tags": ["ace"]}]}
     Only "file" is required. Relative paths are relative to the manifest.

Clips are only ever served from these sources, by an id from the list."""
import hashlib
import json
import os
import threading
import time

import hostsettings

HERE = os.path.dirname(os.path.abspath(__file__))
THUMBS = os.path.join(HERE, ".clipthumbs")
VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".m4v")
HOME = os.path.expanduser("~")
VIDEOS = os.path.join(HOME, "Videos")
BUILTIN = [
    {"name": "NVIDIA", "folder": os.path.join(VIDEOS, "NVIDIA")},
    {"name": "NVIDIA", "folder": os.path.join(VIDEOS, "Captures")},       # Game Bar / older ShadowPlay
    {"name": "Medal", "folder": os.path.join(VIDEOS, "Medal")},
    {"name": "Medal", "folder": "C:\\Medal\\Clips"},
    {"name": "Insights", "folder": os.path.join(VIDEOS, "Insights Capture")},
    {"name": "Outplayed", "folder": os.path.join(VIDEOS, "Overwolf", "Outplayed")},
]
MAX_CLIPS = 2000
hostsettings.register({"clip_sources": []})

_lock = threading.Lock()
_index = {"at": 0.0, "clips": [], "by_id": {}}


def _id(path):
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8", "replace")).hexdigest()[:16]


def sources():
    """Every source that exists on this PC: [{name, folder | manifest, builtin}]."""
    out = []
    for s in BUILTIN:
        if os.path.isdir(s["folder"]):
            out.append({**s, "builtin": True})
    for s in hostsettings.get("clip_sources") or []:
        if isinstance(s, dict) and (s.get("folder") or s.get("manifest")):
            out.append({"name": str(s.get("name") or "Clips"), "folder": s.get("folder"),
                        "manifest": s.get("manifest"), "builtin": False})
    return out


def _sidecar(path):
    try:
        with open(os.path.splitext(path)[0] + ".json", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _clip(path, source, meta=None, base=None):
    meta = meta or {}
    try:
        st = os.stat(path)
    except OSError:
        return None
    thumb = meta.get("thumb")
    if thumb and base and not os.path.isabs(thumb):
        thumb = os.path.join(base, thumb)
    parent = os.path.basename(os.path.dirname(path))
    when = st.st_mtime
    if meta.get("created"):
        try:
            import datetime
            when = datetime.datetime.fromisoformat(str(meta["created"]).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return {"id": _id(path), "path": path, "source": source,
            "title": str(meta.get("title") or os.path.splitext(os.path.basename(path))[0]),
            "game": str(meta.get("game") or (parent if parent.lower() not in ("clips", "videos", "captures") else "")),
            "created": meta.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            "mtime": st.st_mtime, "when": when, "size": st.st_size, "duration": meta.get("duration"),
            "tags": [str(t) for t in meta.get("tags", [])][:8] if isinstance(meta.get("tags"), list) else [],
            "thumb": thumb if thumb and os.path.isfile(thumb) else None}


def _scan():
    clips, seen = [], set()
    for s in sources():
        if s.get("manifest"):
            try:
                with open(s["manifest"], encoding="utf-8") as f:
                    d = json.load(f)
                base = os.path.dirname(s["manifest"])
                for c in (d.get("clips") if isinstance(d, dict) else d) or []:
                    if not isinstance(c, dict) or not c.get("file"):
                        continue
                    p = c["file"] if os.path.isabs(c["file"]) else os.path.join(base, c["file"])
                    clip = _clip(p, s["name"], c, base)
                    if clip and clip["id"] not in seen:
                        seen.add(clip["id"])
                        clips.append(clip)
            except (OSError, ValueError) as e:
                print(f"[clips] {s['name']}: can't read {s['manifest']}: {e}", flush=True)
            continue
        for root, dirs, files in os.walk(s["folder"]):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for n in files:
                if n.lower().endswith(VIDEO_EXT):
                    p = os.path.join(root, n)
                    clip = _clip(p, s["name"], _sidecar(p), root)
                    if clip and clip["id"] not in seen:
                        seen.add(clip["id"])
                        clips.append(clip)
            if len(clips) > MAX_CLIPS * 2:
                break
    clips.sort(key=lambda c: c["when"], reverse=True)
    return clips[:MAX_CLIPS]


def listing(force=False):
    """All clips, newest first (rescanned at most every 10 s)."""
    with _lock:
        if force or time.time() - _index["at"] > 10:
            clips = _scan()
            _index.update(at=time.time(), clips=clips, by_id={c["id"]: c for c in clips})
        return _index["clips"]


def get(clip_id):
    listing()
    return _index["by_id"].get(clip_id)


def public(c):
    return {k: v for k, v in c.items() if k not in ("path", "thumb")} | {"has_thumb": True}


def thumb(clip_id):
    """JPEG bytes for a clip's picture: its own thumbnail, or a frame from
    one second in (made once and kept in .clipthumbs/)."""
    c = get(clip_id)
    if not c:
        return None
    if c["thumb"]:
        with open(c["thumb"], "rb") as f:
            return f.read()
    os.makedirs(THUMBS, exist_ok=True)
    cache = os.path.join(THUMBS, f"{clip_id}-{int(c['mtime'])}.jpg")
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return f.read()
    try:
        import av
        from PIL import Image  # noqa: F401  (to_image needs it)
        with av.open(c["path"]) as box:
            vs = box.streams.video[0]
            vs.thread_type = "AUTO"
            if box.duration and box.duration > 2_000_000:
                box.seek(1_000_000)
            for frame in box.decode(vs):
                img = frame.to_image()
                img.thumbnail((480, 270))
                img.save(cache, "JPEG", quality=78)
                break
            if c["duration"] is None and box.duration:
                c["duration"] = round(box.duration / 1e6, 1)
        with open(cache, "rb") as f:
            return f.read()
    except Exception as e:
        print(f"[clips] no thumbnail for {os.path.basename(c['path'])}: {e}", flush=True)
        return None


def reveal(clip_id):
    """Show the clip in File Explorer on the PC."""
    c = get(clip_id)
    if not c:
        raise ValueError("no such clip")
    import subprocess
    subprocess.Popen(["explorer", "/select,", c["path"]])


def play_on_pc(clip_id):
    c = get(clip_id)
    if not c:
        raise ValueError("no such clip")
    os.startfile(c["path"])        # the PC's video player
