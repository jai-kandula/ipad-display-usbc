"""Coding companion: run your build and test commands from the iPad and
watch their output, see a project's Git state, follow a log file, and keep
snippets to type or copy on the PC.

Projects live in settings.json under "dev_projects", edited on the iPad:
  {"name": "Mod", "folder": "C:\\\\Code\\\\mymod",
   "tasks": [{"name": "Build", "cmd": "gradlew build"}, {"name": "Test", "cmd": "gradlew test"}],
   "log": "run\\\\logs\\\\latest.log"}
A task runs in the project's folder in a hidden window. Only the last 2000
lines of each run are kept. Nothing runs or is read unless you ask."""
import collections
import json
import os
import re
import subprocess
import sys
import threading
import time

import hostsettings

HERE = os.path.dirname(os.path.abspath(__file__))
SNIPPETS = os.path.join(HERE, "snippets.json")
IS_WINDOWS = sys.platform == "win32"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
MAX_LINES = 2000
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
hostsettings.register({"dev_projects": []})

_lock = threading.Lock()
_runs = {}                  # "project/task" -> Run


def projects():
    out = []
    for p in hostsettings.get("dev_projects") or []:
        if not isinstance(p, dict) or not str(p.get("name", "")).strip():
            continue
        tasks = [{"name": str(t.get("name", "")).strip()[:40], "cmd": str(t.get("cmd", "")).strip()[:500]}
                 for t in (p.get("tasks") or []) if isinstance(t, dict) and str(t.get("cmd", "")).strip()]
        out.append({"name": str(p["name"]).strip()[:40], "folder": str(p.get("folder", "")).strip(),
                    "tasks": tasks[:12], "log": str(p.get("log", "")).strip()})
    return out[:20]


def check_projects(ps):
    """Clean up projects coming from the iPad before they are saved."""
    if not isinstance(ps, list):
        raise ValueError("expected a list of projects")
    out = []
    for p in ps[:20]:
        if not isinstance(p, dict) or not str(p.get("name", "")).strip():
            raise ValueError("every project needs a name")
        folder = str(p.get("folder", "")).strip()
        tasks = []
        for t in (p.get("tasks") or [])[:12]:
            if isinstance(t, dict) and str(t.get("cmd", "")).strip():
                tasks.append({"name": str(t.get("name") or t["cmd"]).strip()[:40], "cmd": str(t["cmd"]).strip()[:500]})
        out.append({"name": str(p["name"]).strip()[:40], "folder": folder, "tasks": tasks,
                    "log": str(p.get("log", "")).strip()})
    names = [p["name"] for p in out]
    if len(set(names)) != len(names):
        raise ValueError("two projects have the same name")
    return out


def _project(name):
    p = next((p for p in projects() if p["name"] == name), None)
    if not p:
        raise ValueError(f"no project called '{name}'")
    return p


# ---------------------------------------------------------------- tasks
class Run:
    def __init__(self, key, cmd, folder):
        self.key, self.cmd, self.folder = key, cmd, folder
        self.lines = collections.deque(maxlen=MAX_LINES)
        self.n = 0                       # lines ever written (so the iPad can ask for only new ones)
        self.started, self.ended, self.code, self.stopped = time.time(), None, None, False
        self.proc = subprocess.Popen(cmd, cwd=folder or None, shell=True, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=_NO_WINDOW | (0x00000200 if IS_WINDOWS else 0),
                                     start_new_session=not IS_WINDOWS)
        threading.Thread(target=self._read, daemon=True, name="dev-run").start()

    def _read(self):
        buf = b""
        for chunk in iter(lambda: self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1")
                          else self.proc.stdout.readline(), b""):
            buf += chunk
            *done, buf = re.split(rb"\r?\n", buf)
            for raw in done:
                self._add(raw)
            if len(buf) > 8192:          # a very long line or progress bar with no newline
                self._add(buf)
                buf = b""
        if buf:
            self._add(buf)
        self.code = self.proc.wait()
        self.ended = time.time()

    def _add(self, raw):
        text = raw.decode("utf-8", "replace")
        text = ANSI.sub("", text).split("\r")[-1]       # keep the last state of a progress line
        with _lock:
            self.lines.append(text[:2000])
            self.n += 1

    def stop(self):
        if self.ended is not None:
            return
        self.stopped = True
        if IS_WINDOWS:              # the whole tree (gradle daemons, node watchers...)
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                           capture_output=True, creationflags=_NO_WINDOW)
        else:
            import signal
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass

    def info(self, since=0):
        with _lock:
            first = self.n - len(self.lines)
            start = max(since, first)
            lines = list(self.lines)[start - first:]
            n = self.n
        return {"key": self.key, "cmd": self.cmd, "running": self.ended is None, "code": self.code,
                "stopped": self.stopped, "seconds": round((self.ended or time.time()) - self.started, 1), "started": self.started,
                "n": n, "from": start, "lines": lines}


