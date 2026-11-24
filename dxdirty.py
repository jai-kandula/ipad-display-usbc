"""GPU capture that copies only what changed.

Windows tells a screen grabber which parts of the screen changed since the
last frame (DXGI "dirty" and "move" rectangles), and whether only the mouse
pointer moved. dxcam ignores both and copies the whole screen off the GPU for
every new frame: 15 MB a frame on the iPad's 2360x1640 screen, about 7 ms of
one CPU core, 60 times a second, even when only a clock or a spinner changes.

DirtyGrab keeps one picture of the screen in memory and copies in only the
changed rectangles. A small animation then costs a fraction of a millisecond,
and a pointer move costs nothing (the iPad draws the pointer itself). Every
couple of seconds it copies the whole screen anyway, so a rectangle Windows
forgot to mention can't stay wrong for long.

It uses dxcam's own objects (device, duplicator, staging surface); if those
aren't what this expects, or anything fails, the caller goes back to plain
dxcam grabs. IPD_DIRTY=0 turns it off."""
import ctypes
import os
import time
from ctypes import wintypes

import numpy as np

ENABLED = os.environ.get("IPD_DIRTY", "1") != "0"
FULL_EVERY = 2.0          # seconds between whole-screen copies (a safety net)
MAX_RECTS = 24            # more rectangles than this: copy their bounding box instead
DXGI_ERROR_MORE_DATA = -2005270525          # 0x887A0003


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MOVE_RECT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long), ("rect", RECT)]


if os.name == "nt":
    # IDXGIOutputDuplication vtable: IUnknown (3) + IDXGIObject (4), GetDesc 7,
    # AcquireNextFrame 8, GetFrameDirtyRects 9, GetFrameMoveRects 10.
    _GET_DIRTY = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.UINT, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.UINT))(9, "GetFrameDirtyRects")
    _GET_MOVES = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.UINT, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.UINT))(10, "GetFrameMoveRects")
else:
    _GET_DIRTY = _GET_MOVES = None


class Unsupported(Exception):
    """This dxcam camera can't be used this way: use plain grabs."""


class ScreenChanged(Exception):
    """The screen's size or rotation changed: start capture again."""


def merge(rects, w, h, tile=64):
    """Rectangles (l, t, r, b) grown to the tile grid, clipped to the screen,
    overlapping ones joined, and turned into one box if there are many."""
    out = []
    for l, t, r, b in rects:
        l, t = max(0, l // tile * tile), max(0, t // tile * tile)
        r, b = min(w, -(-r // tile) * tile), min(h, -(-b // tile) * tile)
        if r > l and b > t:
            out.append([l, t, r, b])
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, c = out[i], out[j]
                if a[0] <= c[2] and c[0] <= a[2] and a[1] <= c[3] and c[1] <= a[3]:
                    out[i] = [min(a[0], c[0]), min(a[1], c[1]), max(a[2], c[2]), max(a[3], c[3])]
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    if len(out) > MAX_RECTS:
        out = [[min(x[0] for x in out), min(x[1] for x in out), max(x[2] for x in out), max(x[3] for x in out)]]
    return [tuple(x) for x in out]


class DirtyGrab:
    """grab() -> None (nothing new) or a list of changed (l, t, r, b) in self.frame."""

    def __init__(self, cam):
        if not ENABLED:
            raise Unsupported("turned off (IPD_DIRTY=0)")
        try:
            self.cam = cam
            self.dup = cam._duplicator
            self.stage = cam._stagesurf
            self.ctx = cam._device.im_context
            from dxcam._libs.d3d11 import D3D11_BOX
            self.box = D3D11_BOX()
            self.box.front, self.box.back = 0, 1
            self.w, self.h = int(cam.width), int(cam.height)
            rot = int(getattr(cam, "rotation_angle", 0) or 0)
        except Exception as e:
            raise Unsupported(f"this dxcam version is different ({type(e).__name__}: {e})")
        if rot != 0:
            raise Unsupported("the screen is rotated")
        if self.stage.width != self.w or self.stage.height != self.h:
            self.stage.release()
            self.stage.rebuild(output=cam._output, device=cam._device, dim=(self.w, self.h))
        self.frame = np.zeros((self.h, self.w, 4), np.uint8)
        self.next_full = 0.0
        self._rbuf = (RECT * 64)()
        self._mbuf = (MOVE_RECT * 32)()
        self.stats = {"frames": 0, "pointer_only": 0, "full": 0, "px": 0}

    # ---- the metadata Windows keeps for the frame being held
    def _call(self, fn, buf_attr, item):
        buf = getattr(self, buf_attr)
        need = wintypes.UINT(0)
        hr = fn(self.dup.duplicator, ctypes.sizeof(buf), ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(need))
        if hr == DXGI_ERROR_MORE_DATA:
            n = need.value // ctypes.sizeof(item) + 8
            buf = (item * n)()
            setattr(self, buf_attr, buf)
            hr = fn(self.dup.duplicator, ctypes.sizeof(buf), ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(need))
        if hr < 0:
            return None
        return buf[: need.value // ctypes.sizeof(item)]

    def changed_rects(self):
        """Dirty and moved-to rectangles of the held frame, or None if unknown."""
        if _GET_DIRTY is None:
            return None
        moves = self._call(_GET_MOVES, "_mbuf", MOVE_RECT)
        dirty = self._call(_GET_DIRTY, "_rbuf", RECT)
        if moves is None or dirty is None:
            return None
        out = [(r.left, r.top, r.right, r.bottom) for r in dirty]
        out += [(m.rect.left, m.rect.top, m.rect.right, m.rect.bottom) for m in moves]
        return out

    def grab(self):
        cam = self.cam
        if not cam._acquire_new_frame(wait_for_frame=True):
            if (int(cam.width), int(cam.height)) != (self.w, self.h) or int(getattr(cam, "rotation_angle", 0) or 0):
                raise ScreenChanged("the screen changed")
            self.dup = cam._duplicator        # (a new one after a recovery)
            return None
        dup = self.dup = cam._duplicator
        now = time.perf_counter()
        full = now >= self.next_full
        try:
            if not full and int(dup.accumulated_frames) == 0:
                self.stats["pointer_only"] += 1    # only the pointer moved: the picture is the same
                dup.release_frame()
                return None
            rects = None if full else self.changed_rects()
            if rects is None:
                rects, full = [(0, 0, self.w, self.h)], True
            rects = merge(rects, self.w, self.h)
            if not rects:
                dup.release_frame()
                return None
            with cam._multithread_guard():
                for l, t, r, b in rects:
                    self.box.left, self.box.top, self.box.right, self.box.bottom = l, t, r, b
                    self.ctx.CopySubresourceRegion(self.stage.texture, 0, l, t, 0, dup.texture, 0,
                                                   ctypes.byref(self.box))
                cam._release_frame_if_early_release()
                mapped = self.stage.map()
                try:
                    pitch = int(mapped.Pitch)
                    addr = ctypes.cast(mapped.pBits, ctypes.c_void_p).value
                    src = np.ctypeslib.as_array((ctypes.c_uint8 * (pitch * self.h)).from_address(addr))
                    src = src.reshape(self.h, pitch)
                    for l, t, r, b in rects:
                        self.frame[t:b, l:r] = src[t:b, l * 4:r * 4].reshape(b - t, r - l, 4)
                finally:
                    self.stage.unmap()
        finally:
            cam._release_frame_if_late_release()
        if full:
            self.next_full = now + FULL_EVERY
            self.stats["full"] += 1
        self.stats["frames"] += 1
        self.stats["px"] += sum((r - l) * (b - t) for l, t, r, b in rects)
        return rects
