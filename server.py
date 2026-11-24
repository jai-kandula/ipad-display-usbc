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
import base64
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

# GPU capture (dxcam, through comtypes) and PC sound (soundcard) both set up
# Windows COM on this thread. soundcard asks for the multithreaded kind; comtypes
# defaults to the other kind and then refuses to load at all. Agree on
# multithreaded before either is imported.
if IS_WINDOWS:
    sys.coinit_flags = 0      # COINIT_MULTITHREADED

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
import video  # noqa: E402  (H.264 for moving content)
import dxdirty  # noqa: E402  (GPU capture of only what changed)
import pcaudio  # noqa: E402  (PC sound on the iPad)
import clip  # noqa: E402  (shared clipboard)
import deck  # noqa: E402  (profiles and the button deck)
import screenshape  # noqa: E402  (portrait, resolution and text size)
import pen  # noqa: E402  (Apple Pencil pressure and tilt)
import winpin  # noqa: E402  (put app windows on the iPad)
import hostsettings  # noqa: E402  (settings kept on the PC)
import focus  # noqa: E402  (the app in front, for app-aware buttons)
import ha  # noqa: E402  (Home Assistant buttons)
import stats  # noqa: E402  (live tiles: CPU, GPU, now playing)
import screens  # noqa: E402  (which screen is the iPad's, the laptop's, a monitor)
import tlsutil  # noqa: E402  (encrypted Wi-Fi)
import clips  # noqa: E402  (replay clips from any clipping app)
import obs  # noqa: E402  (OBS Studio buttons and tile)
import callouts  # noqa: E402  (map callouts from your own pictures)
import dev  # noqa: E402  (build/test tasks, Git, logs, snippets)
import mc  # noqa: E402  (Minecraft server over RCON)
import study  # noqa: E402  (focus timer and distraction blocker)
import ical  # noqa: E402  (calendar from a private iCal link)
import mathpad  # noqa: E402  (math pad: numbers, algebra, units)
import school  # noqa: E402  (lab capture and PDF notes)
import capture  # noqa: E402  (quick-capture inbox)
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

_DX_LOCK = threading.Lock()      # re-listing dxcam's screens

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
    DXCAM_ERROR = None
except Exception as _e:
    dxcam = None
    DXCAM_ERROR = f"{type(_e).__name__}: {_e}"   # installed but failed to load: say why

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
CONFIG_PATH = os.path.join(HERE, "config.json")

TILE = 64                     # change-detection grid, multiple of 16 for clean JPEG blocks
SMALL_CHANGE = 0.08           # < 8% of the screen changed: send straight at high quality
FULL_FRAME = 0.45             # > 45% changed: send the whole screen as stripes
REPORT_SECS = 2          # how often the [speed] line is printed and sent to the iPad
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

    # Keys Windows marks as "extended" (arrows, Win, media keys...): games and
    # apps that read scan codes need the flag to tell them from the numpad.
    EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
                0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5} | set(range(0xA6, 0xB8))

    def _scan_key(self, vk, up=False):
        scan = self.user32.MapVirtualKeyW(vk, 0) & 0xFF
        flags = (self.KEYUP if up else 0) | (0x0001 if vk in self.EXTENDED else 0)
        return self._key(vk, scan, flags)

    def combo(self, vk, mods=()):
        """Press a key by virtual-key code with modifiers held (the button deck).
        Fills in the scan code too, so games and global hotkeys see it."""
        if not self.ok:
            return
        mod_vks = [self.VK[m] for m in mods if m in self.VK]
        items = [self._scan_key(m) for m in mod_vks]
        items += [self._scan_key(vk), self._scan_key(vk, up=True)]
        items += [self._scan_key(m, up=True) for m in reversed(mod_vks)]
        self._send(items)


