"""Change the iPad's screen in Windows from the iPad: turn it portrait when
the iPad is held upright, and switch between resolution and text-size
presets. Works on the screen being streamed, found by its position.

Only changes a screen that isn't the main one (the laptop's own screen is
never rotated or resized). Windows-only; elsewhere every call says no.
"""
import ctypes
import sys
from ctypes import wintypes

# Fixed sizes as on Windows (DWORD is 8 bytes on other systems).
DWORD, WORD, LONG, WCHAR = ctypes.c_uint32, ctypes.c_uint16, ctypes.c_int32, wintypes.WCHAR

IS_WINDOWS = sys.platform == "win32"

ENUM_CURRENT_SETTINGS = -1
DM_DISPLAYORIENTATION = 0x00000080
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
CDS_UPDATEREGISTRY = 0x00000001
DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004
RESULTS = {0: "ok", 1: "Windows needs a restart for that", -1: "the display driver refused",
           -2: "that resolution isn't one of the virtual screen's modes",
           -3: "Windows couldn't save it", -4: "bad flags", -5: "bad parameter",
           -6: "bad display", -8: "bad mode"}


class DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", WCHAR * 32), ("dmSpecVersion", WORD),
        ("dmDriverVersion", WORD), ("dmSize", WORD),
        ("dmDriverExtra", WORD), ("dmFields", DWORD),
        ("dmPositionX", LONG), ("dmPositionY", LONG),
        ("dmDisplayOrientation", DWORD), ("dmDisplayFixedOutput", DWORD),
        ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short), ("dmFormName", WCHAR * 32),
        ("dmLogPixels", WORD), ("dmBitsPerPel", DWORD),
        ("dmPelsWidth", DWORD), ("dmPelsHeight", DWORD),
        ("dmDisplayFlags", DWORD), ("dmDisplayFrequency", DWORD),
        ("dmICMMethod", DWORD), ("dmICMIntent", DWORD),
        ("dmMediaType", DWORD), ("dmDitherType", DWORD),
        ("dmReserved1", DWORD), ("dmReserved2", DWORD),
        ("dmPanningWidth", DWORD), ("dmPanningHeight", DWORD),
    ]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", DWORD), ("DeviceName", WCHAR * 32),
                ("DeviceString", WCHAR * 128), ("StateFlags", DWORD),
                ("DeviceID", WCHAR * 128), ("DeviceKey", WCHAR * 128)]


def _screens():
    """[(device name, DEVMODEW, is_primary)] for every screen on the desktop."""
    user32 = ctypes.windll.user32
    out, i = [], 0
    while True:
        dd = DISPLAY_DEVICEW(cb=ctypes.sizeof(DISPLAY_DEVICEW))
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            continue
        dm = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
        if user32.EnumDisplaySettingsW(dd.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
            out.append((dd.DeviceName, dm, bool(dd.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE)))
    return out


def _find(mon):
    if not IS_WINDOWS:
        raise RuntimeError("only on Windows")
    for name, dm, primary in _screens():
        if dm.dmPositionX == mon["left"] and dm.dmPositionY == mon["top"]:
            if primary:
                raise RuntimeError("that's the laptop's main screen, so it's left alone")
            return name, dm
    raise RuntimeError("couldn't find that screen in Windows")


def info(mon):
    """Current mode of the streamed screen: {width, height, portrait, modes}.
    modes lists the landscape resolutions the screen offers (biggest first)."""
    name, dm = _find(mon)
    user32 = ctypes.windll.user32
    modes, i = set(), 0
    while True:
        m = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW))
        if not user32.EnumDisplaySettingsW(name, i, ctypes.byref(m)):
            break
        i += 1
        w, h = max(m.dmPelsWidth, m.dmPelsHeight), min(m.dmPelsWidth, m.dmPelsHeight)
        modes.add((w, h))
    try:
        pct = _scale_now(name)
    except Exception:
        pct = None
    return {"width": dm.dmPelsWidth, "height": dm.dmPelsHeight,
            "portrait": dm.dmDisplayOrientation in (1, 3), "scale": pct,
            "modes": sorted(modes, reverse=True)[:40]}


def _apply(name, dm):
    r = ctypes.windll.user32.ChangeDisplaySettingsExW(name, ctypes.byref(dm), None,
                                                       CDS_UPDATEREGISTRY, None)
    if r != 0:
        raise RuntimeError(RESULTS.get(r, f"Windows said {r}"))


def rotate(mon, portrait):
    """Portrait (iPad upright) or landscape. Keeps the resolution, swapped."""
    name, dm = _find(mon)
    now_portrait = dm.dmDisplayOrientation in (1, 3)
    if now_portrait == portrait:
        return "already"
    w, h = dm.dmPelsWidth, dm.dmPelsHeight
    dm.dmPelsWidth, dm.dmPelsHeight = h, w
    dm.dmDisplayOrientation = 1 if portrait else 0      # 90 degrees, or normal
    dm.dmFields = DM_DISPLAYORIENTATION | DM_PELSWIDTH | DM_PELSHEIGHT
    _apply(name, dm)
    return "ok"


