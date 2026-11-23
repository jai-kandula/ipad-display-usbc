"""PC sound -> iPad: records what the laptop is playing (WASAPI loopback,
via the optional 'soundcard' package) and streams it as 16-bit PCM over a
WebSocket at /audio. 48 kHz stereo is about 1.5 Mbit/s, tiny for the cable."""
import asyncio
import sys
import threading
import warnings

# A short gap in the PC's sound (e.g. nothing playing) is normal; don't fill the log with it.
warnings.filterwarnings("ignore", message="data discontinuity")

RATE = 48000
CHANNELS = 2
BLOCK = 480                   # 10 ms per packet

try:
    import numpy as np
    import soundcard  # type: ignore  # noqa: F401
    AVAILABLE = sys.platform == "win32"
except Exception:             # not installed, or not Windows
    AVAILABLE = False


def _record(loop, queue, stop):
    """Runs on its own thread: loopback-record the default speakers."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)       # COM, multithreaded
    except Exception:
        pass
    import soundcard as sc
    try:
        speaker = sc.default_speaker()
        mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        print(f"[sound] sending '{speaker.name}' to the iPad", flush=True)
        with mic.recorder(samplerate=RATE, channels=CHANNELS, blocksize=BLOCK) as rec:
            while not stop.is_set():
                data = rec.record(numframes=BLOCK)          # float32, frames x channels
                if not data.any():
                    continue                                # silence: send nothing
                pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                loop.call_soon_threadsafe(_put, queue, pcm)
    except Exception as e:
        print("[sound] stopped:", e, flush=True)
    finally:
        loop.call_soon_threadsafe(_put, queue, None)


def _put(queue, item):
    if queue.full():              # the iPad fell behind: drop the oldest
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(item)


async def stream(ws):
    """Send PC sound to one connected iPad until it disconnects."""
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue(maxsize=20)           # at most 200 ms waiting
    stop = threading.Event()
    threading.Thread(target=_record, args=(loop, queue, stop), daemon=True,
                     name="pc-sound").start()
    try:
        while not ws.closed:
            pcm = await queue.get()
            if pcm is None:
                break
            await ws.send_bytes(pcm)
    finally:
        stop.set()
