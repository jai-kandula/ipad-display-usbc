"""
iPad Display host for Windows (Python 3.15).

Captures one of your Windows monitors (normally the virtual one created by
Virtual Display Driver), streams it to the iPad web app over a WebSocket, and
turns iPad touches / Apple Pencil / keyboard into mouse and keyboard input.

How it stays fast *and* sharp:
  * Only the parts of the screen that changed are sent (64 px tiles).
  * Small changes (typing, a button highlight) go out at high quality at once.
  * Big changes (scrolling, video) go out at a lighter quality to keep latency
    low, then get re-sent at high quality as soon as the screen settles.
  * Full frames are split into stripes and encoded in parallel.
  * The mouse pointer is sent as a tiny separate message and drawn on the
    iPad, so it moves instantly without re-sending screen pixels.
  * Quality adapts to how fast the iPad is acknowledging frames.

Run with:  start.bat   (or: python server.py [--port 8765])
"""
import argparse
import asyncio
import ctypes
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

IS_WINDOWS = sys.platform == "win32"

# Must happen before anything touches the screen, so that coordinates are real
# pixels and not DPI-scaled ones.
if IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import mss  # noqa: E402
import numpy as np  # noqa: E402
from aiohttp import web, WSMsgType  # noqa: E402
from PIL import Image  # noqa: E402

try:  # libjpeg-turbo bindings: much faster than Pillow, and releases the GIL
    import simplejpeg  # type: ignore
except Exception:
    simplejpeg = None

_MSS = getattr(mss, "MSS", None) or mss.mss

# GDI capture: CAPTUREBLT forces Windows to re-compose layered windows on every
# grab, which makes BitBlt several times slower. We don't need it.
try:
    import mss.windows.gdi as _mss_gdi  # type: ignore
    _mss_gdi.CAPTUREBLT = 0
except Exception:
    pass

# GPU capture (DXGI Desktop Duplication) via dxcam: much faster than GDI and it
# tells us when nothing on screen changed. Optional: we fall back to GDI.
try:
    if IS_WINDOWS:
        import logging as _logging
        _logging.getLogger("dxcam").setLevel(_logging.ERROR)
        _logging.getLogger("comtypes").setLevel(_logging.ERROR)
        import dxcam  # type: ignore
    else:
        dxcam = None
except Exception:
    dxcam = None

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
CONFIG_PATH = os.path.join(HERE, "config.json")

TILE = 64                     # change-detection grid, multiple of 16 for clean JPEG blocks
SMALL_CHANGE = 0.08           # < 8% of the screen changed: send straight at high quality
FULL_FRAME = 0.45             # > 45% changed: send the whole screen as stripes
SETTLE_SECONDS = 0.12         # screen still for this long: sharpen blurry tiles
MAX_IN_FLIGHT = 2             # frames sent but not yet shown on the iPad
SHARPEN_CHUNK = 0.2           # sharpen at most this share of the screen per update,
                              # so a big re-send never blocks the next motion frame

# Quality profiles. lo = while things move, hi = once they settle.
PROFILES = {
# (For even lower delay, set the virtual screen to the "fast" half resolution
# listed for your iPad: that cuts the pixels to encode by 4x.)
    "fast":     {"lo": 45, "hi": 80, "hi_sub": "420"},
    "balanced": {"lo": 65, "hi": 90, "hi_sub": "444"},
    "sharp":    {"lo": 80, "hi": 95, "hi_sub": "444"},
    # Cable (Ethernet) link: bandwidth is plentiful, so send almost everything
    # at full quality with full colour detail and sharpen almost instantly.
    # USB-C / Ethernet. Sized for a USB 2 iPad (~30 MB/s through Apple's USB
    # service): crisp 4:4:4 for small changes and once things settle, lighter
    # 4:2:0 frames while things move so frames never pile up in the cable.
    "wired":    {"lo": 70, "hi": 92, "hi_sub": "444", "lo_sub": "420",
                 "small": 0.15, "settle": 0.15},
}

# Address ranges that mean "connected by cable" (see WIRED.md):
#   192.168.77.x  the static addresses the wired guide sets up
#   169.254.x.x   automatic addresses on a direct Ethernet cable
#   172.20.10.x   iPad (cellular) Personal Hotspot over USB
#   127.0.0.1     the native iPad app over USB-C (arrives through usb_bridge.py)
WIRED_PREFIXES = ("192.168.77.", "169.254.", "172.20.10.", "127.0.0.1")


