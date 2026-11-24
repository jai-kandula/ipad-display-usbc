"""Home Assistant: run a service (turn a light on, start a scene...) and read
an entity's state, using the address and long-lived access token saved in
Settings on the iPad. Plain HTTP(S) to HA's REST API; nothing to install."""
import json
import urllib.error
import urllib.request

import hostsettings


def _req(path, body=None, timeout=4.0):
    url, token = hostsettings.get("ha_url").rstrip("/"), hostsettings.get("ha_token")
    if not url or not token:
        raise RuntimeError("Home Assistant isn't set up (Settings > Buttons on the iPad)")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    req = urllib.request.Request(url + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Home Assistant refused the token") from None
        raise RuntimeError(f"Home Assistant answered {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"can't reach Home Assistant ({getattr(e, 'reason', e)})") from None
    return json.loads(data) if data else None


def call(service, entity="", data=None):
    """service: 'light.toggle', 'scene.turn_on', 'script.movie_time'..."""
    domain, _, name = str(service).strip().partition(".")
    if not domain or not name:
        raise ValueError(f"'{service}' isn't a service like light.toggle")
    body = dict(data or {})
    if entity:
        body["entity_id"] = str(entity).strip()
    _req(f"/api/services/{domain}/{name}", body)


def check():
    """'ok' or raises with the reason."""
    _req("/api/")
    return "ok"


def entities():
    """[{id, name, state}] for lights, switches, scenes, scripts and the like."""
    keep = ("light.", "switch.", "scene.", "script.", "fan.", "cover.", "media_player.",
            "input_boolean.", "automation.", "button.", "climate.", "lock.")
    out = []
    for s in _req("/api/states") or []:
        eid = s.get("entity_id", "")
        if eid.startswith(keep):
            out.append({"id": eid, "name": s.get("attributes", {}).get("friendly_name", eid),
                        "state": s.get("state")})
    return sorted(out, key=lambda e: e["id"])[:500]


def states(ids):
    """{entity_id: state} for the given entities (one request each, short timeout)."""
    out = {}
    for eid in ids[:24]:
        try:
            out[eid] = (_req(f"/api/states/{eid}", timeout=2.0) or {}).get("state")
        except Exception:
            out[eid] = None
    return out


def default_service(entity):
    """What tapping an entity does when no service is given."""
    domain = str(entity).split(".", 1)[0]
    return {"scene": "scene.turn_on", "script": "script.turn_on", "button": "button.press",
            "automation": "automation.trigger", "lock": "lock.unlock"}.get(domain, f"{domain}.toggle")
