"""What is using each graphics card, and how much.

Reads Windows' own GPU counters (the numbers Task Manager is built on) once a
second and splits them by graphics card (NVIDIA and Intel separately), by
engine (3D, video encode, copy, ...) and by program, with each program's
dedicated video memory (on the card itself) and shared memory (borrowed from
RAM) shown apart. The iPad Display host is picked out by its command line.

    python gpucheck.py [seconds] [--wait N]      (gpu-check.bat runs this)

selftest.py uses sample(..., host_only=True) for the host's own numbers.
"""
import ctypes
import datetime
import os
import re
import subprocess
import sys
import time
from collections import defaultdict

IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
HERE = os.path.dirname(os.path.abspath(__file__))
NVIDIA, AMD, INTEL = 0x10DE, 0x1002, 0x8086

# What the usual names in the list are.
KNOWN = {
    "dwm": "Windows, drawing every screen (the iPad's too)",
    "wudfhost": "Windows driver host (the iPad's virtual screen driver runs here)",
    "csrss": "Windows", "system": "Windows", "explorer": "Windows taskbar and desktop",
    "searchhost": "Windows search", "startmenuexperiencehost": "Windows start menu",
    "textinputhost": "Windows keyboard", "shellexperiencehost": "Windows",
    "nvcontainer": "NVIDIA app", "nvidia overlay": "NVIDIA overlay (Alt+Z, Instant Replay)",
    "nvdisplay.container": "NVIDIA driver", "obs64": "OBS", "discord": "Discord",
    "chrome": "Chrome", "msedge": "Edge", "msedgewebview2": "Edge WebView (used by many apps)",
    "comet": "Comet browser", "spotify": "Spotify", "steam": "Steam", "steamwebhelper": "Steam",
    "awcc": "Alienware Command Center", "rainbowsix": "Rainbow Six Siege",
    "rtss": "RivaTuner", "msiafterburner": "MSI Afterburner",
}

ENGINE_NAMES = {"3d": "3D", "videoencode": "video encode", "videodecode": "video decode",
                "copy": "copy", "compute": "compute", "compute_0": "compute", "compute_1": "compute",
                "videoprocessing": "video processing", "security": "security", "graphics_1": "3D"}


