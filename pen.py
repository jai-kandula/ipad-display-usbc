"""Apple Pencil as a real Windows pen: pressure and tilt reach drawing and
note apps (OneNote, Krita, Photoshop, Whiteboard...) through Windows Ink.

Uses a synthetic pen device (Windows 10 1809 or newer). If that isn't
available, Pen.ok is False and the Pencil keeps working as a mouse.
"""
import ctypes
import sys

PT_PEN = 3
POINTER_FEEDBACK_DEFAULT = 1
F_NEW, F_INRANGE, F_INCONTACT, F_FIRSTBUTTON = 0x1, 0x2, 0x4, 0x10
F_PRIMARY, F_CONFIDENCE = 0x2000, 0x4000
F_DOWN, F_UPDATE, F_UP = 0x10000, 0x20000, 0x40000
PEN_MASK_PRESSURE, PEN_MASK_TILT_X, PEN_MASK_TILT_Y = 0x1, 0x4, 0x8


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class POINTER_INFO(ctypes.Structure):
    _fields_ = [("pointerType", ctypes.c_uint32), ("pointerId", ctypes.c_uint32),
                ("frameId", ctypes.c_uint32), ("pointerFlags", ctypes.c_uint32),
                ("sourceDevice", ctypes.c_void_p), ("hwndTarget", ctypes.c_void_p),
                ("ptPixelLocation", POINT), ("ptHimetricLocation", POINT),
                ("ptPixelLocationRaw", POINT), ("ptHimetricLocationRaw", POINT),
                ("dwTime", ctypes.c_uint32), ("historyCount", ctypes.c_uint32),
                ("InputData", ctypes.c_int32), ("dwKeyStates", ctypes.c_uint32),
                ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_int32)]


class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("penFlags", ctypes.c_uint32),
                ("penMask", ctypes.c_uint32), ("pressure", ctypes.c_uint32),
                ("rotation", ctypes.c_uint32), ("tiltX", ctypes.c_int32), ("tiltY", ctypes.c_int32)]


class _TouchPad(ctypes.Structure):          # POINTER_TOUCH_INFO: only its size matters
    _fields_ = [("pointerInfo", POINTER_INFO), ("rest", ctypes.c_byte * 48)]


class _Union(ctypes.Union):
    _fields_ = [("touchInfo", _TouchPad), ("penInfo", POINTER_PEN_INFO)]


class POINTER_TYPE_INFO(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("u", _Union)]


class Pen:
    def __init__(self):
        self.ok = False
        self.device = None
        self.down = False
        self.frame = 0
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            user32.CreateSyntheticPointerDevice.restype = ctypes.c_void_p
            user32.InjectSyntheticPointerInput.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            self.device = user32.CreateSyntheticPointerDevice(PT_PEN, 1, POINTER_FEEDBACK_DEFAULT)
            self.user32 = user32
            self.ok = bool(self.device)
        except (AttributeError, OSError):
            self.ok = False

    def send(self, state, x, y, pressure=0.5, tilt_x=0, tilt_y=0):
        """state: hover, down, move, up or leave. x, y: screen pixels.
        pressure 0..1; tilt in degrees (-90..90). Returns False if Windows refused."""
        if not self.ok:
            return False
        if state == "down":
            flags = F_INRANGE | F_INCONTACT | F_FIRSTBUTTON | F_DOWN
            self.down = True
        elif state == "move" and self.down:
            flags = F_INRANGE | F_INCONTACT | F_FIRSTBUTTON | F_UPDATE
        elif state == "up":
            if not self.down:
                return True
            flags = F_INRANGE | F_UP
            self.down = False
        elif state == "leave":
            flags = F_UPDATE                 # out of range: the pen cursor goes away
        else:                                # hover
            flags = F_INRANGE | F_UPDATE
        info = POINTER_TYPE_INFO(type=PT_PEN)
        pi = info.u.penInfo
        pi.pointerInfo.pointerType = PT_PEN
        pi.pointerInfo.pointerId = 0
        self.frame = (self.frame + 1) & 0xFFFFFFFF
        pi.pointerInfo.frameId = self.frame
        pi.pointerInfo.pointerFlags = flags | F_PRIMARY | F_CONFIDENCE
        pi.pointerInfo.ptPixelLocation = POINT(int(round(x)), int(round(y)))
        pi.penMask = PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y
        pi.pressure = int(max(0.0, min(1.0, pressure)) * 1024) if flags & F_INCONTACT else 0
        pi.tiltX = int(max(-90, min(90, tilt_x)))
        pi.tiltY = int(max(-90, min(90, tilt_y)))
        if not self.user32.InjectSyntheticPointerInput(self.device, ctypes.byref(info), 1):
            if state == "down":
                self.ok = False              # this PC can't take pen input: fall back to the mouse
                self.down = False
            return False
        return True
