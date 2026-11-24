"""Minecraft server control over RCON: who's online, TPS, and console
commands (say, whitelist, save-all, stop, anything else).

In the server's server.properties set enable-rcon=true, rcon.password=...
and (optionally) rcon.port=25575, restart it, then put the same password in
Settings on the iPad. TPS comes from Paper/Purpur/Spigot's "tps" command, or
Forge/NeoForge's "forge tps"/"neoforge tps"; plain vanilla and Fabric don't
report it. The server's console log is read from its folder when set."""
import os
import re
import socket
import struct
import threading
import time

import hostsettings

hostsettings.register({"mc_host": "127.0.0.1", "mc_port": 25575, "mc_password": "", "mc_folder": ""})
hostsettings.SECRET.add("mc_password")
COLOR = re.compile(r"§.")
_lock = threading.Lock()
_conn = {"sock": None, "key": None}
_tps_cmd = {"cmd": None}
_cache = {"at": 0.0, "data": None}


class RconError(RuntimeError):
    pass


def _packet(rid, kind, body):
    b = body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<iii", len(b) + 8, rid, kind) + b


def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        part = s.recv(n - len(buf))
        if not part:
            raise RconError("the server closed the connection")
        buf += part
    return buf


def _read(s):
    (size,) = struct.unpack("<i", _recv_exact(s, 4))
    if not 10 <= size <= 1 << 20:
        raise RconError("that isn't a Minecraft RCON port")
    data = _recv_exact(s, size)
    rid, kind = struct.unpack("<ii", data[:8])
    return rid, kind, data[8:-2].decode("utf-8", "replace")


def _connect():
    host, port, pw = hostsettings.get("mc_host") or "127.0.0.1", int(hostsettings.get("mc_port") or 25575), \
        hostsettings.get("mc_password")
    if not pw:
        raise RconError("set the RCON password in Settings (the same as rcon.password in server.properties)")
    key = (host, port, pw)
    if _conn["sock"] and _conn["key"] == key:
        return _conn["sock"]
    _close()
    try:
        s = socket.create_connection((host, port), timeout=3)
    except OSError:
        raise RconError("the server isn't running, or RCON is off (enable-rcon=true in server.properties)") from None
    s.settimeout(5)
    s.sendall(_packet(1, 3, pw))
    rid, _, _ = _read(s)
    if rid == -1:
        s.close()
        raise RconError("the server refused the RCON password")
    _conn.update(sock=s, key=key)
    return s


def _close():
    if _conn["sock"]:
        try:
            _conn["sock"].close()
        except OSError:
            pass
    _conn.update(sock=None, key=None)


def command(cmd):
    """Run a console command; returns its answer with colour codes removed."""
    cmd = str(cmd).strip().lstrip("/")
    if not cmd:
        raise ValueError("empty command")
    with _lock:
        for attempt in (1, 2):                     # once more on a fresh connection if the old one died
            try:
                s = _connect()
                s.sendall(_packet(2, 2, cmd))
                # Long answers come in several packets: an empty command after it marks the end.
                s.sendall(_packet(3, 2, ""))
                out = []
                while True:
                    rid, _, body = _read(s)
                    if rid == 3:
                        break
                    out.append(body)
                return COLOR.sub("", "".join(out)).strip()
            except (OSError, RconError) as e:
                _close()
                if attempt == 2 or (isinstance(e, RconError) and "password" in str(e)):
                    raise RconError(str(e) if isinstance(e, RconError) else f"lost the server ({e})") from None


def _players(text):
    # "There are 2 of a max of 20 players online: Jai, Ash" (vanilla); Paper also adds groups.
    m = re.search(r"(\d+)\s*(?:of a max of|/|out of maximum|of)\s*(\d+)", text)
    names = []
    if ":" in text:
        for part in text.split(":", 1)[1].replace("\n", ",").split(","):
            part = part.strip()
            if part and re.fullmatch(r"[\w.]{1,32}", part):
                names.append(part)
    return (int(m.group(1)) if m else len(names)), (int(m.group(2)) if m else None), names


def _tps(text):
    # Paper: "TPS from last 1m, 5m, 15m: 20.0, 19.98, 20.0"  Forge: "Overall: Mean tick time: 4.2 ms. Mean TPS: 20.000"
    m = re.search(r"Overall.*?Mean TPS:\s*([\d.]+)", text, re.S) or re.search(r"TPS[^:]*:\s*\*?([\d.]+)", text)
    return float(m.group(1)) if m else None


def state():
    """{online, players, max, names, tps} (asked at most every 3 s), or {online: False, error}."""
    if time.time() - _cache["at"] < 3 and _cache["data"]:
        return _cache["data"]
    try:
        n, mx, names = _players(command("list"))
        known = _tps_cmd["cmd"]
        tries = [] if known is False else [known] if known else ["tps", "neoforge tps", "forge tps"]
        tps = None
        for c in tries:
            t = _tps(command(c))
            if t is not None:
                tps, _tps_cmd["cmd"] = t, c
                break
        else:
            if tries and not known:
                _tps_cmd["cmd"] = False             # this server can't tell: don't ask again
        d = {"online": True, "players": n, "max": mx, "names": names[:40], "tps": tps}
    except (RconError, ValueError) as e:
        d = {"online": False, "error": str(e)}
    _cache.update(at=time.time(), data=d)
    return d


def reset():
    with _lock:
        _close()
    _tps_cmd["cmd"] = None
    _cache["data"] = None


def log_path():
    f = hostsettings.get("mc_folder").strip()
    return os.path.join(f, "logs", "latest.log") if f else ""
