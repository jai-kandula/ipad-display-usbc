"""Self-test: checks everything on this PC and measures what the iPad costs it.

Run selftest.bat (or: .venv\\Scripts\\python selftest.py) while the host
(start.bat) is running and the iPad is plugged in. It never presses keys,
clicks or changes settings. It writes selftest-report.txt and
selftest-report.json next to this file.

  1. This PC: CPU, GPU and driver, memory, Windows build, power plan
  2. Screens: what each one is, which of the known setups this is
  3. Capture and encode speed on the iPad's screen
  4. A full stream to a test client: frame rate, smoothness, cost to the PC
     (host CPU, GPU, video encoder), and whether any monitor's refresh changed
  5. Stress: hundreds of requests, rapid reconnects, bad messages, then a check
     that nothing leaked and the stream still works
  6. Live tiles: GPU, CPU temperature, now playing

Options: --quick (shorter runs), --port N, --seconds N (stream test length),
--only stream,stress,... (run some sections)."""
import argparse
import asyncio
import ctypes
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
REPORT = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "results": [], "sections": {}}
LINES = []


def say(msg=""):
    print(msg, flush=True)
    LINES.append(msg)


def result(name, ok, detail=""):
    """ok: True (pass), False (fail), None (warning / can't tell)."""
    tag = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
    REPORT["results"].append({"test": name, "result": tag, "detail": detail})
    say(f"  [{tag}] {name}" + (f": {detail}" if detail else ""))


def pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]


def ps(cmd, timeout=30):
    try:
        return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                              capture_output=True, text=True, timeout=timeout,
                              creationflags=_NO_WINDOW).stdout.strip()
    except Exception as e:
        return f"error: {e}"


def ps_json(cmd):
    out = ps(cmd + " | ConvertTo-Json -Compress -Depth 3")
    try:
        return json.loads(out) if out else None
    except ValueError:
        return out


# ---------------------------------------------------------------- 1. this PC
def section_pc():
    say("\n1. This PC")
    pc = {"python": sys.version.split()[0], "platform": platform.platform()}
    if IS_WINDOWS:
        pc["cpu"] = ps_json("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,"
                            "NumberOfLogicalProcessors,MaxClockSpeed")
        pc["system"] = ps_json("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,"
                               "TotalPhysicalMemory")
        pc["memory"] = ps_json("Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed,"
                               "ConfiguredClockSpeed,Manufacturer")
        pc["gpus"] = ps_json("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,"
                             "CurrentHorizontalResolution,CurrentVerticalResolution,CurrentRefreshRate,AdapterRAM")
        pc["windows"] = ps_json("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,"
                                "BuildNumber,OSArchitecture")
        pc["power_plan"] = ps("powercfg /getactivescheme")
        pc["on_battery"] = ps("(Get-CimInstance Win32_Battery | Select-Object -First 1).BatteryStatus")
        pc["hags"] = ps("(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' "
                        "-ErrorAction SilentlyContinue).HwSchMode")           # 2 = on
        pc["game_mode"] = ps("(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\GameBar' "
                             "-ErrorAction SilentlyContinue).AutoGameModeEnabled")
        pc["vdd"] = ps_json("Get-PnpDevice -Class Display | Select-Object FriendlyName,Status")
        try:
            pc["nvidia"] = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,driver_version,pstate,clocks.max.graphics,"
                 "power.limit,pcie.link.gen.current,pcie.link.width.current,encoder.stats.sessionCount",
                 "--format=csv"], capture_output=True, text=True, timeout=10,
                creationflags=_NO_WINDOW).stdout.strip()
        except Exception as e:
            pc["nvidia"] = f"not found ({e})"
    pkgs = {}
    try:
        from importlib import metadata
        for p in ("aiohttp", "mss", "numpy", "Pillow", "simplejpeg", "dxcam", "av", "soundcard", "pystray"):
            try:
                pkgs[p] = metadata.version(p)
            except Exception:
                pkgs[p] = None
    except Exception:
        pass
    pc["packages"] = pkgs
    REPORT["sections"]["pc"] = pc
    cpu = pc.get("cpu") or {}
    if isinstance(cpu, list):
        cpu = cpu[0]
    gpus = pc.get("gpus") or []
    gpus = gpus if isinstance(gpus, list) else [gpus]
    win = pc.get("windows") or {}
    say(f"  CPU: {cpu.get('Name', '?') if isinstance(cpu, dict) else cpu}")
    for g in gpus:
        if isinstance(g, dict):
            say(f"  GPU: {g.get('Name')} (driver {g.get('DriverVersion')})")
    if isinstance(win, dict):
        say(f"  Windows: {win.get('Caption')} build {win.get('BuildNumber')}")
    say(f"  Python {pc['python']}; " + ", ".join(f"{k} {v}" for k, v in pkgs.items() if v))
    for need in ("dxcam", "av", "simplejpeg"):
        result(f"package {need} installed", True if pkgs.get(need) else None,
               "" if pkgs.get(need) else "missing: run start.bat once with internet")
    if pc.get("on_battery") == "1":
        result("on charger", None, "running on battery: Windows may slow the CPU and GPU; plug in for the real numbers")


