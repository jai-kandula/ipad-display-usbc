"""Record what the iPad shows to an MP4 in recordings/ (next to this file).

It reuses the frames the stream already captures, so recording adds no
second screen capture: only a video encode at 30 frames a second, on the
graphics card's encoder when there is one (NVIDIA NVENC, AMD AMF, Intel
QSV), else x264. A still screen is recorded by repeating its last frame."""
import os
import threading
import time
from fractions import Fraction

try:
    import av  # type: ignore
    import numpy as np
    AVAILABLE = True
except Exception:
    AVAILABLE = False

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "recordings")
FPS = 30
# (encoder, pixel format it takes, options) fastest first; quality ~ a good screen recording.
CANDIDATES = [
    ("h264_nvenc", "bgra", {"preset": "p4", "rc": "vbr", "cq": "23", "bf": "0"}),
    ("h264_amf", "bgr0", {"quality": "balanced", "rc": "cqp", "qp_i": "22", "qp_p": "24"}),
    ("h264_qsv", "nv12", {"preset": "faster", "global_quality": "24"}),
    ("libx264", "yuv420p", {"preset": "veryfast", "crf": "23"}),
]


class Recorder:
    def __init__(self, width, height):
        if not AVAILABLE:
            raise RuntimeError("recording needs the 'av' package (start.bat installs it)")
        self.w, self.h = width - width % 2, height - height % 2
        os.makedirs(DIR, exist_ok=True)
        self.path = os.path.join(DIR, time.strftime("iPad %Y-%m-%d %H-%M-%S.mp4"))
        self.latest = None
        self.frames = 0
        self.error = None
        self._stop = threading.Event()
        self.container = av.open(self.path, "w")
        errors = []
        for name, pix, opts in CANDIDATES:
            try:
                st = self.container.add_stream(name, rate=FPS)
                st.width, st.height, st.pix_fmt = self.w, self.h, pix
                st.codec_context.time_base = Fraction(1, FPS)
                st.codec_context.options = dict(opts)
                st.codec_context.open()
                self.stream, self.pix, self.name = st, pix, name
                break
            except Exception as e:
                errors.append(f"{name}: {e}")
                self.container.close()
                self.container = av.open(self.path, "w")
        else:
            self.container.close()
            raise RuntimeError("no video encoder: " + "; ".join(errors))
        self.thread = threading.Thread(target=self._run, daemon=True, name="recorder")
        self.thread.start()

    def offer(self, arr):
        """The newest captured frame (BGRA). Cheap: just keeps a reference."""
        self.latest = arr

    def _frame(self, arr):
        a = arr[: self.h, : self.w]
        if self.pix == "bgra":
            try:
                return av.VideoFrame.from_numpy_buffer(a, format="bgra")
            except Exception:
                pass
        f = av.VideoFrame.from_ndarray(np.ascontiguousarray(a), format="bgra")
        return f if self.pix == "bgra" else f.reformat(format=self.pix)

    def _run(self):
        n, t0 = 0, time.perf_counter()
        try:
            while not self._stop.is_set():
                arr = self.latest
                if arr is not None and arr.shape[0] >= self.h and arr.shape[1] >= self.w:
                    f = self._frame(arr)
                    f.pts = n
                    for p in self.stream.encode(f):
                        self.container.mux(p)
                    n += 1
                    self.frames = n
                # Keep to 30 a second on the wall clock, so the video plays at real speed.
                wait = t0 + (n + 1) / FPS - time.perf_counter()
                if wait > 0:
                    self._stop.wait(wait)
                elif wait < -1.0:
                    t0 = time.perf_counter() - n / FPS       # fell behind (PC busy): don't rush to catch up
        except Exception as e:
            self.error = str(e)
        finally:
            try:
                for p in self.stream.encode(None):
                    self.container.mux(p)
            except Exception:
                pass
            self.container.close()

    def stop(self):
        """Finish the file. Returns (path, seconds) or (None, 0) if nothing was recorded."""
        self._stop.set()
        self.thread.join(timeout=5)
        secs = self.frames / FPS
        if not self.frames:
            try:
                os.remove(self.path)
            except OSError:
                pass
            return None, 0
        return self.path, secs