def run(project, task):
    p = _project(project)
    t = next((t for t in p["tasks"] if t["name"] == task), None)
    if not t:
        raise ValueError(f"no task called '{task}' in {project}")
    if p["folder"] and not os.path.isdir(p["folder"]):
        raise ValueError(f"the folder {p['folder']} doesn't exist")
    key = f"{project}/{task}"
    old = _runs.get(key)
    if old and old.ended is None:
        raise ValueError(f"{task} is already running")
    _runs[key] = Run(key, t["cmd"], p["folder"])
    return _runs[key].info()


def stop(project, task):
    r = _runs.get(f"{project}/{task}")
    if r:
        r.stop()


def output(project, task, since=0):
    r = _runs.get(f"{project}/{task}")
    return r.info(since) if r else None


def status():
    """{key: {running, code, seconds}} for every task run since the host started."""
    return {k: {"running": r.ended is None, "code": 0 if r.stopped else r.code, "seconds": round((r.ended or time.time()) - r.started, 1)}
            for k, r in _runs.items()}


# ---------------------------------------------------------------- git
def _git(folder, *args):
    r = subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True, timeout=8,
                       encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else "git failed")
    return r.stdout


def git(project):
    """Branch, ahead/behind, changed files and the last commits of a project's folder."""
    p = _project(project)
    if not p["folder"]:
        raise ValueError("this project has no folder")
    try:
        st = _git(p["folder"], "status", "--porcelain=v1", "--branch", "--untracked-files=normal")
    except FileNotFoundError:
        raise RuntimeError("Git isn't installed (or isn't on the PATH)") from None
    lines = st.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    branch = head.split("...")[0].replace("No commits yet on ", "")
    ahead = int(m.group(1)) if (m := re.search(r"ahead (\d+)", head)) else 0
    behind = int(m.group(1)) if (m := re.search(r"behind (\d+)", head)) else 0
    files = [{"st": ln[:2].strip() or "?", "path": ln[3:]} for ln in lines[1:]][:200]
    log = []
    try:
        for ln in _git(p["folder"], "log", "-8", "--format=%h\x1f%s\x1f%an\x1f%ct").splitlines():
            h, s, a, t = ln.split("\x1f")
            log.append({"hash": h, "subject": s, "author": a, "time": int(t)})
    except (RuntimeError, ValueError):
        pass
    return {"branch": branch, "ahead": ahead, "behind": behind, "changed": len(lines) - 1,
            "files": files, "log": log, "upstream": "..." in head}


_tile = {"at": 0.0, "data": None}


def git_tile():
    """The Git tile: the first project with a folder (asked at most every 5 s)."""
    if time.time() - _tile["at"] < 5 and _tile["data"] is not None:
        return _tile["data"]
    p = next((p for p in projects() if p["folder"]), None)
    try:
        d = {"project": p["name"], **git(p["name"])} if p else {"error": "add a project in Dev first"}
        d.pop("files", None)
        d["log"] = d.get("log", [])[:1]
    except Exception as e:
        d = {"project": p["name"] if p else "", "error": str(e)}
    _tile.update(at=time.time(), data=d)
    return d


GIT_ACTIONS = {"fetch": ["fetch", "--prune"], "pull": ["pull", "--ff-only"], "push": ["push"]}


def git_do(project, what):
    """fetch, pull (fast-forward only) or push; returns git's last line."""
    if what not in GIT_ACTIONS:
        raise ValueError("unknown git action")
    p = _project(project)
    r = subprocess.run(["git", "-C", p["folder"], *GIT_ACTIONS[what]], capture_output=True, text=True,
                       timeout=60, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    out = (r.stderr + r.stdout).strip().splitlines()
    if r.returncode:
        raise RuntimeError(out[-1] if out else f"git {what} failed")
    return out[-1] if out else "done"


# ---------------------------------------------------------------- logs
def tail(path, since=None, max_bytes=65536):
    """New text at the end of a log file. since: the byte offset the iPad
    already has (None: just the last part). Handles the file being
    replaced or cut (a new latest.log)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        raise RuntimeError(f"can't open {path}") from None
    start = size - max_bytes if since is None or since > size else since
    start = max(0, start, size - max_bytes)
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(size - start)
    text = ANSI.sub("", data.decode("utf-8", "replace"))
    if start and since is None:
        text = text.split("\n", 1)[-1]      # start on a whole line
    return {"pos": size, "text": text, "reset": since is not None and since > size}


def project_log(project, since=None):
    p = _project(project)
    if not p["log"]:
        raise ValueError("this project has no log file set")
    path = p["log"] if os.path.isabs(p["log"]) else os.path.join(p["folder"], p["log"])
    return tail(path, since)


# ---------------------------------------------------------------- snippets
def snippets():
    try:
        with open(SNIPPETS, encoding="utf-8") as f:
            d = json.load(f)
        return [s for s in d if isinstance(s, dict) and isinstance(s.get("text"), str)] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def save_snippets(items):
    if not isinstance(items, list):
        raise ValueError("expected a list of snippets")
    clean = [{"name": str(s.get("name") or "").strip()[:60], "text": str(s.get("text") or "")[:20000]}
             for s in items[:300] if isinstance(s, dict) and str(s.get("text") or "").strip()]
    tmp = SNIPPETS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=1, ensure_ascii=False)
    os.replace(tmp, SNIPPETS)
    return clean
