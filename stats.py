"""Live numbers for the iPad's tiles: CPU and RAM use, the NVIDIA GPU's load
and temperature, the CPU temperature (from LibreHardwareMonitor when it runs),
and what's playing (title, artist, album art) from Windows' media controls.

Everything is read only while an iPad is showing a tile (it asks every second
or two); a minute after the last ask the readers go quiet again."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque

IS_WINDOWS = sys.platform == "win32"
HERE = os.path.dirname(os.path.abspath(__file__))
ART_PATH = os.path.join(HERE, ".nowplaying-art")
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
IDLE_AFTER = 60.0

_lock = threading.Lock()
data = {"cpu": {"load": None, "temp": None}, "ram": {"used": None, "total": None},
        "gpu": None, "media": None}
history = {"cpu": deque(maxlen=60), "gpu": deque(maxlen=60)}
_last_ask = 0.0
_threads = {}
_started = {}
RETRY_AFTER = 30.0                # a reader that stopped early (no NVIDIA GPU...) waits this long


# ---------------------------------------------------------------- CPU and RAM
class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


class _MEMSTAT(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint32), ("load", ctypes.c_uint32), ("total", ctypes.c_uint64),
                ("avail", ctypes.c_uint64), ("tpage", ctypes.c_uint64), ("apage", ctypes.c_uint64),
                ("tvirt", ctypes.c_uint64), ("avirt", ctypes.c_uint64), ("aext", ctypes.c_uint64)]


def _ft(f):
    return (f.hi << 32) | f.lo


def _cpu_times():
    if IS_WINDOWS:
        idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
        ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        return _ft(idle), _ft(kern) + _ft(user)          # kernel time includes idle
    with open("/proc/stat") as f:                         # Linux (for testing)
        v = [int(x) for x in f.readline().split()[1:]]
    return v[3] + v[4], sum(v)


def _ram():
    if IS_WINDOWS:
        m = _MEMSTAT(len=ctypes.sizeof(_MEMSTAT))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total - m.avail, m.total
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k] = int(v.split()[0]) * 1024
    return info["MemTotal"] - info.get("MemAvailable", 0), info["MemTotal"]


def _cpu_loop():
    prev = _cpu_times()
    while time.time() - _last_ask < IDLE_AFTER:
        time.sleep(1.0)
        cur = _cpu_times()
        di, dt = cur[0] - prev[0], cur[1] - prev[1]
        prev = cur
        load = round(100 * (1 - di / dt), 1) if dt > 0 else None
        used, total = _ram()
        with _lock:
            data["cpu"]["load"] = load
            data["ram"] = {"used": used, "total": total}
            if load is not None:
                history["cpu"].append(load)


# ---------------------------------------------------------------- GPU (NVIDIA)
def _nvidia_smi():
    p = shutil.which("nvidia-smi")
    if not p and IS_WINDOWS:
        p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
    return p if p and os.path.exists(p) else None


def _gpu_loop():
    exe = _nvidia_smi()
    if not exe:
        return
    q = "name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw"
    while time.time() - _last_ask < IDLE_AFTER:
        try:
            out = subprocess.run([exe, f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5,
                                 creationflags=_NO_WINDOW).stdout.strip().splitlines()[0]
            name, load, temp, mu, mt, pw = [x.strip() for x in out.split(",")]
            num = lambda x: float(x) if x.replace(".", "", 1).isdigit() else None   # noqa: E731
            g = {"name": name, "load": num(load), "temp": num(temp), "mem_used": num(mu),
                 "mem_total": num(mt), "power": num(pw)}
            with _lock:
                data["gpu"] = g
                if g["load"] is not None:
                    history["gpu"].append(g["load"])
        except Exception:
            pass
        time.sleep(2.0)


# ---------------------------------------------------------------- CPU temperature
def _cpu_temp_loop():
    """LibreHardwareMonitor's web server (Options > Remote Web Server, port 8085)."""
    def walk(node, out):
        if node.get("Type") == "Temperature" or "°C" in str(node.get("Value", "")):
            out.append((node.get("Text", ""), node.get("Value", "")))
        for c in node.get("Children", []):
            walk(c, out)
    while time.time() - _last_ask < IDLE_AFTER:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8085/data.json", timeout=1.5) as r:
                tree = json.load(r)
            temps = []
            walk(tree, temps)
            pick = next((v for t, v in temps if "package" in t.lower() or "tctl" in t.lower()), None)
            if pick:
                with _lock:
                    data["cpu"]["temp"] = float(str(pick).replace("°C", "").replace(",", ".").strip())
        except Exception:
            time.sleep(8.0)               # not running: look again now and then
        time.sleep(2.0)


# ---------------------------------------------------------------- now playing
def _media_loop():
    if not IS_WINDOWS:
        return
    script = os.path.join(HERE, "nowplaying.ps1")
    proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", script, "-Art", ART_PATH],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                m = json.loads(line)
            except ValueError:
                continue
            with _lock:
                data["media"] = m if m.get("title") else None
            if time.time() - _last_ask > IDLE_AFTER:
                break
    finally:
        proc.kill()


# ---------------------------------------------------------------- public
def _ensure(name, fn):
    t = _threads.get(name)
    if t is not None and t.is_alive():
        return
    if time.time() - _started.get(name, 0) < RETRY_AFTER:
        return                    # it just gave up (nothing to read): don't respawn it every second
    _started[name] = time.time()
    t = threading.Thread(target=fn, daemon=True, name=f"stats-{name}")
    _threads[name] = t
    t.start()


READERS = {"cpu": (("cpu", _cpu_loop), ("temp", _cpu_temp_loop)), "ram": (("cpu", _cpu_loop),),
           "gpu": (("gpu", _gpu_loop),), "media": (("media", _media_loop),)}


def snapshot(want=("cpu", "ram", "gpu", "media")):
    """Called on every ask from the iPad: start the readers the shown tiles
    need and return the numbers."""
    global _last_ask
    _last_ask = time.time()
    for w in want:
        for name, fn in READERS.get(w, ()):
            _ensure(name, fn)
    with _lock:
        out = json.loads(json.dumps(data))
        out["history"] = {k: list(v) for k, v in history.items()}
    return out


def art():
    """Bytes and type of the current album art, or None."""
    try:
        with open(ART_PATH, "rb") as f:
            b = f.read()
        return b, ("image/png" if b[:4] == b"\x89PNG" else "image/jpeg")
    except OSError:
        return None
