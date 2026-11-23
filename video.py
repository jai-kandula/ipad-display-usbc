"""H.264 video for moving content (scrolling, dragging, video), using the
graphics card's encoder when there is one (NVIDIA NVENC, AMD AMF, Intel QSV)
and x264 otherwise. Needs the optional 'av' package (PyAV).

The stream is low-latency: no B-frames, no lookahead, one packet out per
frame in. It is sent in AVC format (length-prefixed NAL units) together with
an avcC "description", which is what WebCodecs' VideoDecoder expects."""
from fractions import Fraction

try:
    import av  # type: ignore
    import numpy as np
    AVAILABLE = True
except Exception:
    AVAILABLE = False

# (encoder, input pixel format, options), fastest first.
_NVENC = {"preset": "p1", "tune": "ull", "zerolatency": "1", "delay": "0",
          "rc": "vbr", "cq": "21", "bf": "0", "forced-idr": "1"}
CANDIDATES = [
    # NVENC takes the captured BGRA pixels as they are, so the frame is handed
    # over without any copy or colour conversion on the CPU.
    ("h264_nvenc", "bgra", _NVENC),
    ("h264_nvenc", "bgr0", _NVENC),
    ("h264_amf", "bgr0", {"usage": "ultralowlatency", "quality": "speed", "rc": "cqp",
                           "qp_i": "20", "qp_p": "22", "bf": "0"}),
    ("h264_qsv", "nv12", {"preset": "veryfast", "look_ahead": "0", "async_depth": "1",
                           "global_quality": "22", "bf": "0"}),
    ("libx264", "yuv420p", {"preset": "ultrafast", "tune": "zerolatency", "crf": "21",
                            "bf": "0", "rc-lookahead": "0"}),
]


def _nals(annexb):
    """Split an Annex B byte stream into NAL units (without start codes)."""
    out, i, n = [], 0, len(annexb)
    starts = []
    while True:
        j = annexb.find(b"\x00\x00\x01", i)
        if j < 0:
            break
        starts.append(j + 3)
        i = j + 3
    for k, s in enumerate(starts):
        e = starts[k + 1] - 3 if k + 1 < len(starts) else n
        nal = annexb[s:e]
        while nal.endswith(b"\x00") and k + 1 < len(starts):   # 4-byte start code's leading zero
            nal = nal[:-1]
        if nal:
            out.append(nal)
    return out


class Encoder:
    def __init__(self, width, height, fps=60, verbose=True):
        if not AVAILABLE:
            raise RuntimeError("PyAV (the 'av' package) is not installed")
        self.w, self.h = width, height
        self.pts = 0
        self.config = None          # (codec string, avcC bytes) once known
        self.ctx = None
        errors = []
        for name, pix, opts in CANDIDATES:
            try:
                ctx = av.CodecContext.create(name, "w")
                ctx.width, ctx.height = width, height
                ctx.pix_fmt = pix
                ctx.time_base = Fraction(1, fps)
                ctx.framerate = Fraction(fps, 1)
                ctx.gop_size = 100000              # key frames only when asked
                ctx.max_b_frames = 0
                ctx.bit_rate = 40_000_000
                ctx.options = dict(opts)
                ctx.open()
                self.ctx, self.name, self.pix = ctx, name, pix
                # Prove it works (a GPU encoder can open but fail on the first frame).
                self.encode(np.zeros((height, width, 4), np.uint8), key=True)
                break
            except Exception as e:
                errors.append(f"{name}: {e}")
                self.ctx = None
        if self.ctx is None:
            raise RuntimeError("no H.264 encoder works: " + "; ".join(errors))
        if verbose:
            gpu = {"h264_nvenc": "NVIDIA GPU", "h264_amf": "AMD GPU",
                   "h264_qsv": "Intel GPU", "libx264": "CPU"}[self.name]
            print(f"[video] H.264 encoding on the {gpu} ({self.name}) at {width}x{height}", flush=True)

    def encode(self, bgra, key=False):
        """bgra: HxWx4 uint8 (rows may be padded, as GPU capture gives them).
        Returns (avc bytes, is_key) or (None, False)."""
        frame = _wrap(bgra) if self.pix == "bgra" else None
        if frame is None:
            bgra = np.ascontiguousarray(bgra)
            if self.pix == "bgr0" and _has_bgr0():
                frame = av.VideoFrame.from_ndarray(bgra, format="bgr0")
            else:
                frame = av.VideoFrame.from_ndarray(bgra, format="bgra")
                if self.pix != "bgra":
                    frame = frame.reformat(format=self.pix)
        frame.pts = self.pts
        self.pts += 1
        if key:
            frame.pict_type = av.video.frame.PictureType.I
        data = b"".join(bytes(p) for p in self.ctx.encode(frame))
        if not data:
            return None, False
        nals = _nals(data)
        is_key = any((n[0] & 0x1F) == 5 for n in nals)
        sps = next((n for n in nals if (n[0] & 0x1F) == 7), None)
        pps = next((n for n in nals if (n[0] & 0x1F) == 8), None)
        if sps and pps:
            avcc = (bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1]) + len(sps).to_bytes(2, "big") + sps
                    + b"\x01" + len(pps).to_bytes(2, "big") + pps)
            self.config = (f"avc1.{sps[1]:02x}{sps[2]:02x}{sps[3]:02x}", avcc)
        body = b"".join(len(n).to_bytes(4, "big") + n for n in nals
                        if (n[0] & 0x1F) not in (7, 8, 9))       # SPS/PPS go in avcC; drop AUDs
        return body, is_key

    def close(self):
        try:
            self.ctx.close()
        except Exception:
            pass


def _wrap(bgra):
    """A frame that uses the array's memory directly (no copy), if PyAV can."""
    try:
        return av.VideoFrame.from_numpy_buffer(bgra, format="bgra")
    except Exception:
        return None


_BGR0 = None


def _has_bgr0():
    global _BGR0
    if _BGR0 is None:
        try:
            av.VideoFrame.from_ndarray(np.zeros((2, 2, 4), np.uint8), format="bgr0")
            _BGR0 = True
        except Exception:
            _BGR0 = False
    return _BGR0
