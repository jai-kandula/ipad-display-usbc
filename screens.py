"""Which screens the PC has and what each one is: the iPad's virtual screen,
the laptop's own panel, or an external monitor, with its refresh rate and
name. Used to stream the iPad's screen by default (never a gaming monitor by
accident, whatever order Windows lists the screens in) and to describe the
setup in the log and the self-test.

Windows only (QueryDisplayConfig). Elsewhere every screen is "external"."""
import ctypes
import re
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"
UINT32, INT32 = ctypes.c_uint32, ctypes.c_int32
VIRTUAL_NAMES = re.compile(r"virtual|idd|parsec|mtt|spacedesk|duet|splashtop|sunshine", re.I)

# DISPLAYCONFIG_VIDEO_OUTPUT_TECHNOLOGY
TECH = {0xFFFFFFFF: "other", 0: "VGA", 4: "DVI", 5: "HDMI", 10: "DisplayPort", 11: "embedded DisplayPort",
        13: "embedded UDI", 16: "indirect (wired)", 17: "indirect (virtual)", 0x80000000: "internal"}
INTERNAL = {11, 13, 0x80000000}
VIRTUAL_TECH = {17, 0xFFFFFFFF}


class _LUID(ctypes.Structure):
    _fields_ = [("lo", UINT32), ("hi", INT32)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("num", UINT32), ("den", UINT32)]


class _SOURCE(ctypes.Structure):
    _fields_ = [("adapter", _LUID), ("id", UINT32), ("mode", UINT32), ("flags", UINT32)]


class _TARGET(ctypes.Structure):
    _fields_ = [("adapter", _LUID), ("id", UINT32), ("mode", UINT32), ("tech", UINT32),
                ("rotation", UINT32), ("scaling", UINT32), ("refresh", _RATIONAL),
                ("scanline", UINT32), ("available", INT32), ("flags", UINT32)]


class _PATH(ctypes.Structure):
    _fields_ = [("src", _SOURCE), ("tgt", _TARGET), ("flags", UINT32)]


class _MODE(ctypes.Structure):
    _fields_ = [("type", UINT32), ("id", UINT32), ("adapter", _LUID), ("u", ctypes.c_byte * 48)]


