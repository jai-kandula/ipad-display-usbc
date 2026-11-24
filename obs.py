"""OBS Studio from the iPad: start and stop the stream or recording, save a
replay, switch scenes, mute a mic, and see what OBS is doing (live, recording,
which scene, dropped frames) on a tile.

It talks to the WebSocket server built into OBS 28 and newer (Tools >
WebSocket Server Settings > Enable WebSocket server). Nothing to install. The
address and password are saved in Settings on the iPad.

One connection stays open while OBS runs and OBS pushes every change to it,
so nothing is polled; only while you are live and a tile is on screen does
it ask OBS for the stream's numbers every 2 seconds. When OBS isn't running
it tries again now and then (at most every 30 seconds)."""
import asyncio
import base64
import hashlib
import itertools
import threading
import time

import hostsettings

hostsettings.register({"obs_url": "ws://127.0.0.1:4455", "obs_password": ""})
hostsettings.SECRET.add("obs_password")

# What a button can ask for: {"obs": {"do": "stream"}} or {"obs": {"do": "scene", "scene": "Game"}}
ACTIONS = {
    "stream": ("ToggleStream", None), "stream_start": ("StartStream", None), "stream_stop": ("StopStream", None),
    "record": ("ToggleRecord", None), "record_start": ("StartRecord", None), "record_stop": ("StopRecord", None),
    "pause": ("ToggleRecordPause", None),
    "replay": ("SaveReplayBuffer", None), "replay_buffer": ("ToggleReplayBuffer", None),
    "virtualcam": ("ToggleVirtualCam", None),
    "scene": ("SetCurrentProgramScene", "scene"), "mute": ("ToggleInputMute", "input"),
}
EVENTS = 1 | 4 | 8 | 64              # General (OBS closing), Scenes, Inputs, Outputs
STATS_EVERY = 2.0
WANTED_FOR = 15.0                    # stream numbers are asked for only this long after a tile asks


def check(a):
    """Raise ValueError if a button's {"obs": ...} can't work."""
    if not isinstance(a, dict) or a.get("do") not in ACTIONS:
        raise ValueError("an OBS button needs one of: " + ", ".join(ACTIONS))
    need = ACTIONS[a["do"]][1]
    if need and not str(a.get(need) or "").strip():
        raise ValueError(f"an OBS {a['do']} button needs the {need}'s name")


def _blank():
    return {"connected": False, "error": "", "streaming": False, "recording": False, "paused": False,
            "replay": False, "virtualcam": False, "scene": "", "scenes": [], "inputs": {},
            "stream_since": None, "rec_since": None, "rec_paused_for": None,
            "dropped": None, "kbps": None, "congestion": None, "replay_saved": None}


def _why(e):
    s = str(e) or type(e).__name__
    if "Cannot connect" in s or "Connect call failed" in s or isinstance(e, (ConnectionRefusedError, asyncio.TimeoutError)):
        return "OBS isn't running, or its WebSocket server is off (OBS: Tools > WebSocket Server Settings)"
    return s