def resize(mon, width, height):
    """Landscape width x height (swapped automatically while portrait)."""
    name, dm = _find(mon)
    w, h = max(width, height), min(width, height)
    if dm.dmDisplayOrientation in (1, 3):
        w, h = h, w
    if (dm.dmPelsWidth, dm.dmPelsHeight) == (w, h):
        return "already"
    dm.dmPelsWidth, dm.dmPelsHeight = w, h
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    _apply(name, dm)
    return "ok"


# ---------------------------------------------------------------- text size
# Windows has no public call for a screen's scale (100%, 150%, 200%...).
# This uses the same DisplayConfig request the Settings app uses, which
# counts in steps from the screen's recommended scale.
SCALES = [100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500]
QDC_ONLY_ACTIVE_PATHS = 0x2
GET_SOURCE_NAME = 1
GET_DPI, SET_DPI = -3, -4


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", DWORD), ("HighPart", LONG)]


class _Header(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("size", ctypes.c_uint32),
                ("adapterId", LUID), ("id", ctypes.c_uint32)]


class _SourceName(ctypes.Structure):
    _fields_ = [("header", _Header), ("viewGdiDeviceName", WCHAR * 32)]


class _GetDpi(ctypes.Structure):
    _fields_ = [("header", _Header), ("minScaleRel", ctypes.c_int32),
                ("curScaleRel", ctypes.c_int32), ("maxScaleRel", ctypes.c_int32)]


class _SetDpi(ctypes.Structure):
    _fields_ = [("header", _Header), ("scaleRel", ctypes.c_int32)]


class _PathSource(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32),
                ("modeInfoIdx", ctypes.c_uint32), ("statusFlags", ctypes.c_uint32)]


class _PathTarget(ctypes.Structure):
    _fields_ = [("adapterId", LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32),
                ("scaling", ctypes.c_uint32), ("refreshNum", ctypes.c_uint32),
                ("refreshDen", ctypes.c_uint32), ("scanLineOrdering", ctypes.c_uint32),
                ("targetAvailable", ctypes.c_int32), ("statusFlags", ctypes.c_uint32)]


class _Path(ctypes.Structure):
    _fields_ = [("sourceInfo", _PathSource), ("targetInfo", _PathTarget), ("flags", ctypes.c_uint32)]


class _Mode(ctypes.Structure):          # only its size matters here
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32),
                ("adapterId", LUID), ("data", ctypes.c_byte * 48)]


def _source_for(name):
    user32 = ctypes.windll.user32
    n_paths, n_modes = ctypes.c_uint32(), ctypes.c_uint32()
    if user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)):
        raise RuntimeError("couldn't read the display setup")
    paths, modes = (_Path * n_paths.value)(), (_Mode * n_modes.value)()
    if user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), paths,
                                 ctypes.byref(n_modes), modes, None):
        raise RuntimeError("couldn't read the display setup")
    for p in paths[:n_paths.value]:
        req = _SourceName()
        req.header = _Header(GET_SOURCE_NAME, ctypes.sizeof(_SourceName), p.sourceInfo.adapterId, p.sourceInfo.id)
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(req)) == 0 and req.viewGdiDeviceName == name:
            return p.sourceInfo.adapterId, p.sourceInfo.id
    raise RuntimeError("couldn't find that screen's scale setting")


def _get_dpi(name):
    adapter, sid = _source_for(name)
    get = _GetDpi()
    get.header = _Header(GET_DPI, ctypes.sizeof(_GetDpi), adapter, sid)
    if ctypes.windll.user32.DisplayConfigGetDeviceInfo(ctypes.byref(get)):
        raise RuntimeError("Windows didn't say the current scale")
    return adapter, sid, get


def _scale_now(name):
    _, _, get = _get_dpi(name)
    i = abs(get.minScaleRel) + get.curScaleRel
    return SCALES[i] if 0 <= i < len(SCALES) else None


def scale(mon, percent):
    """Set the screen's scale (text size) to `percent`, e.g. 100 or 200."""
    name, _ = _find(mon)
    adapter, sid, get = _get_dpi(name)
    user32 = ctypes.windll.user32
    recommended = abs(get.minScaleRel)                 # steps from 100% to the recommended scale
    if percent not in SCALES:
        raise RuntimeError(f"{percent}% isn't a Windows scale")
    rel = SCALES.index(percent) - recommended
    if not get.minScaleRel <= rel <= get.maxScaleRel:
        raise RuntimeError(f"{percent}% isn't offered for this resolution")
    if rel == get.curScaleRel:
        return "already"
    req = _SetDpi()
    req.header = _Header(SET_DPI, ctypes.sizeof(_SetDpi), adapter, sid)
    req.scaleRel = rel
    if user32.DisplayConfigSetDeviceInfo(ctypes.byref(req)):
        raise RuntimeError("Windows refused the new scale")
    return "ok"