def is_wired(addr):
    return bool(addr) and addr.startswith(WIRED_PREFIXES)


# --------------------------------------------------------------------------
# Config / pairing PIN
# --------------------------------------------------------------------------
def load_config():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    if not cfg.get("pin"):
        cfg["pin"] = f"{secrets.randbelow(10**6):06d}"
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return cfg


def lan_addresses():
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except Exception:
        pass
    try:  # the address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


# --------------------------------------------------------------------------
# Input injection (Windows only; no-op elsewhere so the app can be tested)
# --------------------------------------------------------------------------
class Input:
    LEFTDOWN, LEFTUP = 0x0002, 0x0004
    RIGHTDOWN, RIGHTUP = 0x0008, 0x0010
    MIDDLEDOWN, MIDDLEUP = 0x0020, 0x0040
    WHEEL, HWHEEL = 0x0800, 0x1000
    KEYUP, UNICODE = 0x0002, 0x0004

    VK = {
        "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Escape": 0x1B,
        "Delete": 0x2E, "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27,
        "ArrowDown": 0x28, "Home": 0x24, "End": 0x23, "PageUp": 0x21,
        "PageDown": 0x22, "Control": 0x11, "Shift": 0x10, "Alt": 0x12,
        "Meta": 0x5B, "Win": 0x5B,
    }

    def __init__(self):
        self.ok = IS_WINDOWS
        if not self.ok:
            return
        self.user32 = ctypes.windll.user32
        ULONG_PTR = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                        ("dwExtraInfo", ULONG_PTR)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                        ("time", ctypes.c_ulong), ("dwExtraInfo", ULONG_PTR)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT),
                        ("pad", ctypes.c_byte * 32)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        self.KEYBDINPUT, self.MOUSEINPUT, self.INPUT = KEYBDINPUT, MOUSEINPUT, INPUT
        self._pt = POINT()

    def cursor(self):
        if self.ok and self.user32.GetCursorPos(ctypes.byref(self._pt)):
            return self._pt.x, self._pt.y
        return None

    def _send(self, items):
        arr = (self.INPUT * len(items))(*items)
        self.user32.SendInput(len(items), arr, ctypes.sizeof(self.INPUT))

    def _mouse(self, flags, data=0):
        i = self.INPUT(type=0)
        i.u.mi = self.MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
        return i

    def _key(self, vk=0, scan=0, flags=0):
        i = self.INPUT(type=1)
        i.u.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        return i

    def move(self, x, y):
        if self.ok:
            self.user32.SetCursorPos(int(round(x)), int(round(y)))

    def button(self, button, down):
        if not self.ok:
            return
        flags = {
            0: (self.LEFTDOWN, self.LEFTUP),
            1: (self.MIDDLEDOWN, self.MIDDLEUP),
            2: (self.RIGHTDOWN, self.RIGHTUP),
        }.get(button, (self.LEFTDOWN, self.LEFTUP))
        self._send([self._mouse(flags[0] if down else flags[1])])

    def scroll(self, dx, dy):
        if not self.ok:
            return
        items = []
        if dy:
            items.append(self._mouse(self.WHEEL, int(dy)))
        if dx:
            items.append(self._mouse(self.HWHEEL, int(dx)))
        if items:
            self._send(items)

    def text(self, s):
        if not self.ok or not s:
            return
        items = []
        for ch in s:
            if ch == "\n":
                items += [self._key(0x0D), self._key(0x0D, flags=self.KEYUP)]
                continue
            code = ord(ch)
            units = [code] if code < 0x10000 else [
                0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
            for u in units:
                items.append(self._key(0, u, self.UNICODE))
                items.append(self._key(0, u, self.UNICODE | self.KEYUP))
        self._send(items)

    def key(self, name, mods=()):
        """Press a named key, optionally with modifiers (e.g. Control+c)."""
        if not self.ok:
            return
        vk = self.VK.get(name)
        if vk is None and len(name) == 1:
            vk = ord(name.upper()) if name.isalnum() else None
        if vk is None:
            return
        mod_vks = [self.VK[m] for m in mods if m in self.VK]
        items = [self._key(m) for m in mod_vks]
        items += [self._key(vk), self._key(vk, flags=self.KEYUP)]
        items += [self._key(m, flags=self.KEYUP) for m in reversed(mod_vks)]
        self._send(items)


INPUT = Input()
ENCODERS = ThreadPoolExecutor(max_workers=max(2, min(6, (os.cpu_count() or 4) - 1)),
                              thread_name_prefix="jpeg")


# --------------------------------------------------------------------------
# JPEG encoding
# --------------------------------------------------------------------------
def encode(arr, quality, sub, cs):
    """arr: HxWx4 uint8 in colour order cs ('BGRA' or 'RGBA')."""
    if simplejpeg is not None:
        return simplejpeg.encode_jpeg(np.ascontiguousarray(arr), quality=quality,
                                      colorspace=cs, colorsubsampling=sub,
                                      fastdct=quality < 85)
    rgb = arr[..., 2::-1] if cs == "BGRA" else arr[..., :3]
    import io
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(rgb), "RGB").save(
        buf, "JPEG", quality=quality,
        subsampling={"444": 0, "422": 1, "420": 2}[sub])
    return buf.getvalue()


