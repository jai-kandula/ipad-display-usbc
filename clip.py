"""Windows clipboard access for the shared clipboard (text and pictures).

Only the standard Win32 calls through ctypes, no extra packages. Pictures go
over the wire as PNG and are stored on Windows as CF_DIB, which every app can
paste."""
import ctypes
import io
import sys

MAX_TEXT = 1_000_000          # characters
MAX_PNG = 8_000_000           # bytes

if sys.platform == "win32":
    from ctypes import wintypes
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    u32.OpenClipboard.argtypes = [wintypes.HWND]
    u32.OpenClipboard.restype = wintypes.BOOL
    u32.CloseClipboard.restype = wintypes.BOOL
    u32.EmptyClipboard.restype = wintypes.BOOL
    u32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    u32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    u32.GetClipboardData.argtypes = [wintypes.UINT]
    u32.GetClipboardData.restype = wintypes.HANDLE
    u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    u32.SetClipboardData.restype = wintypes.HANDLE
    k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k32.GlobalAlloc.restype = wintypes.HGLOBAL
    k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    k32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    k32.GlobalSize.restype = ctypes.c_size_t
    k32.GlobalFree.argtypes = [wintypes.HGLOBAL]

CF_DIB = 8
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def available():
    return sys.platform == "win32"


def sequence():
    """Changes every time anything is copied on Windows."""
    return u32.GetClipboardSequenceNumber() if available() else 0


class _Open:
    def __enter__(self):
        import time
        for _ in range(20):               # another app may hold it for a moment
            if u32.OpenClipboard(None):
                return self
            time.sleep(0.02)
        raise OSError("clipboard busy")

    def __exit__(self, *exc):
        u32.CloseClipboard()


def _read(fmt):
    h = u32.GetClipboardData(fmt)
    if not h:
        return None
    p = k32.GlobalLock(h)
    if not p:
        return None
    try:
        return ctypes.string_at(p, k32.GlobalSize(h))
    finally:
        k32.GlobalUnlock(h)


def read():
    """Returns ("text", str), ("png", bytes) or None."""
    if not available():
        return None
    with _Open():
        if u32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            raw = _read(CF_UNICODETEXT)
            if raw is None:
                return None
            text = raw.decode("utf-16-le", "ignore").split("\0", 1)[0]
            return ("text", text[:MAX_TEXT]) if text else None
        if u32.IsClipboardFormatAvailable(CF_DIB):
            dib = _read(CF_DIB)
        else:
            return None
    return _dib_to_png(dib) if dib else None


def _dib_to_png(dib):
    try:
        from PIL import Image
        # A DIB is a BMP file without its 14-byte header: add one back.
        hdr_size = int.from_bytes(dib[0:4], "little")
        bits = int.from_bytes(dib[14:16], "little")
        colors = int.from_bytes(dib[32:36], "little")
        comp = int.from_bytes(dib[16:20], "little")
        palette = (colors or (1 << bits if bits <= 8 else 0)) * 4
        masks = 12 if comp == 3 and hdr_size == 40 else 0
        off = 14 + hdr_size + masks + palette
        bmp = b"BM" + (14 + len(dib)).to_bytes(4, "little") + b"\0\0\0\0" + off.to_bytes(4, "little") + dib
        im = Image.open(io.BytesIO(bmp))
        out = io.BytesIO()
        im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(out, "PNG", optimize=False, compress_level=3)
        data = out.getvalue()
        return ("png", data) if len(data) <= MAX_PNG else None
    except Exception:
        return None


def _set(fmt, data):
    h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        raise MemoryError("GlobalAlloc failed")
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    if not u32.SetClipboardData(fmt, h):
        k32.GlobalFree(h)
        raise OSError("SetClipboardData failed")


def write_text(text):
    if not available():
        return
    data = text[:MAX_TEXT].encode("utf-16-le") + b"\0\0"
    with _Open():
        u32.EmptyClipboard()
        _set(CF_UNICODETEXT, data)


def write_png(png):
    if not available() or len(png) > MAX_PNG:
        return
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    out = io.BytesIO()
    im.save(out, "BMP")
    dib = out.getvalue()[14:]           # BMP file minus its file header = CF_DIB
    with _Open():
        u32.EmptyClipboard()
        _set(CF_DIB, dib)