# ---------------------------------------------------------------- 2. screens
KNOWN_SETUPS = {
    1: "laptop screen + 1080p 240 Hz monitor + iPad",
    2: "laptop + iPad",
    3: "laptop screen off + 1440p 144 Hz monitor + 1080p 60 Hz monitor + iPad",
    4: "laptop screen off + 1440p 144 Hz monitor + two 1080p 60 Hz monitors + iPad",
}


def which_setup(scr):
    laptop = any(s["kind"] == "laptop" for s in scr)
    ext = [s for s in scr if s["kind"] == "external"]
    fast = [s for s in ext if (s["hz"] or 0) > 100]
    slow = [s for s in ext if (s["hz"] or 60) <= 100]
    if laptop and len(ext) == 1 and fast and fast[0]["height"] in (1080, 1200):
        return 1
    if laptop and not ext:
        return 2
    if not laptop and len(fast) == 1 and fast[0]["height"] == 1440:
        if len(slow) == 1:
            return 3
        if len(slow) == 2:
            return 4
    return None


def get_screens():
    import mss
    import screens
    MSS = getattr(mss, "MSS", None) or mss.mss
    with MSS() as sct:
        return screens.describe(sct.monitors)


def section_screens():
    import screens
    say("\n2. Screens")
    scr = get_screens()
    for s in scr:
        say(f"  #{s['index']} {s['kind']:8} {s['width']}x{s['height']} "
            f"{(str(round(s['hz'])) + ' Hz') if s['hz'] else '?':>7}  at ({s['left']},{s['top']})  "
            f"{s['name']} {s['connection']}{'  (main)' if s['primary'] else ''}")
    n = which_setup(scr)
    REPORT["sections"]["screens"] = {"list": scr, "summary": screens.summary(scr), "setup": n}
    say(f"  This looks like setup {n}: {KNOWN_SETUPS[n]}" if n else "  This isn't one of your 4 usual setups.")
    ipad = [s for s in scr if s["kind"] == "ipad"]
    result("iPad's virtual screen found", True if ipad else None,
           f"{ipad[0]['width']}x{ipad[0]['height']}" if ipad else
           "no virtual screen (is the host running with a profile that shows the screen?)")
    pick = screens.pick_ipad(scr)
    result("host streams the right screen by default",
           (pick is not None and pick["kind"] == "ipad") if ipad else None,
           f"picks #{pick['index']} ({pick['kind']})" if pick else "no screen")
    if pick and (pick["hz"] or 60) > 75 and pick["kind"] != "ipad":
        result("default screen isn't a gaming monitor", False, f"#{pick['index']} is {pick['hz']} Hz")
    return scr