def tiles_to_rects(mask):
    """Merge a (rows, cols) bool tile mask into rectangles (tile units)."""
    rects, open_ = [], {}
    rows, cols = mask.shape
    for r in range(rows):
        row = mask[r]
        runs, c = [], 0
        while c < cols:
            if row[c]:
                s = c
                while c < cols and row[c]:
                    c += 1
                runs.append((s, c))
            else:
                c += 1
        nxt = {}
        for run in runs:
            if run in open_:              # same columns as the row above: grow it down
                rect = open_.pop(run)
                rect[3] += 1
            else:
                rect = [run[0], r, run[1] - run[0], 1]
                rects.append(rect)
            nxt[run] = rect
        open_ = nxt
    return rects


# --------------------------------------------------------------------------
# One streaming session (capture thread state)
# --------------------------------------------------------------------------
class _Grabber(threading.Thread):
    """Copies frames off the GPU on its own thread, so the next frame is being
    fetched while the previous one is compared and encoded. Only this thread
    touches the dxcam camera once it is running."""

    def __init__(self, cam):
        super().__init__(daemon=True, name="gpu-grab")
        self.cam = cam
        self.cond = threading.Condition()
        self.frame = None
        self.err = None
        self.stopping = False
        self.cap_t, self.n = 0.0, 0

    def run(self):
        while not self.stopping:
            t0 = time.perf_counter()
            try:
                f = self.cam.grab(new_frame_only=True)
            except Exception as e:      # display mode changed, driver reset, ...
                with self.cond:
                    self.err = e
                    self.cond.notify_all()
                return
            if f is None:
                time.sleep(0.001)
                continue
            dt = time.perf_counter() - t0
            with self.cond:
                self.frame = f          # newest frame wins; older ones are dropped
                self.cap_t += dt
                self.n += 1
                self.cond.notify_all()

    def take(self, timeout):
        """Newest new frame (or None after timeout), plus grab stats since last call."""
        with self.cond:
            if self.frame is None and self.err is None:
                self.cond.wait(timeout)
            if self.err is not None:
                raise self.err
            f, self.frame = self.frame, None
            st = (self.cap_t, self.n)
            self.cap_t, self.n = 0.0, 0
            return f, st

    def stop(self):
        self.stopping = True
        self.join(timeout=1.0)