INPUT = Input()
PEN = pen.Pen()
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
    touches the dxcam camera, and it releases the camera when it ends.

    One grabber per screen is shared by every connection showing that screen
    (dxcam hands out the same camera for the same screen, and two threads
    grabbing from it at once makes DXGI fail). Each connection keeps its own
    place with a frame number, so none of them steals frames from another."""

    def __init__(self, cam, key):
        super().__init__(daemon=True, name="gpu-grab")
        self.cam, self.key = cam, key
        self.cond = threading.Condition()
        self.frame, self.seq = None, 0
        self.err = None
        self.stopping = False
        self.cap_total = 0.0       # grab time and count, ever (readers keep deltas)
        self.refs = 0
        self.gap = GRAB_GAP        # shortest time between grabs (the fastest viewer's frame rate)
        self.dirty = None          # dxdirty.DirtyGrab: copies only what changed into one frame
        self.hist = []             # (frame number, changed rects or None for all) of recent frames

    def _start_dirty(self):
        try:
            self.dirty = dxdirty.DirtyGrab(self.cam)
            if not getattr(_Grabber, "_said_dirty", False):
                _Grabber._said_dirty = True
                print("[capture] GPU capture copies only the parts of the screen that changed.", flush=True)
        except dxdirty.Unsupported as e:
            self.dirty = None
            print(f"[capture] GPU capture copies the whole screen each frame ({e}).", flush=True)

    def _grab_dirty(self):
        """(frame, rects) from the changed-parts grabber, (None, None) if nothing new."""
        try:
            rects = self.dirty.grab()
        except dxdirty.ScreenChanged:
            raise
        except Exception as e:                # anything unexpected: plain dxcam grabs from now on
            print(f"[capture] copying only what changed stopped working ({type(e).__name__}: {e}); "
                  "copying the whole screen instead.", flush=True)
            self.dirty = None
            return None, None
        return (None, None) if rects is None else (self.dirty.frame, rects)

    def run(self):
        nxt = 0.0
        try:
            self._start_dirty()
        except Exception as e:
            print(f"[capture] {e}", flush=True)
            self.dirty = None
        try:
            while not self.stopping:
                # The iPad shows 60 frames a second: on a faster screen (a 144 or
                # 240 Hz monitor) don't copy frames it would never show.
                wait = nxt - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                t0 = time.perf_counter()
                rects = None
                try:
                    if self.dirty is not None:
                        f, rects = self._grab_dirty()
                    else:
                        f = self.cam.grab(new_frame_only=True)
                except Exception as e:      # display mode changed, driver reset, ...
                    with self.cond:
                        self.err = e
                        self.cond.notify_all()
                    return
                if f is None:
                    time.sleep(GRAB_POLL)
                    continue
                nxt = t0 + self.gap
                dt = time.perf_counter() - t0
                with self.cond:
                    self.frame = f          # newest frame wins; older ones are dropped
                    self.seq += 1
                    self.hist.append((self.seq, rects))
                    if len(self.hist) > 120:
                        del self.hist[:60]
                    self.cap_total += dt
                    self.cond.notify_all()
        finally:
            try:
                self.cam.release()
            except Exception:
                pass

    def take(self, seen, timeout):
        """(frame newer than `seen` or None after timeout, its number, grab seconds total,
        changed rects since `seen` or None if the whole frame may have changed,
        True if the frame is the grabber's own buffer that it keeps writing into)."""
        with self.cond:
            if self.seq <= seen and self.err is None:
                self.cond.wait(timeout)
            if self.err is not None:
                raise self.err
            if self.seq <= seen:
                return None, seen, self.cap_total, None, False
            live = self.dirty is not None and self.frame is self.dirty.frame
            rects = None
            if live and self.hist and self.hist[0][0] <= seen + 1:
                rects = []
                for n, r in self.hist:
                    if n > seen:
                        if r is None:
                            rects = None
                            break
                        rects.extend(r)
                if rects is not None:
                    h, w = self.frame.shape[:2]
                    rects = dxdirty.merge(rects, w, h, TILE)
            return self.frame, self.seq, self.cap_total, rects, live

    @property
    def broken(self):
        return self.err is not None or not self.is_alive()

    def stop(self):
        self.stopping = True
        if threading.current_thread() is not self:
            self.join(timeout=1.0)


_GRABBERS = {}                     # (device, output) -> shared _Grabber
IN_USE = {}                        # id(Session) -> (left, top) of the screen it shows (several iPads)
# Tunables (the self-test measures them on the real PC).
GRAB_GAP = 1.0 / float(os.environ.get("IPD_GRAB_MAX_FPS", "60")) - 0.002   # slack keeps a 60 Hz screen at 60
GRAB_POLL = float(os.environ.get("IPD_GRAB_POLL_MS", "1")) / 1000.0         # look for a new frame this often


def _grabber_get(key, open_cam):
    """The shared grabber for a screen, starting one if needed. Call with _DX_LOCK held."""
    g = _GRABBERS.get(key)
    if g is not None and g.broken:
        g.stop()                   # (its thread released the camera on the way out)
        g = None
    if g is None:
        g = _Grabber(open_cam(), key)
        g.start()
        _GRABBERS[key] = g
    g.refs += 1
    return g


def _grabber_put(g):
    with _DX_LOCK:
        g.refs -= 1
        if g.refs <= 0:
            if _GRABBERS.get(g.key) is g:
                del _GRABBERS[g.key]
            g.stop()


VIDEO_REST_SECS = 45      # close the H.264 encoder after this long with no motion (see Session._video_rest)
_VREST_SAID = False
VIDEO_OPEN_AFTER = 1.0    # a new connection opens its encoder ahead of motion only once it has lasted this long
_PARKED = []              # [(video.Encoder, parked at)] left by closed connections, for the next one
_PARK_LOCK = threading.Lock()
_PARK_MAX = 2


def _park_take(w, h):
    """An open encoder a closed connection left behind, if one fits. Reusing it
    skips opening NVENC, which holds Python's lock (and so the picture) while
    the GPU sets up: a reconnecting iPad starts with no hitch and no extra
    video memory."""
    with _PARK_LOCK:
        for i, (enc, _) in enumerate(_PARKED):
            if (enc.w, enc.h) == (w, h):
                del _PARKED[i]
                return enc
    return None


def _park_put(enc):
    """Keep a closed connection's encoder for VIDEO_REST_SECS, then close it."""
    with _PARK_LOCK:
        _PARKED.append((enc, time.monotonic()))
        extra = [e for e, _ in _PARKED[:-_PARK_MAX]]
        del _PARKED[:-_PARK_MAX]
    for e in extra:
        e.close()
    t = threading.Timer(VIDEO_REST_SECS, _park_sweep)
    t.daemon = True
    t.start()


def _park_sweep():
    now = time.monotonic()
    with _PARK_LOCK:
        old = [e for e, at in _PARKED if now - at >= VIDEO_REST_SECS - 0.5]
        _PARKED[:] = [(e, at) for e, at in _PARKED if e not in old]
    for e in old:
        e.close()


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
        self.dx_next = 0.0         # when to (re)try GPU capture
        self.dx_said = None        # last GPU message printed, so it isn't repeated
        self.grab = None           # shared _Grabber feeding GPU frames
        self.grab_seq, self.grab_cap = 0, 0.0
        self.wait = 1 / 60         # how long a step waits for a new GPU frame
        self.max_fps = 60          # the iPad's screen rate (120 on ProMotion iPads)
        self.recorder = None       # recorder.Recorder while recording
        self.backend = "GDI"
        self.last = None           # last captured (array, colourspace)
        self.stats = {"frames": 0, "cap": 0.0, "enc": 0.0, "bytes": 0}
        self._diff = None          # reused buffer for the changed-pixel mask
        self.video_wanted = False  # the iPad can decode H.264 and asked for it
        self._vlast = 0.0          # when the encoder last made a frame
        self._vrest = False        # encoder closed while nothing moves (frees GPU memory)
        self.video = None          # video.Encoder for moving content
        self.video_key = False     # next video frame must be a key frame
        self.video_failed = False
        self._born = time.perf_counter()

    def set_profile(self, name):
        self.profile = PROFILES.get(name, PROFILES["balanced"])
        self.reset = True          # full refresh on the next capture (thread-safe flag)

    def monitor(self):
        if self.sct is None:
            self.sct = _MSS()
        self.mon = dict(self._pick(self.sct.monitors))
        IN_USE[id(self)] = (self.mon["left"], self.mon["top"])
        return self.mon

    def _pick(self, mons):
        """The screen asked for, or (auto, or that screen is gone) the iPad's
        own virtual screen, never a gaming monitor by accident."""
        if 0 < self.monitor_index < len(mons):
            return mons[self.monitor_index]
        busy = {v for k, v in IN_USE.items() if k != id(self)}     # screens other iPads show
        s = screens.pick_ipad(screens.describe(mons), busy)
        return mons[s["index"]] if s else mons[-1]

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
            new = dict(self._pick(mons))
        except Exception:
            return
        if all(new[k] == self.mon[k] for k in ("left", "top", "width", "height")):
            return
        print(f"[screen] changed to {new['width']}x{new['height']} at "
              f"({new['left']},{new['top']}), following it.")
        if winpin.target["mon"] == self.mon:
            winpin.target["mon"] = dict(new)
        self.mon = new
        IN_USE[id(self)] = (new["left"], new["top"])
        if self.sct is not None:
            self.sct.close()
        self.sct = _MSS()
        self._release_dx()
        self.dx_next, self.backend = 0.0, "GDI"
        self.last = None
        self.reset = True

    def _say(self, msg):
        if msg != self.dx_said:
            self.dx_said = msg
            print(msg)

    def _open_dxgi(self):
        """Find the DXGI output for this screen and start (or join) its grabber."""
        self.dx_next = time.perf_counter() + 5.0     # if this fails, try again in 5 s
        if dxcam is None:
            self.dx_next = float("inf")
            if not getattr(Session, "_said_no_dxcam", False):
                Session._said_no_dxcam = True
                if DXCAM_ERROR:
                    print(f"GPU capture unavailable (dxcam failed to load: {DXCAM_ERROR}), using GDI.")
                else:
                    print("GPU capture unavailable (the dxcam package isn't installed), using GDI. "
                          "Run start.bat once with internet to install it.")
            return
        try:
            fac = getattr(dxcam, "__factory")
            with _DX_LOCK:
                found = self._find_output(fac)
                if not found and not _GRABBERS:
                    # dxcam lists the screens once, when it loads. A screen added
                    # since (the iPad's virtual screen being switched on) isn't in
                    # that list, so look again (only while no camera is open).
                    fac.__init__()
                    found = self._find_output(fac)
                if not found:
                    self._say(f"GPU capture couldn't find this screen ({self.mon['width']}x{self.mon['height']} at "
                              f"{self.mon['left']},{self.mon['top']}), using GDI for now.")
                    return
                d, o = found
                self.grab = _grabber_get((d, o), lambda: dxcam.create(
                    device_idx=d, output_idx=o, output_color="BGRA", processor_backend="numpy"))
            self.grab_seq, self.grab_cap = self.grab.seq, self.grab.cap_total
            self.grab.gap = min(self.grab.gap, 1.0 / self.max_fps - 0.002)
            if self.backend != "GPU" and self.dx_said is not None:
                print("GPU capture is back on.")
            self.backend, self.dx_said = "GPU", None
        except Exception as e:
            self._say(f"GPU capture unavailable, using GDI for now: {e}")
            self.grab = None

    def _find_output(self, fac):
        m = self.mon
        for d, outs in enumerate(fac.outputs):
            for o, out in enumerate(outs):
                out.update_desc()
                rc = out.desc.DesktopCoordinates
                if ((rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
                        == (m["left"], m["top"], m["width"], m["height"])):
                    return d, o           # (dxcam turns portrait screens upright itself)
        return None

    def _capture(self):
        """Returns (array, colourspace), or None if the screen hasn't changed."""
        if IS_WINDOWS and self.grab is None and time.perf_counter() >= self.dx_next:
            self._open_dxgi()
        if self.grab is not None:
            try:
                tw = time.perf_counter()
                frame, seq, cap, rects, live = self.grab.take(self.grab_seq, self.wait)
                self.stats["idle"] = self.stats.get("idle", 0.0) + time.perf_counter() - tw
            except Exception as e:      # display mode changed, driver reset, ...
                self._say(f"GPU capture stopped, using GDI for a moment: {e}")
                self._release_dx()
                self.backend = "GDI"
                self.dx_next = time.perf_counter() + 2.0
            else:
                n, t = seq - self.grab_seq, cap - self.grab_cap
                self.grab_seq, self.grab_cap = seq, cap
                self.stats["cap"] += t
                self.stats["caps"] = self.stats.get("caps", 0) + n
                self.stats["new"] = self.stats.get("new", 0) + n
                return None if frame is None else (frame, "BGRA", rects, live)
        t0 = time.perf_counter()
        shot = self.sct.grab(self.mon)
        self.stats["cap"] += time.perf_counter() - t0
        self.stats["caps"] = self.stats.get("caps", 0) + 1
        self.stats["new"] = self.stats.get("new", 0) + 1
        w, h = shot.size
        return np.frombuffer(shot.raw, dtype=np.uint8).reshape(h, w, 4), "BGRA", None, False

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
        if cap is not None and self.recorder is not None:   # recording reuses this frame: no second capture
            self.recorder.offer(cap[0].copy() if cap[3] else cap[0])
        unchanged = cap is None
        if unchanged:
            if self.last is None:
                return None, self.dims
            cap = self.last
        self.last = cap
        arr, cs, rects, live = cap
        h, w = arr.shape[:2]
        if self.video_wanted and cs == "BGRA" and not self._vrest and \
                (self.video is None or (self.video.w, self.video.h) != (w, h)):
            # Ready before anything moves: at once from a parked encoder, else
            # opened once the connection has lasted a moment (quick reconnects
            # don't each open one).
            if getattr(self, "_vready", None) is None and getattr(self, "_vprep", None) != (w, h):
                self._vready = _park_take(w, h)
                if self._vready is not None:
                    self._vprep = (w, h)
            if time.perf_counter() - self._born > VIDEO_OPEN_AFTER:
                self._video_prepare(w, h)
        elif self.video is not None and time.perf_counter() - self._vlast > VIDEO_REST_SECS:
            self._video_rest()
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
            self.prev = cur.copy() if live else cur
            self.blurry = np.zeros((trows, tcols), bool)
            self.last_change = now
            return self._pack(arr, cs, self._stripes(w, h), hi, prof["hi_sub"], key=True), self.dims

        if unchanged:
            mask = None
        elif live and rects is not None:      # only these parts can have changed: look only there
            mask = self._rect_mask(cur, rects, trows, tcols, px)
        else:
            if self._diff is None or self._diff.shape != cur.shape:
                self._diff = np.empty(cur.shape, bool)
            diff = np.not_equal(cur, self.prev, out=self._diff)
            if diff.any():
                mask = np.logical_or.reduceat(
                    np.logical_or.reduceat(diff, np.arange(0, h, TILE), axis=0),
                    np.arange(0, w // px, TILE // px), axis=1)
                if live:
                    np.copyto(self.prev, cur)   # the grabber keeps writing into cur
                else:
                    self.prev = cur             # every capture is a new array: no copy needed
            else:
                mask = None
        if mask is None:
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
        frac = mask.mean()
        self._note_change(mask, frac)

        lo_sub = prof.get("lo_sub", "420")
        if frac < prof.get("small", SMALL_CHANGE):   # typing, hovering: crisp straight away
            self.blurry &= ~mask
            return self._pack(arr, cs, self._px(tiles_to_rects(mask), w, h),
                              hi, prof["hi_sub"]), self.dims
        # Bigger changes (scrolling, dragging, video): H.264 when the iPad can
        # decode it. It is far smaller than JPEG for motion, and the areas it
        # touched are re-sent as sharp JPEG once things settle, as before.
        packet = self._video(arr, cs)
        if packet is not None:
            if frac > FULL_FRAME:
                self.blurry[:] = True
            else:
                self.blurry |= mask
            return packet, self.dims
        if frac > FULL_FRAME:             # scrolling / video: whole screen, fast
            self.blurry[:] = True
            return self._pack(arr, cs, self._stripes(w, h), lo, lo_sub), self.dims
        self.blurry |= mask
        return self._pack(arr, cs, self._px(tiles_to_rects(mask), w, h), lo, lo_sub), self.dims

    def _rect_mask(self, cur, rects, trows, tcols, px):
        """Changed tiles, comparing only inside rects (on the tile grid), or None.
        Keeps self.prev up to date there."""
        mask = np.zeros((trows, tcols), bool)
        found = False
        for l, t, r, b in rects:
            lx, rx = l // px, r // px
            c, p = cur[t:b, lx:rx], self.prev[t:b, lx:rx]
            d = c != p
            if not d.any():
                continue
            sub = np.logical_or.reduceat(np.logical_or.reduceat(d, np.arange(0, b - t, TILE), axis=0),
                                         np.arange(0, rx - lx, TILE // px), axis=1)
            mask[t // TILE:t // TILE + sub.shape[0], l // TILE:l // TILE + sub.shape[1]] |= sub
            p[...] = c
            found = True
        return mask if found else None

    def _note_change(self, mask, frac):
        """For the [speed] line: how much of the screen changes, and where small changes are."""
        st = self.stats
        st["area"] = st.get("area", 0.0) + frac
        st["areas"] = st.get("areas", 0) + 1
        if frac < 0.05:
            rows, cols = np.nonzero(mask)
            if rows.size:
                box = (int(cols.min()) * TILE, int(rows.min()) * TILE, int(cols.max() + 1) * TILE, int(rows.max() + 1) * TILE)
                b = st.get("small_box")
                st["small_box"] = box if b is None else (min(b[0], box[0]), min(b[1], box[1]), max(b[2], box[2]), max(b[3], box[3]))
                st["small"] = st.get("small", 0) + 1

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
            _grabber_put(self.grab)
            self.grab = None

    def _video_prepare(self, w, h):
        """Open the H.264 encoder on another thread. Opening NVENC takes up to a
        second; doing it in the stream froze the picture that long the first
        time something moved. Until it's ready, motion goes out as pictures."""
        if getattr(self, "_vprep", None) == (w, h) or self.video_failed:
            return
        self._vprep = (w, h)
        parked = _park_take(w, h)
        if parked is not None:
            self._vready = parked
            return

        def work():
            try:
                enc = video.Encoder(w, h)
            except Exception as e:
                self._vready = e
                return
            if getattr(self, "_closed", False):
                _park_put(enc)                # opened just as the iPad left: keep it for the next one
            elif self._vprep != (w, h):
                enc.close()
            else:
                self._vready = enc
        threading.Thread(target=work, daemon=True, name="video-open").start()

    def _video_rest(self):
        """Nothing has moved for a while: close the encoder. NVENC holds a GPU
        context and frame buffers (a few hundred MB of video memory) for as
        long as it's open; it reopens in the background on the next motion,
        with pictures in between."""
        global _VREST_SAID
        if self.video is not None:
            self.video.close()
            self.video = None
        ready, self._vready = getattr(self, "_vready", None), None
        if ready is not None and not isinstance(ready, Exception):
            ready.close()
        self._vprep, self._vrest = None, True
        if not _VREST_SAID:
            _VREST_SAID = True
            print(f"[video] Nothing has moved for {VIDEO_REST_SECS} s, so the video encoder is closed to free "
                  "GPU memory. It opens again by itself when something moves.", flush=True)

    def _video(self, arr, cs):
        if not self.video_wanted or self.video_failed or cs != "BGRA":
            return None
        h, w = arr.shape[:2]
        self._vrest = False
        try:
            if self.video is None or (self.video.w, self.video.h) != (w, h):
                ready, self._vready = getattr(self, "_vready", None), None
                if isinstance(ready, Exception):
                    raise ready
                if ready is None or (ready.w, ready.h) != (w, h):
                    if ready is not None:
                        ready.close()
                    self._video_prepare(w, h)
                    return None               # pictures until the encoder is open
                if self.video is not None:
                    self.video.close()
                self.video = ready
                self.video_key = True
                self._vlast = time.perf_counter()
            t0 = time.perf_counter()
            body, is_key = self.video.encode(arr, key=self.video_key)
            self._vlast = time.perf_counter()
            self.stats["enc"] += self._vlast - t0
        except Exception as e:
            print("[video] turned off, using pictures instead:", e)
            self.video_failed = True
            return None
        if body is None:
            return None
        self.video_key = False
        data = struct.pack("<BBH", 0x56, 1 if is_key else 0, 0) + body
        self.stats["frames"] += 1
        self.stats["bytes"] += len(data)
        self.stats["vid"] = self.stats.get("vid", 0) + 1
        return data

    def close(self):
        self._closed = True
        ready, self._vready = getattr(self, "_vready", None), None
        for enc in (self.video, ready):
            if enc is not None and not isinstance(enc, Exception) and not self.video_failed:
                _park_put(enc)          # the next connection reuses it
            elif enc is not None and not isinstance(enc, Exception):
                enc.close()
        self.video = None
        IN_USE.pop(id(self), None)
        if self.recorder is not None:
            path, secs = self.recorder.stop()
            self.recorder = None
            if path:
                print(f"[record] saved {os.path.basename(path)} ({secs:.0f} s)", flush=True)
        if self.video is not None:
            self.video.close()
            self.video = None
        self._release_dx()
        if self.sct is not None:
            self.sct.close()
            self.sct = None


def screen_change(mon, m):
    """{rotate: portrait|landscape} / {res: [w, h]} / {scale: %} / {get: 1}, on the
    streamed screen. Returns its new state, or the reason it didn't work."""
    try:
        if "rotate" in m:
            screenshape.rotate(mon, m["rotate"] == "portrait")
            print(f"[screen] turned {m['rotate']}", flush=True)
        if "res" in m:
            w, h = (int(v) for v in m["res"])
            screenshape.resize(mon, w, h)
            print(f"[screen] resolution {w}x{h}", flush=True)
        if "scale" in m:
            screenshape.scale(mon, int(m["scale"]))
            print(f"[screen] text size {m['scale']}%", flush=True)
        return {"ok": True, **screenshape.info(mon)}
    except Exception as e:
        if "get" not in m:
            print(f"[screen] couldn't change it: {e}", flush=True)
        return {"ok": False, "error": str(e)}


def list_monitors():
    """Every screen with what it is (ipad / laptop / external), name and refresh rate."""
    with _MSS() as sct:
        return screens.describe(sct.monitors)


# --------------------------------------------------------------------------
# Profiles: what the iPad is for right now (see deck.py)
# --------------------------------------------------------------------------
class Shared:
    """An answer many askers share. The iPad's deck, tiles and menus poll the
    PC several times a second; without this each poll starts its own copy of
    the same slow read (every window, every screen) on its own thread, and
    under a burst those threads hold Python's lock and the picture stutters.
    Now one read runs at a time, everyone waiting gets its answer, and it's
    reused for `ttl` seconds, or until key() says the source changed."""

    def __init__(self, fn, ttl, key=None):
        self.fn, self.ttl, self.key = fn, ttl, key
        self.value, self.at, self.k, self.fut = None, -1e9, None, None

    def clear(self):
        self.at = -1e9

    async def get(self):
        k = self.key() if self.key else None
        if self.fut is None:
            if k == self.k and time.monotonic() - self.at < self.ttl:
                return self.value
            self.fut = asyncio.ensure_future(self._run(k))
        return await asyncio.shield(self.fut)

    async def _run(self, k):
        try:
            v = await asyncio.get_running_loop().run_in_executor(None, self.fn)
            self.value, self.at, self.k = v, time.monotonic(), k
            return v
        finally:
            self.fut = None


# What the iPad is doing right now, for the tray icon.
STATUS = {"streams": 0, "deck_seen": 0.0}
TLS = {"port": None}               # the encrypted Wi-Fi port, when it's on


def status_text():
    if STATUS["streams"]:
        return True, "iPad connected, showing the screen"
    if time.time() - STATUS["deck_seen"] < 12:
        return True, "iPad connected, button deck"
    return False, "waiting for the iPad"


class DeckProfiles:
    """Switches profiles, and turns the iPad's virtual screen on or off to
    match when screen control is set up (display-setup.bat)."""

    def __init__(self):
        self.allowed = False         # main() sets this on Windows unless --keep-screen
        self._ctl = False
        self._next_check = 0.0
        self.lock = threading.Lock()

    @property
    def screen_ctl(self):
        """True once display-setup.bat's tasks exist. Checked again every 10 s
        until then, so running display-setup.bat works without a restart."""
        if self.allowed and not self._ctl and time.monotonic() >= self._next_check:
            checked_before = self._next_check > 0
            self._next_check = time.monotonic() + 10
            import vdd
            self._ctl = vdd.available()
            if self._ctl and checked_before:
                print("[screen] display-setup.bat is set up: the iPad screen now turns on and off "
                      "with the iPad and the profile.", flush=True)
        return self._ctl

    def data(self):
        d = deck.load()
        d["screen_control"] = self.screen_ctl
        # The profile whose buttons follow the app in front right now (if any).
        d["focus"] = focus.match(d["profiles"]) if hostsettings.get("auto_profiles") else None
        d["front"] = focus.state["exe"]
        return d

    def active(self):
        d = deck.load()
        return deck.find(d, d.get("active")) or d["profiles"][0]

    def apply_screen(self, prof):
        if not self.screen_ctl:
            return
        import vdd
        with self.lock:
            if prof.get("screen", True):
                vdd.turn_on(lambda: len(list_monitors()))
            else:
                vdd.turn_off(force=True)

    def switch(self, name, enter=True):
        """Make `name` active and match the screen to it (blocks while Windows
        adds or removes the screen, a few seconds), then run the profile's
        "enter" steps (a context: start the focus timer, open apps, switch the
        OBS scene...). Returns the new data."""
        d = deck.load()
        prof = deck.find(d, name)
        if prof is None:
            raise ValueError(f"no profile named '{name}'")
        d["active"] = name
        d.pop("screen_control", None)
        deck.save(d)
        print(f"[profile] {name}", flush=True)
        self.apply_screen(prof)
        if enter and prof.get("enter"):
            for st in prof["enter"][:deck.MAX_STEPS]:   # one failing step (an app not found) doesn't stop the rest
                try:                      # a "profile" step here switches without its own steps: no loops
                    deck.run({"steps": [st]}, INPUT, lambda n: self.switch(n, enter=False))
                except Exception as e:
                    print(f"[profile] {name}: a step failed: {e}", flush=True)
        return self.data()


DECK = DeckProfiles()


def _deck_key():
    """Changes when the deck's buttons, the app in front or "follow the app" change."""
    try:
        m = os.stat(deck.PATH).st_mtime_ns
    except OSError:
        m = 0
    return m, focus.state["version"], hostsettings.get("auto_profiles")


# Shared answers for what the iPad polls (see Shared).
DECK_JSON = Shared(lambda: json.dumps(DECK.data()).encode(), 1.0, _deck_key)
SCREENS = Shared(list_monitors, 1.0)
WINDOWS = Shared(lambda: winpin.list_windows(), 5.0)


# --------------------------------------------------------------------------
# HTTP + WebSocket server
# --------------------------------------------------------------------------
def page_build():
    """Changes whenever the iPad's page (web/index.html, web/icons.svg) changes.
    The page carries the build it was served as and reloads itself when the
    laptop reports another one: the app keeps its page open for days, so
    without this an updated laptop kept talking to yesterday's page."""
    key = []
    for n in ("index.html", "icons.svg"):
        try:
            st = os.stat(os.path.join(WEB_DIR, n))
            key.append(f"{st.st_mtime_ns:x}{st.st_size:x}")
        except OSError:
            key.append("0")
    return "-".join(key)


def make_app(cfg):
    pin = cfg["pin"]

    def authorized(request):
        # The USB-C app arrives through usb_bridge.py on this PC (127.0.0.1):
        # a physical cable to a trusted iPad, so no PIN is needed.
        if request.remote == "127.0.0.1":
            return True
        if not request.secure and not hostsettings.get("wifi_plain"):
            return False           # Wi-Fi only over the encrypted address (Settings > Connection)
        return secrets.compare_digest(request.query.get("pin", ""), pin)

    page = {"key": None, "body": b""}

    async def index(request):
        # The page with the icon set (web/icons.svg) placed inline, so every
        # <use href="#i-..."> works from the first paint. Built once, again
        # only when a file changes.
        paths = [os.path.join(WEB_DIR, n) for n in ("index.html", "icons.svg")]
        key = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in paths)
        if key != page["key"]:
            with open(paths[0], encoding="utf-8") as f:
                html = f.read()
            try:
                with open(paths[1], encoding="utf-8") as f:
                    icons = f.read().replace("<svg ", '<svg style="display:none" aria-hidden="true" ', 1)
            except OSError:
                icons = ""
            html = html.replace("<head>", f'<head>\n<meta name="page-build" content="{page_build()}">', 1)
            page["body"], page["key"] = html.replace("<!--ICONS-->", icons, 1).encode(), key
        return web.Response(body=page["body"], content_type="text/html", charset="utf-8",
                            headers={"Cache-Control": "no-cache"})

    async def monitors(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        # Off the event loop (reading the screens takes ~10 ms), and shared by askers.
        mons = await SCREENS.get()
        return web.json_response({"monitors": mons, "input": INPUT.ok, "pen": PEN.ok,
                                  "tls_port": TLS["port"], "secure": request.secure,
                                  "fastjpeg": simplejpeg is not None,
                                  "wired": is_wired(request.remote),
                                  "usb": request.remote == "127.0.0.1"})

    async def profiles_get(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        STATUS["deck_seen"] = time.time()          # the deck checks in every 1.5 s
        return web.Response(body=await DECK_JSON.get(), content_type="application/json")

    async def profiles_put(request):
        """Save edited profiles and buttons from the iPad."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        try:
            body = await request.json()
            for k in ("screen_control", "focus", "front"):
                body.pop(k, None)
            await asyncio.get_running_loop().run_in_executor(None, deck.save, body)
        except (ValueError, TypeError, AttributeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        DECK_JSON.clear()
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, DECK.data))

    async def profile_switch(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        try:
            name = (await request.json())["name"]
            data = await asyncio.get_running_loop().run_in_executor(None, DECK.switch, name)
        except (ValueError, KeyError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        DECK_JSON.clear()
        return web.json_response(data)

    async def settings_get(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        return web.json_response(hostsettings.public())

    async def settings_put(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("expected settings")
            before = {k: hostsettings.get(k) for k in ("obs_url", "obs_password")}
            mc_before = {k: hostsettings.get(k) for k in ("mc_host", "mc_port", "mc_password")}
            if "dev_projects" in body:
                body["dev_projects"] = dev.check_projects(body["dev_projects"])
            out = hostsettings.update(body)
            if any(hostsettings.get(k) != v for k, v in before.items()):
                obs.reconnect()
            if any(hostsettings.get(k) != v for k, v in mc_before.items()):
                mc.reset()
            return web.json_response(out)
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)

    async def ha_api(request):
        """GET: Home Assistant entities for the button editor. POST: test the connection."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                return web.json_response({"ok": await loop.run_in_executor(None, ha.check)})
            return web.json_response({"entities": await loop.run_in_executor(None, ha.entities)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def obs_api(request):
        """GET: OBS's scenes, sound sources and state for the button editor. POST: test the connection."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        loop = asyncio.get_running_loop()
        try:
            if request.method == "POST":
                return web.json_response({"ok": await loop.run_in_executor(None, obs.test)})
            return web.json_response(await loop.run_in_executor(None, obs.state))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

    async def dev_api(request):
        """The coding companion: /api/dev/<what> (see dev.py and mc.py)."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        what, q, loop = request.match_info["what"], request.query, asyncio.get_running_loop()
        try:
            body = await request.json() if request.method in ("POST", "PUT") and request.can_read_body else {}
            if not isinstance(body, (dict, list)):
                raise ValueError("expected an object")
            b = body if isinstance(body, dict) else {}
            run = lambda f, *a: loop.run_in_executor(None, f, *a)     # noqa: E731
            since = lambda k: int(q[k]) if q.get(k, "").isdigit() else None   # noqa: E731
            if what == "projects":
                out = {"projects": dev.projects(), "status": dev.status()}
            elif what == "run":
                out = await run(dev.run, str(b.get("project")), str(b.get("task")))
            elif what == "stop":
                await run(dev.stop, str(b.get("project")), str(b.get("task")))
                out = {"ok": True}
            elif what == "output":
                out = dev.output(q.get("project", ""), q.get("task", ""), since("since") or 0) or {"lines": [], "n": 0}
            elif what == "git":
                out = await (run(dev.git_do, str(b.get("project")), str(b.get("do"))) if request.method == "POST"
                             else run(dev.git, q.get("project", "")))
                if isinstance(out, str):
                    out = {"ok": out}
            elif what == "log":
                if q.get("source") == "mc":
                    path = mc.log_path()
                    if not path:
                        raise ValueError("set the server folder in Settings to see its log")
                    out = await run(dev.tail, path, since("since"))
                else:
                    out = await run(dev.project_log, q.get("project", ""), since("since"))
            elif what == "snippets":
                out = {"snippets": await (run(dev.save_snippets, body) if request.method == "PUT" else run(dev.snippets))}
            elif what == "snippet":                         # type it, or put it on the PC's clipboard
                text = str(b.get("text") or "")
                if b.get("do") == "copy":
                    await run(clip.write_text, text)
                else:
                    await run(INPUT.text, text)
                out = {"ok": True}
            elif what == "mc":
                out = await (run(mc.command, str(b.get("cmd", ""))) if request.method == "POST" else run(mc.state))
                if isinstance(out, str):
                    out = {"answer": out}
            else:
                return web.json_response({"error": "unknown"}, status=404)
        except json.JSONDecodeError:
            return web.json_response({"error": "expected JSON"}, status=400)
        except (ValueError, TypeError, RuntimeError, OSError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(out)

    async def school_api(request):
        """School: /api/school/<what> (study.py, ical.py, mathpad.py, school.py)."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        what, q, loop = request.match_info["what"], request.query, asyncio.get_running_loop()
        run = lambda f, *a: loop.run_in_executor(None, f, *a)     # noqa: E731
        try:
            if what == "pdf" and request.method == "GET":       # the PDF itself, by id
                path = await run(school.pdf_path, q.get("id", ""))
                if not path:
                    return web.Response(status=404)
                return web.FileResponse(path, headers={"Content-Type": "application/pdf", "Cache-Control": "no-cache"})
            if what in ("pdf", "photo"):                          # raw uploads
                body = await request.read()
                out = await (run(school.pdf_save, q.get("id", ""), body) if what == "pdf"
                             else run(school.lab_photo, q.get("folder", ""), q.get("name", ""), body))
                return web.json_response(out)
            b = await request.json() if request.method == "POST" and request.can_read_body else {}
            if not isinstance(b, dict):
                raise ValueError("expected an object")
            if what == "study":
                do = b.get("do") if request.method == "POST" else None
                out = (study.start(b.get("focus"), b.get("break")) if do == "start" else study.stop() if do == "stop"
                       else study.pause() if do == "pause" else await run(study.skip) if do == "skip" else study.state())
            elif what == "calendar":
                out = await run(ical.events, q.get("fresh") == "1")
            elif what == "math":
                out = await run(mathpad.solve, str(b.get("q", "")))
            elif what == "lab":
                out = await (run(school.lab_save, b) if request.method == "POST" else run(school.lab_list))
            elif what == "labopen":
                await run(school.lab_open, b.get("folder"))
                out = {"ok": True}
            elif what == "pdfs":
                out = await run(school.pdfs, q.get("fresh") == "1")
            else:
                return web.json_response({"error": "unknown"}, status=404)
        except json.JSONDecodeError:
            return web.json_response({"error": "expected JSON"}, status=400)
        except (ValueError, TypeError, RuntimeError, OSError, ArithmeticError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:                                   # sympy can raise its own kinds
            return web.json_response({"error": f"couldn't work that out ({type(e).__name__})"}, status=400)
        return web.json_response(out)

    async def capture_api(request):
        """Quick capture: /api/capture/<what> (capture.py)."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        what, q, loop = request.match_info["what"], request.query, asyncio.get_running_loop()
        run = lambda f, *a: loop.run_in_executor(None, f, *a)     # noqa: E731
        try:
            if what == "file":
                path = await run(capture.file_path, q.get("name", ""))
                if not path:
                    return web.Response(status=404)
                return web.FileResponse(path, headers={"Cache-Control": "max-age=86400"})
            if what == "photo":
                out = await run(capture.photo, q.get("name", ""), await request.read())
                return web.json_response(out)
            b = await request.json() if request.method == "POST" and request.can_read_body else {}
            if not isinstance(b, dict):
                raise ValueError("expected an object")
            if what == "list":
                out = await run(capture.listing)
            elif what == "note":
                out = await run(capture.note, b.get("text"))
            elif what == "screenshot":
                out = await run(capture.screenshot)
            elif what == "delete":
                out = await run(capture.delete, str(b.get("kind", "")), str(b.get("id", "")))
            elif what == "clip":                                     # put a note on the PC clipboard
                text = str(b.get("text") or "")
                if not text.strip():
                    raise ValueError("nothing to copy")
                await run(clip.write_text, text)
                out = {"ok": True}
            elif what == "folder":
                await run(capture.open_folder)
                out = {"ok": True}
            else:
                return web.json_response({"error": "unknown"}, status=404)
        except json.JSONDecodeError:
            return web.json_response({"error": "expected JSON"}, status=400)
        except (ValueError, TypeError, OSError, RuntimeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:                                       # mss / PIL
            return web.json_response({"error": f"couldn't capture ({type(e).__name__}: {e})"}, status=400)
        return web.json_response(out)

    async def callouts_list(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        return web.json_response(await asyncio.get_running_loop().run_in_executor(None, callouts.listing))

    async def callouts_image(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        got = await asyncio.get_running_loop().run_in_executor(None, callouts.image, request.query.get("id", ""))
        if not got:
            return web.Response(status=404)
        return web.FileResponse(got[0], headers={"Content-Type": got[1], "Cache-Control": "max-age=604800"})

    async def callouts_folder(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        try:
            await asyncio.get_running_loop().run_in_executor(None, callouts.open_folder)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    async def apps_api(request):
        """Apps the profile editor can offer: recently in front, and open now."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        names = list(focus.recent)
        try:
            for w in await WINDOWS.get():
                exe = (w.get("exe") or "").lower()
                if exe and exe not in names:
                    names.append(exe)
        except Exception:
            pass
        return web.json_response({"apps": names[:40], "front": focus.state["exe"]})

    async def tiles_api(request):
        """Numbers for the live tiles on screen: ?want=cpu,gpu,ram,media&ha=light.desk,..."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        asked = request.query.get("want", "").split(",")
        want = [w for w in asked if w in stats.READERS]
        ids = [e.strip() for e in request.query.get("ha", "").split(",") if e.strip()]
        loop = asyncio.get_running_loop()
        out = stats.snapshot(want) if want else {}
        if "obs" in asked:
            out["obs"] = obs.state()
        if "mc" in asked:
            out["mc"] = await loop.run_in_executor(None, mc.state)
        if "study" in asked:
            out["study"] = study.state()
        if "cal" in asked:
            out["cal"] = await loop.run_in_executor(None, ical.events)
        if "git" in asked:
            out["git"] = await loop.run_in_executor(None, dev.git_tile)
        if ids and hostsettings.get("ha_url") and hostsettings.get("ha_token"):
            out["ha"] = await loop.run_in_executor(None, ha.states, ids)
        return web.json_response(out)

    async def tiles_art(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        got = stats.art()
        if not got:
            return web.Response(status=404)
        return web.Response(body=got[0], content_type=got[1], headers={"Cache-Control": "no-cache"})

    async def clips_list(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, clips.listing, request.query.get("fresh") == "1")
        return web.json_response({"sources": [{k: v for k, v in s.items() if v is not None}
                                              for s in await loop.run_in_executor(None, clips.sources)],
                                  "clips": [clips.public(c) for c in items]})

    async def clips_thumb(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        data = await asyncio.get_running_loop().run_in_executor(None, clips.thumb, request.query.get("id", ""))
        if not data:
            return web.Response(status=404)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    async def clips_video(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        c = await asyncio.get_running_loop().run_in_executor(None, clips.get, request.query.get("id", ""))
        if not c or not os.path.isfile(c["path"]):
            return web.Response(status=404)
        return web.FileResponse(c["path"])        # supports seeking (Range requests)

    async def clips_action(request):
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        try:
            body = await request.json()
            fn = {"reveal": clips.reveal, "play": clips.play_on_pc}[body["do"]]
            await asyncio.get_running_loop().run_in_executor(None, fn, str(body["id"]))
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    async def deck_action(request):
        """A deck button was tapped: {profile, index}."""
        if not authorized(request):
            return web.json_response({"error": "bad pin"}, status=403)
        try:
            body = await request.json()
            prof = deck.find(deck.load(), body["profile"])
            button = prof["buttons"][int(body["index"])]
        except (ValueError, KeyError, TypeError, IndexError):
            return web.json_response({"error": "no such button"}, status=400)

        def work():
            switched = []
            deck.run(button, INPUT, lambda name: switched.append(DECK.switch(name)))
            return switched[0] if switched else None
        try:
            data = await asyncio.get_running_loop().run_in_executor(None, work)
        except Exception as e:           # bad key name, app not found...
            print(f"[deck] {button.get('label', '?')}: {e}", flush=True)
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True, "profiles": data})

    async def audio_handler(request):
        """PC sound for the iPad (see pcaudio.py)."""
        if not authorized(request):
            return web.Response(status=403, text="bad pin")
        ws = web.WebSocketResponse(heartbeat=30, compress=False)
        await ws.prepare(request)
        if not pcaudio.AVAILABLE:
            await ws.send_str('{"type":"nosound"}')
            await ws.close()
            return ws
        reader = asyncio.ensure_future(ws.receive())        # notices the iPad closing it
        sender = asyncio.ensure_future(pcaudio.stream(ws))
        try:
            await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (reader, sender):
                t.cancel()
            await ws.close()
        return ws

    async def ws_handler(request):
        if not authorized(request):
            return web.Response(status=403, text="bad pin")
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=16 << 20, compress=False)
        await ws.prepare(request)
        sock = request.transport.get_extra_info("socket") if request.transport else None
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        q = request.query
        try:
            mon_idx = int(q.get("monitor", "-1"))
        except ValueError:                  # "auto": the iPad's own screen
            mon_idx = -1
        sess = Session(mon_idx, q.get("mode", "balanced"))
        fps = max(10, min(120, int(q.get("fps", "60"))))     # 120 for ProMotion iPads
        sess.video_wanted = q.get("video") == "1" and video.AVAILABLE
        sess.max_fps = fps
        tablet = q.get("tablet") == "1"     # drawing tablet: input only, nothing captured
        loop = asyncio.get_running_loop()
        cap_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")  # mss is per-thread
        mon = await loop.run_in_executor(cap_pool, sess.monitor)
        winpin.target["mon"] = dict(mon)
        STATUS["streams"] += 1
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
                    if t in ("move", "pen", "scroll"):        # finger, Pencil and two-finger moves for [speed]
                        state["moves"] = state.get("moves", 0) + 1
                    if t == "move":
                        INPUT.move(*to_screen(m))
                    elif t == "down":
                        INPUT.move(*to_screen(m))
                        INPUT.button(int(m.get("b", 0)), True)
                    elif t == "up":
                        if "x" in m:
                            INPUT.move(*to_screen(m))
                        INPUT.button(int(m.get("b", 0)), False)
                    elif t == "pen":
                        # Apple Pencil with pressure and tilt; a mouse if Windows can't take pens.
                        x, y = to_screen(m)
                        st = m.get("s")
                        if not PEN.send(st, x, y, float(m.get("p", 0.5)),
                                        float(m.get("tx", 0)), float(m.get("ty", 0)),
                                        barrel=bool(m.get("b")), eraser=bool(m.get("e"))):
                            if st in ("down", "move", "hover", "up"):
                                INPUT.move(x, y)
                            if st in ("down", "up"):
                                INPUT.button(0, st == "down")
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
                    elif t == "record":         # record what the iPad shows to recordings/
                        await ws.send_str(json.dumps(await loop.run_in_executor(None, record, bool(m.get("on")))))
                    elif t == "combo":          # "ctrl+shift+m", as on deck buttons (gestures, pen tools)
                        mods, vk = deck.parse_keys(str(m.get("k", ""))[:60])
                        INPUT.combo(vk, mods)
                    elif t == "mode":
                        sess.set_profile(str(m.get("mode", "balanced")))
                        state["force"] = True
                    elif t == "ping":          # the iPad measures the round trip
                        await ws.send_str(json.dumps({"type": "pong", "c": m.get("c", 0)}))
                        continue
                    elif t == "refresh":
                        state["force"] = True
                    elif t == "vkey":
                        # The iPad's video decoder restarted: send its setup and a key frame.
                        sess.video_key = True
                        state["vcfg"] = None
                        state["force"] = True
                    elif t == "clip":
                        # Something was copied on the iPad: put it on the PC clipboard.
                        await loop.run_in_executor(None, set_pc_clip, m)
                    elif t == "display":
                        # Extend: the iPad is its own screen. Mirror: it shows a copy of
                        # the laptop screen. The stream follows the change by itself.
                        mode = {"extend": "/extend", "clone": "/clone"}.get(m.get("mode"))
                        if mode and IS_WINDOWS:
                            print(f"[screen] switching Windows to {'mirror' if mode == '/clone' else 'extend'} mode")
                            import subprocess
                            subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                             "System32", "DisplaySwitch.exe"), mode])
                    elif t in ("windows", "winmove", "pin"):
                        # The iPad's list of open apps: move one here, or keep an app here.
                        def apps(m=m, mon=dict(sess.mon)):
                            if t == "winmove":
                                winpin.move_here(int(m["hwnd"]), mon)
                            elif t == "pin":
                                winpin.set_pin(str(m["exe"]), bool(m.get("on")))
                                if m.get("on"):
                                    for w in winpin.list_windows():
                                        if w["exe"].lower() == str(m["exe"]).lower() and not w["here"]:
                                            winpin.move_here(w["hwnd"], mon)
                            if t != "windows":
                                time.sleep(0.3)          # let Windows finish moving it
                            return {"type": "windows", "list": winpin.list_windows(),
                                    "pins": winpin.load_pins(), "ok": IS_WINDOWS}
                        await ws.send_str(json.dumps(await loop.run_in_executor(None, apps)))
                    elif t == "screen":
                        # Turn the iPad's screen portrait, or change its resolution or text size.
                        res = await loop.run_in_executor(None, screen_change, dict(sess.mon), m)
                        await ws.send_str(json.dumps({"type": "screen", **res}))
                    wake.set()
                except Exception as e:  # never let a bad event kill the session
                    print("input error:", e)

        async def tablet_writer():
            """Drawing-tablet mode: the iPad is a pad for one screen. Nothing is
            captured or encoded; the link is just kept alive."""
            await ws.send_str(json.dumps({"type": "info", "width": sess.mon["width"], "height": sess.mon["height"],
                                          "monitor": sess.mon, "tablet": True, "build": page_build()}))
            while not ws.closed:
                await asyncio.sleep(1.5)
                if not ws.closed:
                    await ws.send_str('{"type":"ping"}')

        async def writer():
            if tablet:
                return await tablet_writer()
            interval = 1.0 / fps
            sess.wait = interval
            last_cursor = None
            last_dims = None
            next_report = time.perf_counter() + REPORT_SECS
            focus_seen = -1
            while not ws.closed:
                await can_send.wait()
                start = time.perf_counter()
                if focus.state["version"] != focus_seen:      # app-aware buttons
                    focus_seen = focus.state["version"]
                    try:
                        profs = deck.load()["profiles"]
                        fp = focus.match(profs) if hostsettings.get("auto_profiles") else None
                        await ws.send_str(json.dumps({"type": "focus", "profile": fp,
                                                      "front": focus.state["exe"]}))
                    except Exception:
                        pass
                if start >= next_report:       # live speed readout in the PC window
                    st, n = sess.stats, max(1, sess.stats["frames"])
                    secs = start - next_report + REPORT_SECS
                    acks = max(1, state["acks"])
                    sp = {"screen": st.get('new', 0) / secs, "updates": st['frames'] / secs,
                          "video": st.get('vid', 0) / secs,
                          "cap": st['cap'] * 1000 / max(1, st.get('caps', 0)), "backend": sess.backend,
                          "work": (st.get('work', 0) - st.get('idle', 0)) * 1000 / max(1, st.get('steps', 0)),
                          "enc": st['enc'] * 1000 / n, "kb": st['bytes'] / n / 1024,
                          "mbit": st['bytes'] * 8 / 1e6 / secs,
                          "rtt": state['rtt_sum'] * 1000 / acks, "q": st.get('q', '-'),
                          "moves": state.get('moves', 0) / secs,
                          "area": 100 * st.get('area', 0.0) / max(1, st.get('areas', 0)),
                          "encoder": sess.video.name if sess.video is not None else None,
                          "encoders": video.open_count() if video.AVAILABLE else 0}
                    print(f"[speed] {sp['screen']:4.0f} screen fps | "
                          f"{sp['updates']:4.0f} updates/s ({sp['video']:.0f} video) | "
                          f"capture {sp['cap']:4.1f} ms ({sp['backend']}) | "
                          f"work {sp['work']:4.1f} ms | "
                          f"encode {sp['enc']:4.1f} ms | "
                          f"{sp['kb']:5.0f} KB/update | "
                          f"{sp['mbit']:5.1f} Mbit/s | "
                          f"round trip {sp['rtt']:4.0f} ms | "
                          f"quality {sp['q']} | "
                          f"touch moves {sp['moves']:3.0f}/s | "
                          f"changed {sp['area']:4.1f}% of the screen", flush=True)
                    # Something small that redraws all the time (an animation, a video, a
                    # spinner) keeps the laptop capturing even when nobody touches the iPad.
                    # Say once where it is, so it can be found and closed if it's not wanted.
                    if st.get("small", 0) / secs > 20 and sp["area"] < 5 and sp["moves"] < 1:
                        state["busy"] = state.get("busy", 0) + 1
                        if state["busy"] == 3 and st.get("small_box"):
                            l, t, r, b = st["small_box"]
                            print(f"[speed] Note: something small on the iPad's screen keeps changing about "
                                  f"{st['small'] / secs:.0f} times a second, around x {l}-{r}, y {t}-{b} of the "
                                  f"iPad's {sess.dims[0]}x{sess.dims[1]} screen (an animation, video or spinner). "
                                  "It's shown as it should be; it just keeps the laptop a little busy.", flush=True)
                    else:
                        state["busy"] = 0
                    # The same numbers for the iPad's status corner.
                    await ws.send_str(json.dumps({"type": "speed", **sp}))
                    state["moves"] = 0
                    sess.stats = {"frames": 0, "cap": 0.0, "enc": 0.0, "bytes": 0}
                    state["rtt_sum"], state["acks"] = 0.0, 0
                    next_report = start + REPORT_SECS
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
                if sess.video is not None and sess.video.config and sess.video.config != state.get("vcfg"):
                    state["vcfg"] = sess.video.config
                    codec, desc = sess.video.config
                    await ws.send_str(json.dumps({"type": "vconfig", "codec": codec,
                                                  "desc": base64.b64encode(desc).decode()}))
                if dims != last_dims:
                    last_dims = dims
                    await ws.send_str(json.dumps({"type": "info", "width": dims[0], "height": dims[1],
                                                  "monitor": sess.mon, "build": page_build()}))
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

        # Shared clipboard: anything copied on the PC goes to the iPad.
        clip_seen = {"seq": clip.sequence()}

        def record(on):
            import recorder
            if on and sess.recorder is None:
                try:
                    if sess.last is not None:                   # the size frames really come in
                        h, w = sess.last[0].shape[:2]
                    else:
                        w, h = sess.mon["width"], sess.mon["height"]
                    sess.recorder = recorder.Recorder(w, h)
                    print(f"[record] recording to {os.path.basename(sess.recorder.path)} ({sess.recorder.name})", flush=True)
                except Exception as e:
                    return {"type": "record", "on": False, "error": str(e)}
                return {"type": "record", "on": True}
            if not on and sess.recorder is not None:
                rec, sess.recorder = sess.recorder, None
                path, secs = rec.stop()
                if path:
                    print(f"[record] saved {os.path.basename(path)} ({secs:.0f} s)", flush=True)
                return {"type": "record", "on": False, "file": os.path.basename(path) if path else None,
                        "secs": round(secs), "error": rec.error}
            return {"type": "record", "on": sess.recorder is not None}

        def set_pc_clip(m):
            try:
                if isinstance(m.get("text"), str):
                    clip.write_text(m["text"])
                elif isinstance(m.get("png"), str):
                    clip.write_png(base64.b64decode(m["png"]))
                else:
                    return
                clip_seen["seq"] = clip.sequence()     # don't send it straight back
                print("[clipboard] iPad -> PC")
            except Exception as e:
                print("clipboard error:", e)

        async def clip_watch():
            while not ws.closed and clip.available():
                await asyncio.sleep(0.5)
                seq = clip.sequence()
                if seq == clip_seen["seq"]:
                    continue
                clip_seen["seq"] = seq
                try:
                    got = await loop.run_in_executor(None, clip.read)
                except Exception:
                    continue
                if got is None or ws.closed:
                    continue
                kind, data = got
                msg = {"type": "clip", "text": data} if kind == "text" else \
                      {"type": "clip", "png": base64.b64encode(data).decode()}
                await ws.send_str(json.dumps(msg))
                print("[clipboard] PC -> iPad")

        rtask = asyncio.create_task(reader())
        wtask = asyncio.create_task(writer())
        ctask = asyncio.create_task(clip_watch())
        try:
            await asyncio.wait({rtask, wtask}, return_when=asyncio.FIRST_COMPLETED)
            for t in (rtask, wtask):
                if t.done() and t.exception():
                    print("session error:", t.exception())
        finally:
            for t in (rtask, wtask, ctask):
                t.cancel()
            if winpin.target["mon"] == sess.mon:
                winpin.target["mon"] = None
            await loop.run_in_executor(cap_pool, sess.close)
            cap_pool.shutdown(wait=False)
            STATUS["streams"] -= 1
            print(f"[-] iPad disconnected ({request.remote})")
        return ws

    app = web.Application(client_max_size=64 << 20)      # photos and PDFs from the iPad

    async def tag_build(request, response):
        if request.path.startswith("/api/"):
            response.headers["X-Page-Build"] = page_build()
    app.on_response_prepare.append(tag_build)
    app.router.add_get("/", index)
    app.router.add_get("/api/monitors", monitors)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/audio", audio_handler)
    app.router.add_get("/api/profiles", profiles_get)
    app.router.add_put("/api/profiles", profiles_put)
    app.router.add_post("/api/profile", profile_switch)
    app.router.add_post("/api/action", deck_action)
    app.router.add_get("/api/settings", settings_get)
    app.router.add_put("/api/settings", settings_put)
    for m in ("GET", "POST", "PUT"):
        app.router.add_route(m, "/api/dev/{what}", dev_api)
    for m in ("GET", "POST"):
        app.router.add_route(m, "/api/school/{what}", school_api)
        app.router.add_route(m, "/api/capture/{what}", capture_api)
    app.router.add_get("/api/callouts", callouts_list)
    app.router.add_get("/api/callouts/image", callouts_image)
    app.router.add_post("/api/callouts/folder", callouts_folder)
    app.router.add_get("/api/obs", obs_api)
    app.router.add_post("/api/obs", obs_api)
    app.router.add_get("/api/ha", ha_api)
    app.router.add_post("/api/ha", ha_api)
    app.router.add_get("/api/apps", apps_api)
    app.router.add_get("/api/tiles", tiles_api)
    app.router.add_get("/api/tiles/art", tiles_art)
    app.router.add_get("/api/clips", clips_list)
    app.router.add_get("/api/clips/thumb", clips_thumb)
    app.router.add_get("/api/clips/video", clips_video)
    app.router.add_post("/api/clips/action", clips_action)
    app.router.add_static("/", WEB_DIR, show_index=False)

    async def remember_loop(app):
        app["loop"] = asyncio.get_running_loop()
    app.on_startup.append(remember_loop)
    return app


def _serve_both(app, port, tls_ctx):
    """Like web.run_app, but listening on the plain port (the USB-C cable and
    older setups) and on port + 1 with TLS (encrypted Wi-Fi)."""
    from aiohttp.web_runner import GracefulExit

    async def serve():
        runner = web.AppRunner(app, handle_signals=True)
        await runner.setup()
        try:
            await web.TCPSite(runner, "0.0.0.0", port).start()
            await web.TCPSite(runner, "0.0.0.0", port + 1, ssl_context=tls_ctx).start()
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()
    try:
        asyncio.run(serve())
    except (GracefulExit, KeyboardInterrupt):
        pass


def _quit_from_tray(app):
    """Stop the host as Ctrl+C would (so the iPad screen is switched off too)."""
    from aiohttp.web_runner import GracefulExit

    def stop():
        raise GracefulExit()
    loop = app.get("loop")
    if loop is not None:
        loop.call_soon_threadsafe(stop)
    else:
        os._exit(0)


def _log_to_file(path):
    """Background mode has no window: send all output to a log file (kept small)."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 5_000_000:
            os.replace(path, path + ".old")
    except OSError:
        pass
    f = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stderr = f
    print(f"\n--- started {time.strftime('%Y-%m-%d %H:%M:%S')} ---")


def main():
    ap = argparse.ArgumentParser(description="Stream a Windows monitor to an iPad")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--new-pin", action="store_true", help="generate a new pairing PIN")
    ap.add_argument("--background", action="store_true",
                    help="run without a window (auto-start): log to host.log, and turn the "
                         "iPad's virtual screen on only while an iPad is plugged in")
    ap.add_argument("--no-tray", action="store_true", help="don't show the icon next to the clock")
    ap.add_argument("--keep-screen", action="store_true",
                    help="don't turn the iPad's virtual screen on/off automatically")
    args = ap.parse_args()
    # The picture's thread waits for Python's lock whenever another thread
    # (an answer to the iPad's deck or tiles) holds it; hand it over every
    # 1 ms instead of 5 so those waits stay short. Costs nothing when idle.
    sys.setswitchinterval(0.001)
    if video.AVAILABLE:
        threading.Thread(target=video.warm, daemon=True, name="video-warm").start()

    if args.background:
        _log_to_file(os.path.join(HERE, "host.log"))
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
    tls_ctx, tls_info = tlsutil.context()
    if tls_ctx is not None:
        TLS["port"] = args.port + 1
        print(f"\n Encrypted Wi-Fi (recommended):  https://{(sorted(addrs) or ['<this-PC-IP>'])[0]}:{TLS['port']}")
        print(f"   Pairing code: {tls_info}   (the iPad app asks you to check it the first time)")
    else:
        print(f"\n (Encrypted Wi-Fi is off: {tls_info}.)")
    on_plug = None
    if IS_WINDOWS and not args.keep_screen:
        import vdd
        DECK.allowed = True
        ready = DECK.screen_ctl
    if IS_WINDOWS and not args.keep_screen and args.background:
        if ready:
            print("\n The iPad's virtual screen turns on while an iPad is plugged in over USB-C.")
        else:
            print("\n Tip: run display-setup.bat once, so the iPad's virtual screen turns on")
            print("      when the iPad is plugged in and off when it's unplugged.")

        def on_plug(plugged):
            # Switching takes a few seconds; do it off the event loop.
            def work():
                if not DECK.screen_ctl:
                    return
                if plugged and not DECK.active().get("screen", True):
                    print("[screen] iPad plugged in; this profile doesn't use the screen.", flush=True)
                elif plugged:
                    print("[screen] iPad plugged in: turning the virtual screen on.", flush=True)
                    vdd.turn_on(lambda: len(list_monitors()))
                else:
                    print("[screen] iPad unplugged: turning the virtual screen off.", flush=True)
                    vdd.turn_off()
            threading.Thread(target=work, daemon=True).start()
    elif IS_WINDOWS and not args.keep_screen:
        if ready:
            res = None
            if DECK.active().get("screen", True):
                print("\n Turning on the iPad's virtual screen (it turns off when you close this window)...")
                res = vdd.turn_on(lambda: len(list_monitors()))
            if res == "failed":
                print("   It didn't appear. Check vdd.log in this folder, or turn it on in Device Manager.")
        else:
            print("\n Tip: run display-setup.bat once, so the iPad's virtual screen turns on")
            print("      with this window and off when you close it.")
    print("\n Screens found:")
    found = list_monitors()
    for m in found:
        hz = f" {m['hz']:.0f} Hz" if m.get("hz") else ""
        print(f"   #{m['index']}: {m['width']}x{m['height']}{hz} at ({m['left']},{m['top']})  "
              f"{m['kind']}{' ' + m['name'] if m.get('name') else ''}{'  (main)' if m.get('primary') else ''}")
    pick = screens.pick_ipad(found)
    if pick:
        print(f"   The iPad shows #{pick['index']} unless you pick another screen on it.")
    if not IS_WINDOWS:
        print("\n (Not on Windows: streaming works, but touch input is disabled.)")
    if simplejpeg is None:
        print("\n Tip: 'pip install simplejpeg' gives a much faster stream.")
    print("\n Keep this window open. Press Ctrl+C to stop.")
    print("=" * 62)
    print(" USB-C: plug in the iPad and open the iPad Display app (see USB-C.md).")
    print("=" * 62)
    app = make_app(cfg)
    winpin.start()
    focus.start()
    if IS_WINDOWS and not args.no_tray:
        import tray
        tray.Tray(status_text, DECK, os.path.join(HERE, "host.log"), HERE,
                  lambda: _quit_from_tray(app)).start()
    import usb_bridge
    usb_bridge.start(app, args.port, on_plug)
    try:
        if tls_ctx is None:
            web.run_app(app, host="0.0.0.0", port=args.port, print=None)
        else:
            _serve_both(app, args.port, tls_ctx)
    except OSError as e:
        if getattr(e, "errno", None) in (10048, 98, 48):
            sys.exit("\n The host is already running, either in another window or in the "
                     "background (auto-start).\n If you set up auto-start, you don't need "
                     "start.bat: just plug in the iPad and open the app.\n Otherwise close the "
                     "other window first (or end 'Python' in Task Manager), then run start.bat again.")
        raise


if __name__ == "__main__":
    main()