# ---------------------------------------------------------------- process cost
class Proc:
    """CPU time, memory and handles of the host process (Windows)."""

    def __init__(self, pid):
        self.pid = pid
        self.h = None
        if IS_WINDOWS and pid:
            self.h = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0400, False, pid)   # QUERY_LIMITED | QUERY

    def cpu(self):
        if not self.h:
            return None
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        ctypes.windll.kernel32.GetProcessTimes(self.h, ctypes.byref(c), ctypes.byref(e),
                                               ctypes.byref(k), ctypes.byref(u))
        f = lambda t: ((t.dwHighDateTime << 32) | t.dwLowDateTime) / 1e7     # noqa: E731
        return f(k) + f(u)

    def mem_mb(self):
        if not self.h:
            return None

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + \
                       [(n, ctypes.c_size_t) for n in ("peak_ws", "ws", "qpp", "qp", "qnpp", "qnp", "pf", "ppf")]
        m = PMC(cb=ctypes.sizeof(PMC))
        ctypes.windll.psapi.GetProcessMemoryInfo(self.h, ctypes.byref(m), m.cb)
        return round(m.ws / 2 ** 20, 1)

    def handles(self):
        if not self.h:
            return None
        n = wintypes.DWORD()
        ctypes.windll.kernel32.GetProcessHandleCount(self.h, ctypes.byref(n))
        return n.value


def host_pid(port):
    if not IS_WINDOWS:
        return None
    out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True,
                         creationflags=_NO_WINDOW).stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 5 and p[1].endswith(f":{port}") and p[3] == "LISTENING":
            return int(p[4])
    return None


class GpuSampler(threading.Thread):
    """nvidia-smi every 0.5 s: GPU load, video encoder load, temperature, power."""

    def __init__(self):
        super().__init__(daemon=True)
        self.samples, self.stop_ = [], False

    def run(self):
        q = "utilization.gpu,utilization.encoder,temperature.gpu,power.draw,clocks.gr"
        try:
            p = subprocess.Popen(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits",
                                  "-lms", "500"], stdout=subprocess.PIPE, text=True,
                                 creationflags=_NO_WINDOW)
        except Exception:
            return
        try:
            for line in p.stdout:
                if self.stop_:
                    break
                try:
                    self.samples.append([float(x) for x in line.split(",")])
                except ValueError:
                    pass
        finally:
            p.kill()

    def mean(self, i):
        v = [s[i] for s in self.samples if len(s) > i]
        return round(statistics.mean(v), 1) if v else None


def measure(proc, seconds, during=None):
    """Average host CPU (% of one core and % of the whole CPU), GPU and encoder load over `seconds`."""
    g = GpuSampler()
    g.start()
    c0, t0 = proc.cpu(), time.perf_counter()
    if during:
        during(seconds)
    else:
        time.sleep(seconds)
    c1, t1 = proc.cpu(), time.perf_counter()
    g.stop_ = True
    core = round(100 * (c1 - c0) / (t1 - t0), 1) if c0 is not None else None
    return {"cpu_core_pct": core, "cpu_total_pct": round(core / (os.cpu_count() or 1), 2) if core is not None else None,
            "gpu_pct": g.mean(0), "encoder_pct": g.mean(1), "gpu_temp": g.mean(2), "gpu_watts": g.mean(3),
            "ram_mb": proc.mem_mb()}