class _Client:
    def __init__(self):
        self.st = _blank()
        self.loop = None
        self.ws = None
        self.ready = None
        self.kick = None
        self.pending = {}
        self.ids = itertools.count(1)
        self.started = False
        self.lock = threading.Lock()
        self.wanted = 0.0
        self._bytes = None

    # ------------------------------------------------------------ thread side
    def ensure(self):
        self.wanted = time.time()
        with self.lock:
            if self.started:
                return
            self.started = True
        up = threading.Event()
        threading.Thread(target=self._thread, args=(up,), daemon=True, name="obs").start()
        up.wait(2)

    def _thread(self, up):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready, self.kick = asyncio.Event(), asyncio.Event()
        up.set()
        self.loop.run_until_complete(self._main())

    async def _main(self):
        import aiohttp
        wait = 2.0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=3)) as http:
            while True:
                try:
                    await self._session(http)
                    wait = 2.0                             # was connected: OBS closed, try again soon
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.st.update(_blank(), error=_why(e))
                self.ready.clear()
                self.ws = None
                self.st["connected"] = False
                for f in self.pending.values():
                    if not f.done():
                        f.set_exception(RuntimeError("OBS closed the connection"))
                self.pending.clear()
                try:
                    await asyncio.wait_for(self.kick.wait(), wait)
                except asyncio.TimeoutError:
                    pass
                self.kick.clear()
                wait = min(30.0, wait * 2)

    async def _session(self, http):
        url = hostsettings.get("obs_url").strip() or "ws://127.0.0.1:4455"
        if not url.startswith(("ws://", "wss://")):
            url = "ws://" + url
        async with http.ws_connect(url, heartbeat=20, max_msg_size=8 << 20, autoping=True) as ws:
            hello = await ws.receive_json(timeout=5)
            if hello.get("op") != 0:
                raise RuntimeError("that address isn't OBS's WebSocket server")
            d = hello.get("d", {})
            ident = {"rpcVersion": 1, "eventSubscriptions": EVENTS}
            auth = d.get("authentication")
            if auth:
                pw = hostsettings.get("obs_password")
                if not pw:
                    raise RuntimeError("OBS asks for a password: copy it from OBS (Tools > WebSocket Server Settings > Show Connect Info)")
                secret = base64.b64encode(hashlib.sha256((pw + auth["salt"]).encode()).digest())
                ident["authentication"] = base64.b64encode(hashlib.sha256(secret + auth["challenge"].encode()).digest()).decode()
            await ws.send_json({"op": 1, "d": ident})
            msg = await ws.receive(timeout=5)
            if msg.type != 1:                               # WSMsgType.TEXT
                if ws.close_code == 4009:
                    raise RuntimeError("OBS refused the password")
                raise RuntimeError(f"OBS closed the connection ({ws.close_code})")
            if msg.json().get("op") != 2:
                raise RuntimeError("OBS didn't accept the connection")
            self.ws = ws
            self.st.update(_blank(), connected=True)
            self.ready.set()
            print("[obs] connected to", url, flush=True)
            tasks = [asyncio.ensure_future(self._refresh()), asyncio.ensure_future(self._stats())]
            try:
                async for m in ws:
                    if m.type != 1:
                        break
                    self._on(m.json())
            finally:
                for t in tasks:
                    t.cancel()
            raise RuntimeError("OBS was closed")

    def _on(self, j):
        op, d = j.get("op"), j.get("d", {})
        if op == 7:
            f = self.pending.pop(d.get("requestId"), None)
            if f and not f.done():
                s = d.get("requestStatus", {})
                if s.get("result"):
                    f.set_result(d.get("responseData") or {})
                else:
                    f.set_exception(RuntimeError(_friendly(d.get("requestType"), s)))
        elif op == 5:
            self._event(d.get("eventType"), d.get("eventData") or {})

    def _event(self, kind, e):
        st, now = self.st, time.time()
        if kind == "StreamStateChanged":
            st["streaming"] = bool(e.get("outputActive"))
            if e.get("outputState") == "OBS_WEBSOCKET_OUTPUT_STARTED":
                st["stream_since"], self._bytes = now, None
            elif not st["streaming"]:
                st.update(stream_since=None, dropped=None, kbps=None, congestion=None)
        elif kind == "RecordStateChanged":
            s = e.get("outputState")
            st["recording"] = bool(e.get("outputActive"))
            if s == "OBS_WEBSOCKET_OUTPUT_STARTED":
                st.update(rec_since=now, paused=False, rec_paused_for=None)
            elif s == "OBS_WEBSOCKET_OUTPUT_PAUSED":
                st.update(paused=True, rec_paused_for=now - (st["rec_since"] or now))
            elif s == "OBS_WEBSOCKET_OUTPUT_RESUMED":
                st.update(paused=False, rec_since=now - (st["rec_paused_for"] or 0), rec_paused_for=None)
            elif not st["recording"]:
                st.update(rec_since=None, paused=False, rec_paused_for=None)
        elif kind == "ReplayBufferStateChanged":
            st["replay"] = bool(e.get("outputActive"))
        elif kind == "ReplayBufferSaved":
            st["replay_saved"] = now
        elif kind == "VirtualcamStateChanged":
            st["virtualcam"] = bool(e.get("outputActive"))
        elif kind == "CurrentProgramSceneChanged":
            st["scene"] = e.get("sceneName", "")
        elif kind == "SceneListChanged":
            st["scenes"] = _scene_names(e.get("scenes"))
        elif kind == "SceneNameChanged":
            st["scenes"] = [e.get("sceneName") if s == e.get("oldSceneName") else s for s in st["scenes"]]
            if st["scene"] == e.get("oldSceneName"):
                st["scene"] = e.get("sceneName", "")
        elif kind == "InputMuteStateChanged":
            st["inputs"][e.get("inputName", "")] = bool(e.get("inputMuted"))
        elif kind in ("InputCreated", "InputRemoved", "InputNameChanged"):
            asyncio.ensure_future(self._inputs())
        elif kind == "ExitStarted":
            st["error"] = "OBS is closing"

    async def _req(self, kind, data=None, timeout=4.0):
        if not self.ready.is_set():
            await asyncio.wait_for(self.ready.wait(), 3)
        rid = str(next(self.ids))
        f = self.loop.create_future()
        self.pending[rid] = f
        await self.ws.send_json({"op": 6, "d": {"requestType": kind, "requestId": rid, "requestData": data or {}}})
        try:
            return await asyncio.wait_for(f, timeout)
        finally:
            self.pending.pop(rid, None)

    async def _try(self, kind, data=None):
        try:
            return await self._req(kind, data)
        except Exception:
            return None

    async def _refresh(self):
        st, now = self.st, time.time()
        s = await self._try("GetStreamStatus")
        if s:
            st["streaming"] = bool(s.get("outputActive"))
            st["stream_since"] = now - s.get("outputDuration", 0) / 1000 if st["streaming"] else None
        r = await self._try("GetRecordStatus")
        if r:
            st["recording"], st["paused"] = bool(r.get("outputActive")), bool(r.get("outputPaused"))
            if st["recording"]:
                el = r.get("outputDuration", 0) / 1000
                st["rec_since"] = now - el
                st["rec_paused_for"] = el if st["paused"] else None
        rb = await self._try("GetReplayBufferStatus")         # fails when the replay buffer isn't set up
        st["replay"] = bool(rb and rb.get("outputActive"))
        vc = await self._try("GetVirtualCamStatus")
        st["virtualcam"] = bool(vc and vc.get("outputActive"))
        sc = await self._try("GetSceneList")
        if sc:
            st["scenes"] = _scene_names(sc.get("scenes"))
            st["scene"] = sc.get("currentProgramSceneName") or ""
        await self._inputs()

    async def _inputs(self):
        il = await self._try("GetInputList")
        if not il:
            return
        out = {}
        for i in (il.get("inputs") or [])[:60]:
            name = i.get("inputName")
            m = await self._try("GetInputMute", {"inputName": name})    # only sources with sound answer
            if m is not None:
                out[name] = bool(m.get("inputMuted"))
        self.st["inputs"] = out

    async def _stats(self):
        """The stream's dropped frames and bitrate, while live and a tile is looking."""
        while True:
            await asyncio.sleep(STATS_EVERY)
            if not self.st["streaming"] or time.time() - self.wanted > WANTED_FOR:
                self._bytes = None
                continue
            s = await self._try("GetStreamStatus")
            if not s or not s.get("outputActive"):
                continue
            now, total = time.time(), s.get("outputTotalFrames") or 0
            self.st["dropped"] = round(100 * (s.get("outputSkippedFrames") or 0) / total, 2) if total else 0.0
            self.st["congestion"] = s.get("outputCongestion")
            b = s.get("outputBytes")
            if b is not None and self._bytes:
                pb, pt = self._bytes
                if now > pt and b >= pb:
                    self.st["kbps"] = round((b - pb) * 8 / 1000 / (now - pt))
            self._bytes = (b, now) if b is not None else None
            self.st["stream_since"] = now - (s.get("outputDuration") or 0) / 1000

    # ------------------------------------------------------------ callers (any thread)
    def call(self, kind, data=None, timeout=5.0):
        self.ensure()
        if not self.st["connected"]:
            self.loop.call_soon_threadsafe(self.kick.set)     # OBS may have just opened: try now, not in 30 s
        fut = asyncio.run_coroutine_threadsafe(self._req(kind, data), self.loop)
        try:
            return fut.result(timeout)
        except (asyncio.TimeoutError, TimeoutError):
            fut.cancel()
            raise RuntimeError(self.st["error"] or "OBS didn't answer") from None
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(self.st["error"] or _why(e)) from None

    def reconnect(self):
        if self.loop and self.kick:
            def go():
                self.ready.clear()
                if self.ws is not None:
                    asyncio.ensure_future(self.ws.close())
                self.kick.set()
            self.loop.call_soon_threadsafe(go)

    def state(self):
        self.ensure()
        st, now = dict(self.st), time.time()
        st["inputs"] = dict(st["inputs"])
        st["scenes"] = list(st["scenes"])
        st["stream_time"] = round(now - st["stream_since"]) if st["stream_since"] else None
        st["rec_time"] = (round(st["rec_paused_for"]) if st["paused"] and st["rec_paused_for"] is not None
                          else round(now - st["rec_since"]) if st["rec_since"] else None)
        st["replay_saved"] = round(now - st["replay_saved"]) if st["replay_saved"] else None
        for k in ("stream_since", "rec_since", "rec_paused_for"):
            st.pop(k)
        return st