class Session:
    def __init__(self, monitor_index, profile):
        self.monitor_index = monitor_index
        self.set_profile(profile)
        self.sct = None
        self.mon = None
        self.prev = None           # last frame as uint32 (h, w)
        self.blurry = None         # tiles last sent at motion quality
        self.last_change = 0.0
        self.dims = (0, 0)
        self.scale = 1.0
        self.adapt = 0             # quality drop (0..30) when the link is slow
        self.dx = None             # dxcam camera (GPU capture) if available
        self.dx_tried = False
        self.grab = None           # _Grabber thread feeding GPU frames
        self.wait = 1 / 60         # how long a step waits for a new GPU frame
        self.backend = "GDI"
        self.last = None           # last captured (array, colourspace)
        self.stats = {"frames": 0, "cap": 0.0, "enc": 0.0, "bytes": 0}
        self._diff = None          # reused buffer for the changed-pixel mask

    def set_profile(self, name):
        self.profile = PROFILES.get(name, PROFILES["balanced"])
        self.reset = True          # full refresh on the next capture (thread-safe flag)

    def monitor(self):
        if self.sct is None:
            self.sct = _MSS()
        mons = self.sct.monitors
        idx = self.monitor_index if 0 < self.monitor_index < len(mons) else len(mons) - 1
        self.mon = dict(mons[idx])
        return self.mon

    def _check_geometry(self):
        """Every 2 s, notice if Windows moved or resized this screen (new
        resolution, scale or arrangement) and follow it."""
        now = time.perf_counter()
        if now < getattr(self, "_next_geo", 0):
            return
        self._next_geo = now + 2.0
        try:
            with _MSS() as fresh:
                mons = fresh.monitors
            idx = self.monitor_index if 0 < self.monitor_index < len(mons) else len(mons) - 1
            new = dict(mons[idx])
        except Exception:
            return
        if all(new[k] == self.mon[k] for k in ("left", "top", "width", "height")):
            return
        print(f"[screen] changed to {new['width']}x{new['height']} at "
              f"({new['left']},{new['top']}), following it.")
        self.mon = new
        if self.sct is not None:
            self.sct.close()
        self.sct = _MSS()
        self._release_dx()
        self.dx_tried, self.backend = False, "GDI"
        self.last = None
        self.reset = True

    def _open_dxgi(self):
        """Find the DXGI output for this screen and open a duplication on it."""
        self.dx_tried = True
        if dxcam is None:
            return
        try:
            fac = getattr(dxcam, "__factory")
            m = self.mon
            for d, outs in enumerate(fac.outputs):
                for o, out in enumerate(outs):
                    out.update_desc()
                    rc = out.desc.DesktopCoordinates
                    if ((rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
                            == (m["left"], m["top"], m["width"], m["height"])
                            and out.rotation_angle == 0):
                        self.dx = dxcam.create(device_idx=d, output_idx=o,
                                               output_color="BGRA",
                                               processor_backend="numpy")
                        self.grab = _Grabber(self.dx)
                        self.grab.start()
                        self.backend = "GPU"
                        return
        except Exception as e:
            print("GPU capture unavailable, using GDI:", e)
        self.dx = None

    def _capture(self):
        """Returns (array, colourspace), or None if the screen hasn't changed."""
        if IS_WINDOWS and not self.dx_tried:
            self._open_dxgi()
        if self.grab is not None:
            try:
                tw = time.perf_counter()
                frame, (t, n) = self.grab.take(self.wait)
                self.stats["idle"] = self.stats.get("idle", 0.0) + time.perf_counter() - tw
            except Exception as e:      # display mode changed, driver reset, ...
                print("GPU capture stopped, switching to GDI:", e)
                self._release_dx()
                self.backend = "GDI"
            else:
                self.stats["cap"] += t
                self.stats["caps"] = self.stats.get("caps", 0) + n
                self.stats["new"] = self.stats.get("new", 0) + n
                return None if frame is None else (frame, "BGRA")
        t0 = time.perf_counter()
        shot = self.sct.grab(self.mon)
        self.stats["cap"] += time.perf_counter() - t0
        self.stats["caps"] = self.stats.get("caps", 0) + 1
        self.stats["new"] = self.stats.get("new", 0) + 1
        w, h = shot.size
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape(h, w, 4), "BGRA"

    def step(self, force=False):
        """Capture once. Returns (packet bytes | None, (w, h))."""
        t0 = time.perf_counter()
        try:
            return self._step(force)
        finally:
            self.stats["work"] = self.stats.get("work", 0.0) + time.perf_counter() - t0
            self.stats["steps"] = self.stats.get("steps", 0) + 1

    def _step(self, force=False):
        if self.mon is None:
            self.monitor()
        self._check_geometry()
        cap = self._capture()
        unchanged = cap is None
        if unchanged:
            if self.last is None:
                return None, self.dims
            cap = self.last
        self.last = cap
        arr, cs = cap
        h, w = arr.shape[:2]
        self.scale = w / self.mon["width"]
        # Compare two pixels at a time when the width allows (half the work).
        px = 2 if w % 2 == 0 else 1
        cur = arr.reshape(h, w * 4).view(np.uint64 if px == 2 else np.uint32)
        now = time.perf_counter()
        prof = self.profile
        lo = max(35, prof["lo"] - self.adapt)
        hi = max(70, prof["hi"] - self.adapt // 2)
        trows, tcols = -(-h // TILE), -(-w // TILE)

        if self.reset:
            self.reset, force = False, True
        if force or self.prev is None or self.prev.shape != cur.shape:
            self.dims = (w, h)
            self.prev = cur
            self.blurry = np.zeros((trows, tcols), bool)
            self.last_change = now
            return self._pack(arr, cs, self._stripes(w, h), hi, prof["hi_sub"], key=True), self.dims

        if unchanged:
            diff = None
        else:
            if self._diff is None or self._diff.shape != cur.shape:
                self._diff = np.empty(cur.shape, bool)
            diff = np.not_equal(cur, self.prev, out=self._diff)
        if diff is None or not diff.any():
            # Nothing moved. If something was sent blurry, sharpen it now.
            # Done in slices (top to bottom) so no single update is huge: if
            # things start moving again, the next motion frame isn't stuck
            # behind a megabyte of sharpening in the cable.
            if self.blurry.any() and now - self.last_change >= prof.get("settle", SETTLE_SECONDS):
                budget = max(1, int(self.blurry.size * SHARPEN_CHUNK))
                upto = int(np.searchsorted(np.cumsum(self.blurry.sum(axis=1)), budget)) + 1
                part = np.zeros_like(self.blurry)
                part[:upto] = self.blurry[:upto]
                self.blurry &= ~part
                rects = self._px(tiles_to_rects(part), w, h)
                return self._pack(arr, cs, rects, hi, prof["hi_sub"]), self.dims
            return None, self.dims

        self.last_change = now
        mask = np.logical_or.reduceat(
            np.logical_or.reduceat(diff, np.arange(0, h, TILE), axis=0),
            np.arange(0, w // px, TILE // px), axis=1)
        self.prev = cur        # every capture is a new array, so no copy is needed
        frac = mask.mean()

        lo_sub = prof.get("lo_sub", "420")
        if frac < prof.get("small", SMALL_CHANGE):   # typing, hovering: crisp straight away
            self.blurry &= ~mask
            return self._pack(arr, cs, self._px(tiles_to_rects(mask), w, h),
                              hi, prof["hi_sub"]), self.dims
        if frac > FULL_FRAME:             # scrolling / video: whole screen, fast
            self.blurry[:] = True
            return self._pack(arr, cs, self._stripes(w, h), lo, lo_sub), self.dims
        self.blurry |= mask
        return self._pack(arr, cs, self._px(tiles_to_rects(mask), w, h), lo, lo_sub), self.dims

    @staticmethod
    def _px(rects, w, h):
        out = []
        for c, r, cw, rh in rects:
            x, y = c * TILE, r * TILE
            out.append((x, y, min(cw * TILE, w - x), min(rh * TILE, h - y)))
        return out

    @staticmethod
    def _stripes(w, h):
        n = min(8, ENCODERS._max_workers) if h >= 512 else 1
        step = -(-h // n // 16) * 16
        return [(0, y, w, min(step, h - y)) for y in range(0, h, step)]

    def _pack(self, arr, cs, rects, quality, sub, key=False):
        t0 = time.perf_counter()
        data = self._pack_inner(arr, cs, rects, quality, sub, key)
        self.stats["enc"] += time.perf_counter() - t0
        self.stats["frames"] += 1
        self.stats["bytes"] += len(data)
        self.stats["q"] = quality
        return data

    def _pack_inner(self, arr, cs, rects, quality, sub, key=False):
        # Encode rectangles in parallel (libjpeg-turbo releases the GIL).
        futs = [ENCODERS.submit(encode, arr[y:y + rh, x:x + rw], quality, sub, cs)
                for x, y, rw, rh in rects]
        parts = [f.result() for f in futs]
        head = [struct.pack("<BBH", 0x46, 1 if key else 0, len(rects))]
        for (x, y, rw, rh), data in zip(rects, parts):
            head.append(struct.pack("<HHHHI", x, y, rw, rh, len(data)))
        return b"".join(head + parts)

    def _release_dx(self):
        if self.grab is not None:
            self.grab.stop()
            self.grab = None
        if self.dx is not None:
            try:
                self.dx.release()
            except Exception:
                pass
            self.dx = None

    def close(self):
        self._release_dx()
        if self.sct is not None:
            self.sct.close()
            self.sct = None


def list_monitors():
    with _MSS() as sct:
        return [{"index": i, "left": m["left"], "top": m["top"],
                 "width": m["width"], "height": m["height"]}
                for i, m in enumerate(sct.monitors) if i > 0]


# --------------------------------------------------------------------------
# HTTP + WebSocket server
# --------------------------------------------------------------------------
def make_app(cfg):
    pin = cfg["pin"]

    def authorized(request):
        # The USB-C app arrives through usb_bridge.py on this PC (127.0.0.1):
        # a physical cable to a trusted iPad, so no PIN is needed.
        if request.remote == "127.0.0.1":
            return True
        return secrets.compare_digest(request.query.get("pin", ""), pin)

    async def index(request):
        return web.FileResponse(os.path.join(WEB_DIR, "index.html"),
                                headers={"Cache-Control": "no-cache"})

    async def monitors(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        return web.json_response({"monitors": list_monitors(), "input": INPUT.ok,
                                  "fastjpeg": simplejpeg is not None,
                                  "wired": is_wired(request.remote),
                                  "usb": request.remote == "127.0.0.1"})

    async def ws_handler(request):
        if not authorized(request):
            return web.Response(status=403, text="bad pin")
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=1 << 20, compress=False)
        await ws.prepare(request)
        sock = request.transport.get_extra_info("socket") if request.transport else None
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        q = request.query
        sess = Session(int(q.get("monitor", "-1")), q.get("mode", "balanced"))
        fps = max(10, min(60, int(q.get("fps", "60"))))
        loop = asyncio.get_running_loop()
        cap_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")  # mss is per-thread
        mon = await loop.run_in_executor(cap_pool, sess.monitor)
        print(f"[+] iPad connected from {request.remote} -> screen "
              f"{mon['width']}x{mon['height']} at ({mon['left']},{mon['top']})")

        state = {"in_flight": 0, "force": True, "seq": 0, "sent": {}, "rtt": 0.03,
                 "rtt_sum": 0.0, "acks": 0}
        can_send = asyncio.Event()
        can_send.set()
        wake = asyncio.Event()       # input arrived: capture again right away

        def to_screen(m):
            mon = sess.mon                 # current geometry (follows resolution/scale changes)
            return (mon["left"] + max(0.0, min(1.0, float(m["x"]))) * (mon["width"] - 1),
                    mon["top"] + max(0.0, min(1.0, float(m["y"]))) * (mon["height"] - 1))

        async def reader():
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                t = m.get("t")
                try:
                    if t == "ack":
                        sent_at = state["sent"].pop(m.get("seq"), None)
                        if sent_at is not None:
                            rtt = time.perf_counter() - sent_at
                            state["rtt"] = 0.8 * state["rtt"] + 0.2 * rtt
                            # Slow link: lighten frames. Fast link: restore quality.
                            state["rtt_sum"] += rtt
                            state["acks"] += 1
                            # Frames taking long to arrive = the link is full:
                            # lighten frames fast, restore quality slowly.
                            if state["rtt"] > 0.06:
                                sess.adapt = min(30, sess.adapt + 3)
                            elif state["rtt"] < 0.03:
                                sess.adapt = max(0, sess.adapt - 1)
                        state["in_flight"] = max(0, state["in_flight"] - 1)
                        if state["in_flight"] < MAX_IN_FLIGHT:
                            can_send.set()
                        continue
                    if t == "move":
                        state["moves"] = state.get("moves", 0) + 1
                        INPUT.move(*to_screen(m))
                    elif t == "down":
                        INPUT.move(*to_screen(m))
                        INPUT.button(int(m.get("b", 0)), True)
                    elif t == "up":
                        if "x" in m:
                            INPUT.move(*to_screen(m))
                        INPUT.button(int(m.get("b", 0)), False)
                    elif t == "click":
                        INPUT.move(*to_screen(m))
                        b = int(m.get("b", 0))
                        for _ in range(int(m.get("n", 1))):
                            INPUT.button(b, True)
                            INPUT.button(b, False)
                    elif t == "scroll":
                        if "x" in m:
                            INPUT.move(*to_screen(m))
                        INPUT.scroll(m.get("dx", 0), m.get("dy", 0))
                    elif t == "text":
                        INPUT.text(str(m.get("s", ""))[:2000])
                    elif t == "key":
                        INPUT.key(str(m.get("k", "")), m.get("mods", []))
                    elif t == "mode":
                        sess.set_profile(str(m.get("mode", "balanced")))
                        state["force"] = True
                    elif t == "refresh":
                        state["force"] = True
                    elif t == "display":
                        # Extend: the iPad is its own screen. Mirror: it shows a copy of
                        # the laptop screen. The stream follows the change by itself.
                        mode = {"extend": "/extend", "clone": "/clone"}.get(m.get("mode"))
                        if mode and IS_WINDOWS:
                            print(f"[screen] switching Windows to {'mirror' if mode == '/clone' else 'extend'} mode")
                            import subprocess
                            subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                             "System32", "DisplaySwitch.exe"), mode])
                    wake.set()
                except Exception as e:  # never let a bad event kill the session
                    print("input error:", e)

        async def writer():
            interval = 1.0 / fps
            sess.wait = interval
            last_cursor = None
            last_dims = None
            next_report = time.perf_counter() + 5
            while not ws.closed:
                await can_send.wait()
                start = time.perf_counter()
                if start >= next_report:       # live speed readout in the PC window
                    st, n = sess.stats, max(1, sess.stats["frames"])
                    secs = start - next_report + 5
                    acks = max(1, state["acks"])
                    print(f"[speed] {st.get('new', 0) / secs:4.0f} screen fps | "
                          f"{st['frames'] / secs:4.0f} updates/s | "
                          f"capture {st['cap'] * 1000 / max(1, st.get('caps', 0)):4.1f} ms ({sess.backend}) | "
                          f"work {(st.get('work', 0) - st.get('idle', 0)) * 1000 / max(1, st.get('steps', 0)):4.1f} ms | "
                          f"encode {st['enc'] * 1000 / n:4.1f} ms | "
                          f"{st['bytes'] / n / 1024:5.0f} KB/update | "
                          f"{st['bytes'] * 8 / 1e6 / secs:5.1f} Mbit/s | "
                          f"round trip {state['rtt_sum'] * 1000 / acks:4.0f} ms | "
                          f"quality {st.get('q', '-')} | "
                          f"touch moves {state.get('moves', 0) / secs:3.0f}/s", flush=True)
                    state["moves"] = 0
                    sess.stats = {"frames": 0, "cap": 0.0, "enc": 0.0, "bytes": 0}
                    state["rtt_sum"], state["acks"] = 0.0, 0
                    next_report = start + 5
                force, state["force"] = state["force"], False
                try:
                    packet, dims = await loop.run_in_executor(cap_pool, sess.step, force)
                except Exception as e:  # e.g. the screen was just removed/resized
                    print("capture error:", e)
                    state["force"] = True
                    await asyncio.sleep(0.5)
                    continue
                if ws.closed:
                    break
                if dims != last_dims:
                    last_dims = dims
                    await ws.send_str(json.dumps({"type": "info", "width": dims[0],
                                                  "height": dims[1], "monitor": sess.mon}))
                cur = INPUT.cursor()
                if cur is not None:
                    cx = (cur[0] - sess.mon["left"]) * sess.scale
                    cy = (cur[1] - sess.mon["top"]) * sess.scale
                    vis = 0 <= cx < dims[0] and 0 <= cy < dims[1]
                    c = (round(cx), round(cy), vis)
                    if c != last_cursor:
                        last_cursor = c
                        await ws.send_str(json.dumps({"type": "cursor", "x": c[0],
                                                      "y": c[1], "v": vis}))
                if packet is None and time.perf_counter() - state.get("last_tx", 0) > 1.5:
                    # Keep-alive so the iPad can tell a still screen from a dead link.
                    await ws.send_str('{"type":"ping"}')
                    state["last_tx"] = time.perf_counter()
                if packet is not None:
                    state["last_tx"] = time.perf_counter()
                    state["seq"] = (state["seq"] + 1) & 0xFFFFFFFF
                    seq = state["seq"]
                    state["sent"][seq] = time.perf_counter()
                    if len(state["sent"]) > 32:
                        state["sent"].pop(next(iter(state["sent"])))
                    state["in_flight"] += 1
                    if state["in_flight"] >= MAX_IN_FLIGHT:
                        can_send.clear()
                    await ws.send_bytes(struct.pack("<I", seq) + packet)
                # Sleep until the next tick, but wake early when input arrives
                # so the result of a tap shows up as soon as possible.
                if sess.grab is not None:
                    continue           # paced by the screen: the step waits for its next frame
                remaining = interval - (time.perf_counter() - start)
                if remaining > 0:
                    wake.clear()
                    try:
                        await asyncio.wait_for(wake.wait(), remaining)
                        await asyncio.sleep(0.004)  # let the app repaint first
                    except asyncio.TimeoutError:
                        pass

        rtask = asyncio.create_task(reader())
        wtask = asyncio.create_task(writer())
        try:
            await asyncio.wait({rtask, wtask}, return_when=asyncio.FIRST_COMPLETED)
            for t in (rtask, wtask):
                if t.done() and t.exception():
                    print("session error:", t.exception())
        finally:
            for t in (rtask, wtask):
                t.cancel()
            await loop.run_in_executor(cap_pool, sess.close)
            cap_pool.shutdown(wait=False)
            print(f"[-] iPad disconnected ({request.remote})")
        return ws

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/monitors", monitors)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/", WEB_DIR, show_index=False)
    return app


def main():
    ap = argparse.ArgumentParser(description="Stream a Windows monitor to an iPad")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--new-pin", action="store_true", help="generate a new pairing PIN")
    ap.add_argument("--keep-screen", action="store_true",
                    help="don't turn the iPad's virtual screen on/off automatically")
    args = ap.parse_args()

    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 or newer is required (3.15 recommended).")
    if args.new_pin and os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
    cfg = load_config()

    print("=" * 62)
    print(f" iPad Display host  (Python {sys.version.split()[0]})")
    print("=" * 62)
    print(" On your iPad, open Safari and go to:")
    addrs = lan_addresses()
    for a in sorted(addrs, key=lambda a: not is_wired(a)) or ["<this-PC's-IP>"]:
        tag = "   <- cable (best)" if is_wired(a) else "   (Wi-Fi)"
        print(f"     http://{a}:{args.port}{tag}")
    if not any(is_wired(a) for a in addrs):
        print("   (No cable link found. For a wired connection, see WIRED.md.)")
    print(f"\n Pairing PIN:  {cfg['pin']}")
    if IS_WINDOWS and not args.keep_screen:
        import vdd
        if vdd.available():
            print("\n Turning on the iPad's virtual screen (it turns off when you close this window)...")
            res = vdd.turn_on(lambda: len(list_monitors()))
            if res == "failed":
                print("   It didn't appear. Check vdd.log in this folder, or turn it on in Device Manager.")
        else:
            print("\n Tip: run display-setup.bat once, so the iPad's virtual screen turns on")
            print("      with this window and off when you close it.")
    print("\n Screens found:")
    for m in list_monitors():
        print(f"   #{m['index']}: {m['width']}x{m['height']} at ({m['left']},{m['top']})")
    if not IS_WINDOWS:
        print("\n (Not on Windows: streaming works, but touch input is disabled.)")
    if simplejpeg is None:
        print("\n Tip: 'pip install simplejpeg' gives a much faster stream.")
    print("\n Keep this window open. Press Ctrl+C to stop.")
    print("=" * 62)
    print(" USB-C: plug in the iPad and open the iPad Display app (see USB-C.md).")
    print("=" * 62)
    app = make_app(cfg)
    import usb_bridge
    usb_bridge.start(app, args.port)
    try:
        web.run_app(app, host="0.0.0.0", port=args.port, print=None)
    except OSError as e:
        if getattr(e, "errno", None) in (10048, 98, 48):
            sys.exit("\n The host is already running in another window. Close that "
                     "window first (or end 'Python' in Task Manager), then run start.bat again.")
        raise


if __name__ == "__main__":
    main()
