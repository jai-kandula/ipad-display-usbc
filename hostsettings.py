"""Settings kept on the PC (settings.json next to this file), shared by every
iPad: things the PC itself needs, such as the Home Assistant address and
token, or whether buttons follow the app in front. The token is never sent
back to the iPad in full."""
import copy
import json
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "settings.json")

DEFAULT = {
    "auto_profiles": True,        # show a profile's buttons while one of its apps is in front
    "ha_url": "",                 # Home Assistant, e.g. http://homeassistant.local:8123
    "ha_token": "",               # a long-lived access token from your HA profile page
    "wifi_plain": True,           # also answer on the unencrypted Wi-Fi address
}
SECRET = {"ha_token"}
_lock = threading.Lock()
_cache = None


def load():
    global _cache
    with _lock:
        if _cache is None:
            data = copy.deepcopy(DEFAULT)
            try:
                with open(PATH, encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    data.update({k: v for k, v in saved.items() if k in DEFAULT or k.startswith("x_")})
            except (OSError, ValueError):
                pass
            _cache = data
        return copy.deepcopy(_cache)


def get(key):
    return load().get(key, DEFAULT.get(key))


def public():
    """What the iPad may see: secrets only say whether they are set."""
    d = load()
    for k in SECRET:
        d[k] = "set" if d.get(k) else ""
    return d


def update(changes):
    """Apply {key: value} for known keys (type-checked against the default)."""
    global _cache
    data = load()
    for k, v in (changes or {}).items():
        if k not in DEFAULT:
            continue
        if k in SECRET and v == "set":           # unchanged secret coming back from the iPad
            continue
        want = type(DEFAULT[k])
        if want is bool:
            v = bool(v)
        elif want is int:
            v = int(v)
        elif want is float:
            v = float(v)
        elif want is str:
            v = str(v).strip()
        elif not isinstance(v, want):
            raise ValueError(f"{k} has the wrong type")
        data[k] = v
    with _lock:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, PATH)
        _cache = data
    return public()


def register(defaults):
    """Other modules add their own keys (with defaults) at import time."""
    global _cache
    with _lock:
        for k, v in defaults.items():
            DEFAULT.setdefault(k, v)
        _cache = None