def adapters():
    """{luid: (name, vendor id, dedicated MB)} for every graphics card, from DXGI."""
    out = {}
    if not IS_WINDOWS:
        return out

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]

    class DESC1(ctypes.Structure):
        _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint),
                    ("DeviceId", ctypes.c_uint), ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint),
                    ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                    ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID), ("Flags", ctypes.c_uint)]

    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                    ("d", ctypes.c_ubyte * 8)]

    def method(obj, index, *argtypes):
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])

    iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
    try:
        factory = ctypes.c_void_p()
        if ctypes.WinDLL("dxgi.dll").CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0:
            return out
        i = 0
        while i < 16:
            ad = ctypes.c_void_p()
            # IDXGIFactory1::EnumAdapters1 is vtable slot 12, IDXGIAdapter1::GetDesc1 slot 10.
            if method(factory, 12, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(factory, i, ctypes.byref(ad)) != 0:
                break
            d = DESC1()
            if method(ad, 10, ctypes.POINTER(DESC1))(ad, ctypes.byref(d)) == 0:
                luid = f"0x{d.AdapterLuid.HighPart & 0xFFFFFFFF:08x}_0x{d.AdapterLuid.LowPart:08x}"
                out[luid] = (d.Description.strip(), d.VendorId, d.DedicatedVideoMemory >> 20)
            method(ad, 2)(ad)                      # Release
            i += 1
        method(factory, 2)(factory)
    except Exception:
        pass
    return out


_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$hostIds = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server.py*' } | ForEach-Object { $_.ProcessId })
'H' + "`t" + ($hostIds -join ',')
Get-Process | ForEach-Object { 'P' + "`t" + $_.Id + "`t" + $_.ProcessName }
if (HOSTONLY) {
  if ($hostIds.Count -eq 0) { exit }
  $c = foreach ($p in $hostIds) { "\GPU Engine(pid_$($p)_*)\Utilization Percentage";
       "\GPU Process Memory(pid_$($p)_*)\Dedicated Usage"; "\GPU Process Memory(pid_$($p)_*)\Shared Usage" }
} else {
  $c = '\GPU Engine(*)\Utilization Percentage', '\GPU Process Memory(*)\Dedicated Usage',
       '\GPU Process Memory(*)\Shared Usage', '\GPU Adapter Memory(*)\Dedicated Usage',
       '\GPU Adapter Memory(*)\Shared Usage'
}
$i = 0
Get-Counter -Counter $c -SampleInterval 1 -MaxSamples SAMPLES | ForEach-Object {
  foreach ($x in $_.CounterSamples) { if ($x.CookedValue -gt 0) { 'S' + "`t" + $i + "`t" + $x.Path + "`t" + [string][double]$x.CookedValue } }
  $i++
}
"""

_ENGINE = re.compile(r"gpu engine\(pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_\d+_engtype_([^)]*)\)")
_PMEM = re.compile(r"gpu process memory\(pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+\)\\(dedicated|shared) usage")
_AMEM = re.compile(r"gpu adapter memory\(luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+\)\\(dedicated|shared) usage")


def sample(seconds, host_only=False):
    """Measure for `seconds` (one reading a second). Returns a dict (see report())."""
    n = max(2, int(seconds))
    names, host = {}, set()
    eng = defaultdict(lambda: defaultdict(float))     # sample -> (pid, luid, engine type) -> %
    pmem = defaultdict(lambda: defaultdict(float))    # (pid, luid) -> kind -> peak MB
    amem = defaultdict(lambda: defaultdict(float))    # luid -> kind -> peak MB
    taken = 0
    if IS_WINDOWS:
        script = _PS.replace("HOSTONLY", "$true" if host_only else "$false").replace("SAMPLES", str(n))
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=n * 4 + 60,
                                 creationflags=_NO_WINDOW).stdout
        except Exception:
            out = ""
        for line in out.splitlines():
            f = line.rstrip("\r").split("\t")
            if f[0] == "H" and len(f) > 1:
                host = {int(p) for p in f[1].split(",") if p.strip().isdigit()}
            elif f[0] == "P" and len(f) > 2 and f[1].isdigit():
                names[int(f[1])] = f[2]
            elif f[0] == "S" and len(f) > 3:
                try:
                    i, path, v = int(f[1]), f[2].lower(), float(f[3])
                except ValueError:
                    continue
                taken = max(taken, i + 1)
                m = _ENGINE.search(path)
                if m:
                    eng[i][(int(m.group(1)), m.group(2), m.group(3))] += v
                    continue
                m = _PMEM.search(path)
                if m:
                    k = pmem[(int(m.group(1)), m.group(2))]
                    k[m.group(3)] = max(k[m.group(3)], v / 2**20)
                    continue
                m = _AMEM.search(path)
                if m:
                    k = amem[m.group(1)]
                    k[m.group(2)] = max(k[m.group(2)], v / 2**20)
    taken = max(taken, 1)

    # Per program per card: its busiest engine each second (what Task Manager's
    # GPU column shows), averaged and at its peak.
    procs = defaultdict(lambda: {"busy": [0.0] * taken, "peak_engine": None})
    gpus = defaultdict(lambda: defaultdict(lambda: [0.0] * taken))   # luid -> engine -> per-second %
    for i, d in eng.items():
        per = defaultdict(lambda: defaultdict(float))
        for (pid, luid, et), v in d.items():
            per[(pid, luid)][et] += v
            gpus[luid][et][i] = min(100.0, gpus[luid][et][i] + v)
        for key, ets in per.items():
            et, v = max(ets.items(), key=lambda kv: kv[1])
            p = procs[key]
            if v > max(p["busy"]):
                p["peak_engine"] = et
            p["busy"][i] = min(100.0, v)
    rows = []
    for key in set(procs) | set(pmem):
        pid, luid = key
        busy = procs[key]["busy"] if key in procs else [0.0] * taken
        mem = pmem.get(key, {})
        rows.append({"pid": pid, "name": names.get(pid, f"pid {pid}"), "host": pid in host, "gpu": luid,
                     "avg": round(sum(busy) / taken, 1), "peak": round(max(busy), 1),
                     "engine": procs[key]["peak_engine"] if key in procs else None,
                     "dedicated_mb": round(mem.get("dedicated", 0.0)), "shared_mb": round(mem.get("shared", 0.0))})
    return {"seconds": n, "samples": taken, "taken_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "adapters": {k: list(v) for k, v in adapters().items()}, "host_pids": sorted(host), "procs": rows,
            "gpus": {luid: {et: [round(sum(v) / taken, 1), round(max(v), 1)] for et, v in ets.items()}
                     for luid, ets in gpus.items()},
            "gpu_memory": {luid: {k: round(v) for k, v in d.items()} for luid, d in amem.items()}}


def main_gpu(d):
    """The luid of the gaming card: NVIDIA (or AMD) first, else the one with most memory."""
    ads = d.get("adapters") or {}
    if ads:
        for vendor in (NVIDIA, AMD):
            for luid, (_, v, _) in ads.items():
                if v == vendor:
                    return luid
        return max(ads, key=lambda k: ads[k][2])
    used = defaultdict(float)
    for r in d["procs"]:
        used[r["gpu"]] += r["dedicated_mb"]
    return max(used, key=used.get) if used else None


def gpu_name(d, luid):
    a = (d.get("adapters") or {}).get(luid)
    return a[0] if a else f"graphics card {luid}"


def host_summary(d):
    """The host's own numbers: on the gaming card (busiest engine avg/peak,
    which engine, dedicated MB) and shared memory on any card."""
    main = main_gpu(d)
    rows = [r for r in d["procs"] if r["host"]]
    on_main = [r for r in rows if r["gpu"] == main]
    # Several host processes (the venv launcher and Python) add up.
    avg = round(sum(r["avg"] for r in on_main), 1)
    peak = round(max((r["peak"] for r in on_main), default=0.0), 1)
    engine = max(on_main, key=lambda r: r["peak"])["engine"] if on_main else None
    return {"gpu": gpu_name(d, main) if main else None, "avg": avg, "peak": peak,
            "engine": ENGINE_NAMES.get(engine, engine),
            "dedicated_mb": sum(r["dedicated_mb"] for r in on_main),
            "shared_mb": sum(r["shared_mb"] for r in rows),
            "other_gpu_dedicated_mb": sum(r["dedicated_mb"] for r in rows if r["gpu"] != main)}


def _label(r):
    if r["host"]:
        return f"{r['name']} = iPad Display host"
    what = KNOWN.get(r["name"].lower())
    return f"{r['name']}: {what}" if what else r["name"]


def report(d):
    L = [f"GPU check  {d['taken_at']}  ({d['samples']} readings, one a second)", ""]
    if not d["procs"]:
        L.append("No GPU readings came back (this only works on Windows 10/11).")
        return "\n".join(L)
    main = main_gpu(d)
    order = sorted({r["gpu"] for r in d["procs"]} | set(d["gpus"]), key=lambda g: g != main)
    for luid in order:
        name = gpu_name(d, luid)
        ets = d["gpus"].get(luid, {})
        mem = d["gpu_memory"].get(luid, {})
        L.append(f"== {name}" + ("  (the gaming card)" if luid == main else ""))
        if ets:
            busy = sorted(ets.items(), key=lambda kv: -kv[1][1])
            L.append("   Whole card, by engine (average / highest second):")
            for et, (a, p) in busy:
                if p >= 0.5:
                    L.append(f"     {ENGINE_NAMES.get(et, et):17s} {a:5.1f}% / {p:5.1f}%")
        if mem:
            L.append(f"   Memory in use (highest): {mem.get('dedicated', 0)} MB on the card, "
                     f"{mem.get('shared', 0)} MB shared from RAM")
        rows = [r for r in d["procs"] if r["gpu"] == luid and (r["peak"] >= 0.5 or r["dedicated_mb"] >= 16
                                                                or r["host"])]
        rows.sort(key=lambda r: (-r["host"], -r["peak"], -r["dedicated_mb"]))
        if rows:
            L.append("   Programs (GPU % average / highest second, busiest engine | memory on the card + shared):")
            for r in rows[:14]:
                eng = ENGINE_NAMES.get(r["engine"], r["engine"]) or "-"
                L.append(f"     {_label(r)[:72]:72s} {r['avg']:5.1f}% / {r['peak']:5.1f}%  {eng:14s} | "
                         f"{r['dedicated_mb']:5d} MB + {r['shared_mb']} MB shared")
        L.append("")
    h = host_summary(d)
    if d["host_pids"]:
        L.append(f"iPad Display host on {h['gpu']}: GPU {h['avg']}% average, {h['peak']}% at most "
                 f"({h['engine'] or 'no engine busy'}), {h['dedicated_mb']} MB of its memory, "
                 f"plus {h['shared_mb']} MB shared from RAM.")
    else:
        L.append("The iPad Display host wasn't running during this check.")
    L += ["",
          "How to read it: Task Manager's GPU % for a program is its busiest engine. Video encode is",
          "its own part of the chip (the same one NVIDIA ShadowPlay uses), so it takes almost",
          "nothing from a game; 3D is what games use."]
    return "\n".join(L)


def main():
    args = sys.argv[1:]
    wait = 0
    if "--wait" in args:
        k = args.index("--wait")
        wait = int(args[k + 1]) if k + 1 < len(args) else 10
        del args[k:k + 2]
    seconds = int(args[0]) if args else 60
    if wait:
        print(f"Starting in {wait} seconds...", flush=True)
        time.sleep(wait)
    print(f"Measuring for {seconds} seconds. Use the PC as you normally would.", flush=True)
    d = sample(seconds)
    text = report(d)
    print()
    print(text)
    out = os.path.join(HERE, "gpu-check")
    os.makedirs(out, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(out, f"gpu-check-{stamp}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(f"\nSaved to {path}")
    if IS_WINDOWS:
        try:
            import winsound
            winsound.Beep(880, 300)
        except Exception:
            pass


if __name__ == "__main__":
    main()