# ---------------------------------------------------------------- moving picture on a screen
def animate(x, y, w, h, seconds):
    """(Run in its own process.) A window full of motion on one screen."""
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.attributes("-topmost", True)
    c = tk.Canvas(root, width=w, height=h, highlightthickness=0, bg="#101820")
    c.pack()
    boxes = [c.create_rectangle(0, 0, w // 6, h // 4, fill=col, outline="")
             for col in ("#e63946", "#2a9d8f", "#e9c46a", "#457b9d", "#f4a261", "#8338ec")]
    txt = c.create_text(w // 2, h // 2, text="iPad Display self-test", fill="white", font=("Segoe UI", 48, "bold"))
    end, t = time.time() + seconds, [0]

    def tick():
        t[0] += 1
        for i, b in enumerate(boxes):
            px = (t[0] * (7 + i * 3)) % (w + w // 6) - w // 6
            py = (i * h // 6 + t[0] * (2 + i)) % h
            c.coords(b, px, py, px + w // 6, py + h // 4)
        c.itemconfigure(txt, text=f"iPad Display self-test  {t[0]}")
        if time.time() < end:
            root.after(16, tick)
        else:
            root.destroy()
    tick()
    root.mainloop()


def start_motion(mon, seconds):
    return subprocess.Popen([sys.executable, os.path.abspath(__file__), "--animate",
                             str(mon["left"]), str(mon["top"]), str(mon["width"]), str(mon["height"]),
                             str(seconds)], creationflags=_NO_WINDOW)


# ---------------------------------------------------------------- 3. capture and encode
def section_bench(scr, quick):
    say("\n3. Capture and encode on the iPad's screen")
    import screens
    mon = screens.pick_ipad(scr)
    if not mon:
        result("bench", None, "no screen to test")
        return
    out = {"screen": f"{mon['width']}x{mon['height']}"}
    secs = 3 if quick else 6
    motion = start_motion(mon, secs * 3 + 4)
    time.sleep(1.5)
    try:
        import mss
        import numpy as np
        MSS = getattr(mss, "MSS", None) or mss.mss
        with MSS() as sct:
            box = {k: mon[k] for k in ("left", "top", "width", "height")}
            times = []
            end = time.perf_counter() + secs
            while time.perf_counter() < end:
                t = time.perf_counter()
                sct.grab(box)
                times.append((time.perf_counter() - t) * 1000)
        out["gdi_ms"] = round(statistics.mean(times), 2)
        out["gdi_p95_ms"] = round(pct(times, 95), 2)
        say(f"  GDI capture: {out['gdi_ms']} ms a frame (p95 {out['gdi_p95_ms']} ms)")
        frame = None
        try:
            import dxcam
            cam = None
            for d, outs in enumerate(getattr(dxcam, "__factory").outputs):
                for o, op in enumerate(outs):
                    op.update_desc()
                    rc = op.desc.DesktopCoordinates
                    if (rc.left, rc.top) == (mon["left"], mon["top"]):
                        cam = dxcam.create(device_idx=d, output_idx=o, output_color="BGRA",
                                           processor_backend="numpy")
            if cam is None:
                result("GPU capture finds the iPad's screen", False, "no DXGI output at that position")
            else:
                times, n, end = [], 0, time.perf_counter() + secs
                t_start = time.perf_counter()
                while time.perf_counter() < end:
                    t = time.perf_counter()
                    f = cam.grab(new_frame_only=True)
                    if f is None:
                        time.sleep(0.001)
                        continue
                    n += 1
                    frame = f
                    times.append((time.perf_counter() - t) * 1000)
                cam.release()
                fps = n / (time.perf_counter() - t_start)
                out.update(gpu_fps=round(fps, 1), gpu_ms=round(statistics.mean(times), 2) if times else None,
                           gpu_p95_ms=round(pct(times, 95), 2) if times else None)
                say(f"  GPU capture: {out['gpu_fps']} new frames/s, {out['gpu_ms']} ms each (p95 {out['gpu_p95_ms']})")
                result("GPU capture keeps up with 60 fps", fps >= 55, f"{fps:.0f} fps of motion captured")
        except Exception as e:
            result("GPU capture", None, f"unavailable: {e}")
        if frame is None:
            with MSS() as sct:
                frame = np.frombuffer(sct.grab(box).raw, np.uint8).reshape(mon["height"], mon["width"], 4)
        frame = np.ascontiguousarray(frame)
        try:
            import simplejpeg
            times = []
            for _ in range(30):
                t = time.perf_counter()
                simplejpeg.encode_jpeg(frame, quality=80, colorspace="BGRA", colorsubsampling="420")
                times.append((time.perf_counter() - t) * 1000)
            out["jpeg_ms"] = round(statistics.mean(times), 2)
            say(f"  JPEG (whole screen): {out['jpeg_ms']} ms")
        except Exception as e:
            say(f"  JPEG: {e}")
        try:
            import video
            enc = video.Encoder(mon["width"] - mon["width"] % 2, mon["height"] - mon["height"] % 2, verbose=False)
            f2 = np.ascontiguousarray(frame[: enc.h, : enc.w])
            times = []
            for i in range(120 if not quick else 60):
                f2[:, :, 0] = (i * 3) % 255                      # change every frame
                t = time.perf_counter()
                enc.encode(f2)
                times.append((time.perf_counter() - t) * 1000)
            out.update(encoder=enc.name, enc_ms=round(statistics.mean(times[5:]), 2),
                       enc_p95_ms=round(pct(times[5:], 95), 2))
            say(f"  Video encode ({enc.name}): {out['enc_ms']} ms a frame (p95 {out['enc_p95_ms']})")
            result("hardware video encoder", enc.name.endswith(("nvenc", "amf", "qsv")), enc.name)
            result("video encode fits in a 60 fps frame", out["enc_p95_ms"] < 8, f"p95 {out['enc_p95_ms']} ms")
        except Exception as e:
            result("video encoder", False, str(e))
    finally:
        motion.kill()
    REPORT["sections"]["bench"] = out


# ---------------------------------------------------------------- 4. stream to a test client
async def stream_client(base, seconds, query="monitor=auto&mode=wired&fps=60&video=1", motion_mon=None):
    import aiohttp
    res = {"frames": 0, "bytes": 0, "gaps": [], "speed": [], "info": None, "errors": []}
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(f"{base.replace('http', 'ws')}/ws?{query}", max_msg_size=64 << 20) as ws:
            last, end = None, time.perf_counter() + seconds
            while time.perf_counter() < end:
                try:
                    msg = await ws.receive(timeout=max(0.1, end - time.perf_counter()))
                except asyncio.TimeoutError:
                    break
                if msg.type == aiohttp.WSMsgType.BINARY:
                    now = time.perf_counter()
                    seq = int.from_bytes(msg.data[:4], "little")
                    await ws.send_str(json.dumps({"t": "ack", "seq": seq}))
                    res["frames"] += 1
                    res["bytes"] += len(msg.data)
                    if last is not None:
                        res["gaps"].append((now - last) * 1000)
                    last = now
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    m = json.loads(msg.data)
                    if m.get("type") == "speed":
                        res["speed"].append(m)
                    elif m.get("type") == "info":
                        res["info"] = m
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    res["errors"].append(str(msg))
                    break
    return res


def summarize_stream(r, seconds):
    gaps = r["gaps"]
    sp = r["speed"][1:] or r["speed"]
    avg = lambda k: round(statistics.mean([s[k] for s in sp if isinstance(s.get(k), (int, float))]), 1) \
        if sp else None                                                          # noqa: E731
    return {"fps": round(r["frames"] / seconds, 1), "mbit": round(r["bytes"] * 8 / 1e6 / seconds, 1),
            "gap_p50_ms": round(pct(gaps, 50), 1) if gaps else None,
            "gap_p99_ms": round(pct(gaps, 99), 1) if gaps else None,
            "gap_max_ms": round(max(gaps), 1) if gaps else None,
            "stutters_over_50ms": sum(1 for g in gaps if g > 50),
            "host_capture_ms": avg("cap"), "host_work_ms": avg("work"), "host_encode_ms": avg("enc"),
            "host_screen_fps": avg("screen"), "backend": sp[-1].get("backend") if sp else None,
            "encoder": sp[-1].get("encoder") if sp else None}


def section_stream(base, proc, scr, seconds):
    say("\n4. Full stream to a test client (the iPad can stay connected)")
    import screens
    mon = screens.pick_ipad(scr)
    before_hz = {s["gdi"] or s["index"]: s["hz"] for s in scr}
    out = {}
    out["idle"] = measure(proc, 5)
    say(f"  Host doing nothing: {out['idle']['cpu_total_pct']}% CPU, {out['idle']['ram_mb']} MB")

    def run(label, secs, motion):
        box = {}
        mp = start_motion(mon, secs + 3) if (motion and mon) else None
        time.sleep(1.0 if mp else 0)

        def during(s):
            box["r"] = asyncio.run(stream_client(base, s))
        cost = measure(proc, secs, during)
        if mp:
            mp.kill()
        s = summarize_stream(box["r"], secs)
        s.update(cost)
        out[label] = s
        say(f"  {label}: {s['fps']} fps to the client, frame gap p99 {s['gap_p99_ms']} ms, max {s['gap_max_ms']} ms, "
            f"{s['mbit']} Mbit/s | host: capture {s['host_capture_ms']} ms ({s['backend']}), encode "
            f"{s['host_encode_ms']} ms ({s['encoder']}) | cost: {s['cpu_total_pct']}% CPU "
            f"({s['cpu_core_pct']}% of a core), GPU {s['gpu_pct']}%, encoder {s['encoder_pct']}%, {s['ram_mb']} MB")
        return s
    still = run("still screen", max(6, seconds // 2), False)
    moving = run("moving picture", seconds, True)
    result("stream runs at 60 fps with motion", moving["fps"] >= 55, f"{moving['fps']} fps")
    result("no stutters (gaps over 50 ms)", moving["stutters_over_50ms"] == 0,
           f"{moving['stutters_over_50ms']} in {seconds} s, worst {moving['gap_max_ms']} ms")
    result("still screen costs almost nothing", (still["cpu_total_pct"] or 0) < 1.5,
           f"{still['cpu_total_pct']}% CPU, {still['fps']} updates/s")
    after = get_screens()
    changed = [f"{s['name'] or s['index']}: {before_hz.get(s['gdi'] or s['index'])} -> {s['hz']} Hz"
               for s in after if s["kind"] != "ipad" and before_hz.get(s["gdi"] or s["index"]) not in (None, s["hz"])]
    result("monitors keep their refresh rate", not changed, "; ".join(changed) or
           ", ".join(f"{s['name'] or s['kind']} {round(s['hz'] or 0)} Hz" for s in after if s["kind"] != "ipad"))
    REPORT["sections"]["stream"] = out


# ---------------------------------------------------------------- 5. stress
async def stress(base, proc):
    import aiohttp
    out = {}
    h0, m0 = proc.handles(), proc.mem_mb()
    lat, errs = [], []
    paths = ["/api/profiles", "/api/monitors", "/api/settings", "/api/tiles?want=cpu,ram",
             "/api/apps", "/"]
    async with aiohttp.ClientSession() as http:
        async def one(p):
            t = time.perf_counter()
            try:
                async with http.get(base + p) as r:
                    await r.read()
                    if r.status >= 500:
                        errs.append(f"{p} -> {r.status}")
            except Exception as e:
                errs.append(f"{p}: {e}")
            lat.append((time.perf_counter() - t) * 1000)
        for _ in range(10):
            await asyncio.gather(*(one(p) for p in paths * 8))
        out["http"] = {"requests": len(lat), "errors": errs[:10], "p50_ms": round(pct(lat, 50), 1),
                       "p99_ms": round(pct(lat, 99), 1)}
        result("480 requests in bursts of 48", not errs,
               f"p50 {out['http']['p50_ms']} ms, p99 {out['http']['p99_ms']} ms" + (f", errors: {errs[:3]}" if errs else ""))

        bad = []                                           # wrong data must be refused, never crash or save
        for body in ({"profiles": []}, {"profiles": [{"name": ""}]}, {"profiles": [{"name": "x", "buttons": [{"keys": "ctrl+nope"}]}]},
                     {"profiles": [{"name": "x", "buttons": [{"tile": "nope"}]}]}, "junk", ["x"],
                     {"profiles": ["x"]}, {"profiles": [{"name": "x", "cols": "wide"}]},
                     {"profiles": [{"name": "x", "buttons": [{"steps": [{"steps": []}]}]}]}):
            async with http.put(base + "/api/profiles", data=json.dumps(body),
                                headers={"Content-Type": "application/json"}) as r:
                if r.status != 400:
                    bad.append(f"{str(body)[:40]} -> {r.status}")
        for path, method, body in (("/api/action", "POST", {"profile": "no such", "index": 99}),
                                   ("/api/action", "POST", ["x"]), ("/api/action", "POST", {"profile": None}),
                                   ("/api/profile", "POST", {"name": "no such profile"}),
                                   ("/api/profile", "POST", "junk"), ("/api/settings", "PUT", "junk"),
                                   ("/api/settings", "PUT", ["x"])):
            async with http.request(method, base + path, data=json.dumps(body),
                                    headers={"Content-Type": "application/json"}) as r:
                if r.status != 400:
                    bad.append(f"{method} {path} {str(body)[:30]} -> {r.status}")
        result("bad edits are refused safely", not bad, "; ".join(bad))

        # The picture must stay smooth while the iPad's deck, tiles and menus ask the PC things.
        import screens
        mon = screens.pick_ipad(get_screens())
        mp = start_motion(mon, 10) if mon else None
        await asyncio.sleep(1.5)

        async def chatter(secs):
            end = time.perf_counter() + secs
            while time.perf_counter() < end:
                await asyncio.gather(*(one(p) for p in paths))
                await asyncio.sleep(0.05)
        r, _ = await asyncio.gather(stream_client(base, 6), chatter(6))
        if mp:
            mp.kill()
        s = summarize_stream(r, 6)
        out["smooth_under_load"] = s
        result("picture stays smooth while ~100 requests/s arrive", s["stutters_over_50ms"] == 0 and s["fps"] >= 50,
               f"{s['fps']} fps, worst gap {s['gap_max_ms']} ms")

        # Rapid reconnects, plus messages that make no sense (never input that moves or types).
        fails = 0
        for i in range(25):
            try:
                async with http.ws_connect(base.replace("http", "ws") + "/ws?monitor=auto&mode=wired&fps=60&video=1") as ws:
                    for junk in ('{"t":"ack","seq":"x"}', "not json", '{"t":"mode","mode":5}', '{"t":12}',
                                 '{"t":"refresh"}', '{"t":"ping","c":"x"}', '{"t":"vkey"}', "{}" * 5000):
                        await ws.send_str(junk)
                    await asyncio.sleep(0.05 if i % 3 else 0.4)
            except Exception:
                fails += 1
        result("25 quick reconnects with bad messages", fails == 0, f"{fails} failed to connect")
    await asyncio.sleep(3)
    import screens
    mon = screens.pick_ipad(get_screens())
    mp = start_motion(mon, 8) if mon else None
    await asyncio.sleep(1.5)
    r = await stream_client(base, 4)
    if mp:
        mp.kill()
    result("stream still works after the stress", r["frames"] > 150 if mp else r["frames"] > 0,
           f"{r['frames']} frames in 4 s")
    await asyncio.sleep(4)                                 # let sessions close
    h1, m1 = proc.handles(), proc.mem_mb()
    out["leak"] = {"handles": [h0, h1], "ram_mb": [m0, m1]}
    if h0 is not None:
        result("nothing leaked", h1 - h0 < 200 and m1 - m0 < 150,
               f"handles {h0} -> {h1}, memory {m0} -> {m1} MB")
    REPORT["sections"]["stress"] = out


# ---------------------------------------------------------------- 6. tiles
def section_tiles(base):
    say("\n6. Live tiles")
    url = base + "/api/tiles?want=cpu,gpu,ram,media,obs"
    try:
        for _ in range(4):
            with urllib.request.urlopen(url, timeout=5) as r:
                d = json.load(r)
            time.sleep(1.5)
    except Exception as e:
        result("tiles answer", False, str(e))
        return
    REPORT["sections"]["tiles"] = d
    result("CPU load", d.get("cpu", {}).get("load") is not None, f"{d.get('cpu', {}).get('load')}%")
    result("CPU temperature", True if d.get("cpu", {}).get("temp") else None,
           f"{d['cpu']['temp']} C" if d.get("cpu", {}).get("temp") else
           "needs LibreHardwareMonitor running with Options > Remote Web Server on")
    g = d.get("gpu")
    result("GPU numbers", bool(g), f"{g['name']}: {g['load']}%, {g['temp']} C" if g else "nvidia-smi gave nothing")
    m = d.get("media")
    result("now playing", True if m else None,
           f"{m['title']} - {m['artist']}" if m else "nothing playing (play a song in Spotify to test)")
    o = d.get("obs") or {}
    result("OBS", True if o.get("connected") else None,
           f"connected, scene '{o.get('scene')}', {len(o.get('scenes', []))} scenes" if o.get("connected") else
           (o.get("error") or "not open (open OBS and turn on Tools > WebSocket Server Settings to test)"))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--only", default="")
    ap.add_argument("--animate", nargs=5, type=int)
    a = ap.parse_args()
    if a.animate:
        animate(*a.animate)
        return
    only = set(filter(None, a.only.split(",")))
    want = lambda s: not only or s in only        # noqa: E731
    base = f"http://127.0.0.1:{a.port}"
    secs = 10 if a.quick else a.seconds
    say("iPad Display self-test  " + REPORT["started"])
    say("This takes about two minutes. Windows of moving colours will flash on the iPad: that's the test.")
    try:
        urllib.request.urlopen(base + "/api/profiles", timeout=3).read()
        host_ok = True
    except Exception as e:
        host_ok = False
        say(f"\n  The host isn't answering on port {a.port} ({e}). Start start.bat first for sections 4 to 6.")
    pid = host_pid(a.port)
    proc = Proc(pid)
    REPORT["host_pid"] = pid
    steps = [("pc", section_pc), ("screens", None), ("bench", None), ("stream", None), ("stress", None), ("tiles", None)]
    scr = []
    for name, _ in steps:
        if not want(name):
            continue
        try:
            if name == "pc":
                section_pc()
            elif name == "screens":
                scr = section_screens()
            elif name == "bench":
                section_bench(scr or get_screens(), a.quick)
            elif name == "stream" and host_ok:
                section_stream(base, proc, scr or get_screens(), secs)
            elif name == "stress" and host_ok:
                say("\n5. Stress")
                asyncio.run(stress(base, proc))
            elif name == "tiles" and host_ok:
                section_tiles(base)
        except Exception as e:
            result(f"section {name} ran", False, f"{e}\n{traceback.format_exc()}")
    fails = [r for r in REPORT["results"] if r["result"] == "FAIL"]
    warns = [r for r in REPORT["results"] if r["result"] == "WARN"]
    say(f"\nDone: {len(REPORT['results']) - len(fails) - len(warns)} passed, {len(warns)} warnings, {len(fails)} failed.")
    with open(os.path.join(HERE, "selftest-report.json"), "w", encoding="utf-8") as f:
        json.dump(REPORT, f, indent=1, default=str)
    with open(os.path.join(HERE, "selftest-report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LINES) + "\n")
    say("Saved selftest-report.txt and selftest-report.json in this folder.")


if __name__ == "__main__":
    main()