class _HEADER(ctypes.Structure):
    _fields_ = [("type", UINT32), ("size", UINT32), ("adapter", _LUID), ("id", UINT32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("h", _HEADER), ("gdi", wintypes.WCHAR * 32)]


class _TARGET_NAME(ctypes.Structure):
    _fields_ = [("h", _HEADER), ("flags", UINT32), ("tech", UINT32), ("mfg", ctypes.c_uint16),
                ("product", ctypes.c_uint16), ("connector", UINT32),
                ("name", wintypes.WCHAR * 64), ("path", wintypes.WCHAR * 128)]


class _DEVMODE(ctypes.Structure):
    _fields_ = [("name", wintypes.WCHAR * 32), ("spec", ctypes.c_uint16), ("drv", ctypes.c_uint16),
                ("size", ctypes.c_uint16), ("extra", ctypes.c_uint16), ("fields", UINT32),
                ("x", INT32), ("y", INT32), ("orient", UINT32), ("fixed", UINT32),
                ("_c", ctypes.c_short * 4), ("form", wintypes.WCHAR * 32), ("logpix", ctypes.c_uint16),
                ("bpp", UINT32), ("w", UINT32), ("h", UINT32), ("dflags", UINT32), ("hz", UINT32),
                ("_r", UINT32 * 8)]


class _DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [("cb", UINT32), ("name", wintypes.WCHAR * 32), ("string", wintypes.WCHAR * 128),
                ("flags", UINT32), ("id", wintypes.WCHAR * 128), ("key", wintypes.WCHAR * 128)]


def _adapters():
    """{gdi name: adapter description} from EnumDisplayDevices."""
    out, i = {}, 0
    user32 = ctypes.windll.user32
    while True:
        dd = _DISPLAY_DEVICE(cb=ctypes.sizeof(_DISPLAY_DEVICE))
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            return out
        i += 1
        out[dd.name] = (dd.string, bool(dd.flags & 4))       # PRIMARY_DEVICE


def _paths():
    """[{gdi, name, tech, hz}] for every active screen, from QueryDisplayConfig."""
    user32 = ctypes.windll.user32
    np_, nm = UINT32(), UINT32()
    if user32.GetDisplayConfigBufferSizes(2, ctypes.byref(np_), ctypes.byref(nm)):   # QDC_ONLY_ACTIVE_PATHS
        return []
    paths, modes = (_PATH * np_.value)(), (_MODE * nm.value)()
    if user32.QueryDisplayConfig(2, ctypes.byref(np_), paths, ctypes.byref(nm), modes, None):
        return []
    out = []
    for p in paths[:np_.value]:
        sn = _SOURCE_NAME()
        sn.h.type, sn.h.size, sn.h.adapter, sn.h.id = 1, ctypes.sizeof(sn), p.src.adapter, p.src.id
        tn = _TARGET_NAME()
        tn.h.type, tn.h.size, tn.h.adapter, tn.h.id = 2, ctypes.sizeof(tn), p.tgt.adapter, p.tgt.id
        user32.DisplayConfigGetDeviceInfo(ctypes.byref(sn))
        user32.DisplayConfigGetDeviceInfo(ctypes.byref(tn))
        r = p.tgt.refresh
        out.append({"gdi": sn.gdi, "name": tn.name, "tech": p.tgt.tech,
                    "hz": round(r.num / r.den, 2) if r.den else None})
    return out


def describe(mss_monitors):
    """mss_monitors: mss's list (index 0 is all screens together). Returns one
    dict per screen: index (mss), left, top, width, height, kind ("ipad",
    "laptop", "external"), name, hz, primary, gdi, connection."""
    out = []
    for i, m in enumerate(mss_monitors):
        if i == 0:
            continue
        out.append({"index": i, "left": m["left"], "top": m["top"], "width": m["width"],
                    "height": m["height"], "kind": "external", "name": "", "hz": None,
                    "primary": m["left"] == 0 and m["top"] == 0, "gdi": "", "connection": ""})
    if not IS_WINDOWS:
        return out
    try:
        adapters = _adapters()
        info = {p["gdi"]: p for p in _paths()}
        user32 = ctypes.windll.user32
        for gdi, (adapter, primary) in adapters.items():
            dm = _DEVMODE(size=ctypes.sizeof(_DEVMODE))
            if not user32.EnumDisplaySettingsW(gdi, -1, ctypes.byref(dm)):
                continue
            s = next((s for s in out if (s["left"], s["top"]) == (dm.x, dm.y)), None)
            if s is None:
                continue
            p = info.get(gdi, {})
            tech = p.get("tech")
            s.update(gdi=gdi, primary=primary, hz=p.get("hz") or dm.hz or None,
                     name=p.get("name") or adapter, connection=TECH.get(tech, "") if tech is not None else "")
            if VIRTUAL_NAMES.search(adapter) or VIRTUAL_NAMES.search(p.get("name", "")):
                s["kind"] = "ipad"
            elif tech in INTERNAL:
                s["kind"] = "laptop"
            elif tech in VIRTUAL_TECH and not p.get("name"):
                s["kind"] = "ipad"
    except Exception as e:                     # never let this stop the stream
        print("[screens] couldn't read the screen details:", e, flush=True)
    return out


def pick_ipad(screens):
    """The screen to stream when the iPad didn't pick one: its virtual screen,
    else the laptop's own panel, else the main screen. Never a fast gaming
    monitor unless it's the only screen."""
    for test in (lambda s: s["kind"] == "ipad", lambda s: s["kind"] == "laptop",
                 lambda s: s["primary"] and (s["hz"] or 60) <= 75, lambda s: (s["hz"] or 60) <= 75):
        hit = [s for s in screens if test(s)]
        if hit:
            return hit[-1]
    return screens[-1] if screens else None


def summary(screens):
    """'laptop 1920x1080 165 Hz (main) + external LG 2560x1440 144 Hz + iPad 2360x1640 60 Hz'"""
    def one(s):
        hz = f" {s['hz']:.0f} Hz" if s["hz"] else ""
        name = f" {s['name']}" if s["name"] and s["kind"] == "external" else ""
        return f"{'iPad' if s['kind'] == 'ipad' else s['kind']}{name} {s['width']}x{s['height']}{hz}" + \
            (" (main)" if s["primary"] else "")
    return " + ".join(one(s) for s in screens) or "no screens"