def _scene_names(scenes):
    # OBS lists scenes bottom-up; the iPad shows them in the order OBS's own list does.
    return [s.get("sceneName", "") for s in sorted(scenes or [], key=lambda s: -s.get("sceneIndex", 0))]


def _friendly(kind, status):
    code, comment = status.get("code"), status.get("comment") or ""
    if kind == "SaveReplayBuffer" and code in (501, 604):
        return "the replay buffer is off: start it in OBS (Settings > Output > Replay Buffer), or with a Replay buffer button"
    if kind == "SetCurrentProgramScene" and code == 600:
        return "OBS has no scene with that name"
    if kind == "ToggleInputMute" and code in (600, 604):
        return "OBS has no audio source with that name"
    if kind == "ToggleRecordPause" and code == 501:
        return "OBS isn't recording"
    return comment or f"OBS said no (code {code})"


CLIENT = _Client()


def do(a):
    """Run a button's {"do": ..., "scene"/"input": ...}."""
    check(a)
    kind, need = ACTIONS[a["do"]]
    data = {"sceneName": str(a["scene"]).strip()} if need == "scene" else \
        {"inputName": str(a["input"]).strip()} if need == "input" else None
    CLIENT.call(kind, data)


def state():
    return CLIENT.state()


def reconnect():
    CLIENT.reconnect()


def test():
    """'ok' or raises with why it can't connect."""
    CLIENT.ensure()
    CLIENT.reconnect()
    time.sleep(0.2)
    CLIENT.call("GetVersion")
    return "ok"
