"""
USB-C bridge between this PC and the iPad Display app on the iPad.

Apple's USB service on Windows (installed with iTunes or the Apple Devices
app) runs "usbmuxd" on 127.0.0.1:27015. It lets the PC open TCP connections to
a port on a USB-connected, trusted iPad. The iPad app listens on port 8766 and
hands each of these connections to its built-in viewer.

This module keeps a few spare tunnels open to the iPad. When the viewer uses
one, the bridge connects it to the local web server (127.0.0.1:8765), so the
viewer gets exactly the same page, stream and controls as Safari would, just
over the cable instead of Wi-Fi.
"""
import asyncio
import plistlib
import struct
import sys

USBMUXD_HOST, USBMUXD_PORT = "127.0.0.1", 27015
IPAD_PORT = 8766          # the iPad app listens here (loopback, via USB)
SPARE_TUNNELS = 4         # idle tunnels kept ready for the viewer
TUNNEL_MAX_IDLE = 30      # seconds: recycle spare tunnels so none go stale
POLL_SECONDS = 1.5

_tag = 0


def _log(*a):
    print("[usb]", *a, flush=True)


async def _request(reader, writer, payload):
    """Send one usbmux plist request and read its reply."""
    global _tag
    _tag += 1
    payload = {"ClientVersionString": "ipad-display", "ProgName": "ipad-display",
               "kLibUSBMuxVersion": 3, **payload}
    body = plistlib.dumps(payload, fmt=plistlib.FMT_XML)
    # header: total length, version 1 (plist), message 8 (plist), tag
    writer.write(struct.pack("<IIII", 16 + len(body), 1, 8, _tag) + body)
    await writer.drain()
    head = await reader.readexactly(16)
    length = struct.unpack("<I", head[:4])[0]
    return plistlib.loads(await reader.readexactly(length - 16))


async def _open_usbmux():
    return await asyncio.open_connection(USBMUXD_HOST, USBMUXD_PORT)


async def list_usb_devices():
    reader, writer = await _open_usbmux()
    try:
        reply = await _request(reader, writer, {"MessageType": "ListDevices"})
    finally:
        writer.close()
    out = []
    for d in reply.get("DeviceList", []):
        props = d.get("Properties", {})
        if props.get("ConnectionType") == "USB":
            out.append((d.get("DeviceID", props.get("DeviceID")), props.get("SerialNumber", "?")))
    return out


async def _connect_device(device_id, port):
    """Open a raw TCP tunnel to `port` on the iPad. Returns (reader, writer) or None."""
    reader, writer = await _open_usbmux()
    swapped = ((port & 0xFF) << 8) | (port >> 8)   # usbmuxd wants network byte order
    reply = await _request(reader, writer, {"MessageType": "Connect",
                                            "DeviceID": device_id, "PortNumber": swapped})
    if reply.get("Number", -1) != 0:
        writer.close()
        return None
    return reader, writer


async def _pipe(reader, writer):
    try:
        while True:
            data = await reader.read(1 << 20)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


class Bridge:
    def __init__(self, web_port, on_change=None):
        self.web_port = web_port
        self.on_change = on_change    # called with True/False when an iPad is plugged in / out
        self.idle = {}            # device_id -> number of idle tunnels
        self.devices = {}         # device_id -> serial
        self.warned = False
        self.app_missing = set()
        # asyncio only keeps weak references to tasks: without this set, Python
        # can garbage-collect a tunnel mid-stream ("Task was destroyed but it is
        # pending!"), which drops the iPad's connection.
        self.tasks = set()

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _tunnel(self, device_id):
        """One spare tunnel: wait for the iPad viewer to use it, then pipe it to the web server."""
        try:
            conn = await _connect_device(device_id, IPAD_PORT)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            conn = None
        if conn is None:
            if device_id not in self.app_missing:
                self.app_missing.add(device_id)
                _log("iPad found, waiting for the iPad Display app to be opened on it...")
            await asyncio.sleep(1.0)
            self.idle[device_id] -= 1
            return
        if device_id in self.app_missing:
            self.app_missing.discard(device_id)
            _log("Connected to the iPad Display app over USB.")
        ipad_r, ipad_w = conn
        try:
            # Blocks until the viewer uses this tunnel. Spare tunnels are
            # recycled every TUNNEL_MAX_IDLE seconds so a silently dead one is
            # never handed to the viewer.
            first = await asyncio.wait_for(ipad_r.read(1 << 20), TUNNEL_MAX_IDLE)
        except (ConnectionError, OSError, asyncio.TimeoutError):
            first = b""
        self.idle[device_id] -= 1                    # in use (or dead): let the pool refill
        if not first:
            ipad_w.close()
            return
        try:
            web_r, web_w = await asyncio.open_connection("127.0.0.1", self.web_port)
        except OSError:
            ipad_w.close()
            return
        for s in (ipad_w, web_w):
            sock = s.get_extra_info("socket")
            if sock is not None:
                try:
                    import socket as _s
                    sock.setsockopt(_s.IPPROTO_TCP, _s.TCP_NODELAY, 1)
                except OSError:
                    pass
        web_w.write(first)
        await web_w.drain()
        pipes = asyncio.gather(_pipe(ipad_r, web_w), _pipe(web_r, ipad_w))
        self.tasks.add(pipes)
        try:
            await pipes
        finally:
            self.tasks.discard(pipes)

    async def run(self):
        while True:
            try:
                devices = await list_usb_devices()
                self.warned = False
            except (ConnectionError, OSError, asyncio.IncompleteReadError):
                if not self.warned:
                    self.warned = True
                    _log("Apple's USB service isn't running, so USB-C mode is off. "
                         "Install iTunes (or Apple Devices) to use the cable. Wi-Fi still works.")
                await asyncio.sleep(5)
                continue
            current = dict(devices)
            for dev_id, serial in current.items():
                if dev_id not in self.devices:
                    _log(f"iPad plugged in over USB (serial {serial}).")
                self.idle.setdefault(dev_id, 0)
                while self.idle[dev_id] < SPARE_TUNNELS:
                    self.idle[dev_id] += 1
                    self._spawn(self._tunnel(dev_id))
            for dev_id in list(self.devices):
                if dev_id not in current:
                    _log("iPad unplugged.")
                    self.idle.pop(dev_id, None)
                    self.app_missing.discard(dev_id)
            if self.on_change and bool(current) != bool(self.devices):
                try:
                    self.on_change(bool(current))
                except Exception as e:
                    _log("plug handler failed:", e)
            self.devices = current
            await asyncio.sleep(POLL_SECONDS)


def start(app, web_port, on_change=None):
    """Hook the bridge into the aiohttp app's lifecycle. on_change(plugged) is
    called when the first iPad is plugged in (True) or the last one unplugged (False)."""
    async def on_startup(app):
        app["usb_bridge"] = asyncio.create_task(Bridge(web_port, on_change).run())

    async def on_cleanup(app):
        t = app.get("usb_bridge")
        if t:
            t.cancel()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)


if __name__ == "__main__":
    # Standalone: python usb_bridge.py [web_port]
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    asyncio.run(Bridge(port).run())
