"""Your calendar on the iPad: classes, exams and due dates from a calendar's
private iCal link (Google Calendar: Settings > your calendar > "Secret
address in iCal format"; Outlook: Publish calendar > ICS). Read-only.

The link is kept in settings.json and fetched at most every 10 minutes, only
while the iPad shows the calendar. Weekly classes (repeating events),
cancelled ones and moved ones are handled."""
import datetime as dt
import threading
import time
import urllib.request

import hostsettings

hostsettings.register({"ical_url": ""})
hostsettings.SECRET.add("ical_url")
REFRESH = 600
DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
_lock = threading.Lock()
_cache = {"at": 0.0, "url": None, "events": None, "error": ""}


def _tz(name):
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name.strip('"'))
    except Exception:
        return None                    # unknown zone (or no tz data on this PC): the PC's own time


def _when(value, params):
    """(naive wall-clock datetime, tzinfo or None for the PC's time, all_day)."""
    v = value.strip()
    if params.get("VALUE") == "DATE" or (len(v) == 8 and v.isdigit()):
        return dt.datetime.strptime(v[:8], "%Y%m%d"), None, True
    if v.endswith("Z"):
        return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S"), dt.timezone.utc, False
    return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S"), _tz(params.get("TZID")), False


def _aware(naive, tz):
    return naive.replace(tzinfo=tz) if tz else naive.astimezone()      # naive = the PC's local time


def _lines(text):
    out = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _prop(line):
    head, _, value = line.partition(":")
    name, *ps = head.split(";")
    params = {}
    for p in ps:
        k, _, v = p.partition("=")
        params[k.upper()] = v
    return name.upper(), params, value


def _unescape(s):
    return s.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse(text):
    """VEVENTs as dicts: uid, title, location, start (naive), tz, allday, length, rrule, exdates, rid, cancelled."""
    events, ev = [], None
    for line in _lines(text):
        if line == "BEGIN:VEVENT":
            ev = {"exdates": set(), "rrule": None, "rid": None, "title": "", "location": "", "cancelled": False}
            continue
        if line == "END:VEVENT":
            if ev and ev.get("start") is not None:
                if ev.get("end") is not None:
                    ev["length"] = _aware(ev["end"], ev["end_tz"]) - _aware(ev["start"], ev["tz"])
                elif "length" not in ev:
                    ev["length"] = dt.timedelta(days=1) if ev["allday"] else dt.timedelta(0)
                events.append(ev)
            ev = None
            continue
        if ev is None:
            continue
        name, params, value = _prop(line)
        try:
            if name == "SUMMARY":
                ev["title"] = _unescape(value)[:200]
            elif name == "LOCATION":
                ev["location"] = _unescape(value)[:200]
            elif name == "UID":
                ev["uid"] = value
            elif name == "STATUS":
                ev["cancelled"] = value.upper() == "CANCELLED"
            elif name == "DTSTART":
                ev["start"], ev["tz"], ev["allday"] = _when(value, params)
            elif name == "DTEND":
                ev["end"], ev["end_tz"], _ = _when(value, params)
            elif name == "DURATION":
                ev["length"] = _duration(value)
            elif name == "RRULE":
                ev["rrule"] = dict(p.partition("=")[::2] for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                for v in value.split(","):
                    ev["exdates"].add(_when(v, params)[0])
            elif name == "RECURRENCE-ID":
                ev["rid"] = _when(value, params)[0]
        except ValueError:
            pass
    return events


def _duration(v):
    sign = -1 if v.startswith("-") else 1
    total, num, part = 0, "", "date"
    for ch in v.lstrip("+-P"):
        if ch == "T":
            part = "time"
        elif ch.isdigit():
            num += ch
        else:
            n = int(num or 0)
            num = ""
            total += n * {"W": 604800, "D": 86400, "H": 3600, "S": 1}.get(ch, 60 if part == "time" else 0)
    return dt.timedelta(seconds=sign * total)


def _occurrences(ev, lo, hi):
    """Start times (naive, in the event's zone) of an event between lo and hi (naive, same zone)."""
    s, r = ev["start"], ev["rrule"]
    if not r:
        return [s] if lo - ev["length"] <= s < hi else []
    freq, step = r.get("FREQ", ""), max(1, int(r.get("INTERVAL", 1) or 1))
    count = int(r["COUNT"]) if r.get("COUNT", "").isdigit() else None
    until = None
    if r.get("UNTIL"):
        try:
            u, utz, _ = _when(r["UNTIL"], {})
            until = _aware(u, utz).astimezone(ev["tz"]).replace(tzinfo=None) if utz and not ev["allday"] else u
        except ValueError:
            pass
    bydays = [DAYS[d[-2:]] for d in r.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    out, n, i = [], 0, 0
    while i < 3000:
        if freq == "WEEKLY":
            week = s - dt.timedelta(days=s.weekday()) + dt.timedelta(weeks=i * step)
            cands = sorted(week + dt.timedelta(days=d) for d in (bydays or [s.weekday()]))
            cands = [c.replace(hour=s.hour, minute=s.minute, second=s.second) for c in cands if c >= s.replace(hour=0, minute=0, second=0)]
        elif freq == "DAILY":
            cands = [s + dt.timedelta(days=i * step)]
            if bydays:
                cands = [c for c in cands if c.weekday() in bydays]
        elif freq == "MONTHLY":
            m = s.month - 1 + i * step
            try:
                cands = [s.replace(year=s.year + m // 12, month=m % 12 + 1)]
            except ValueError:
                cands = []                 # the 31st in a short month
        elif freq == "YEARLY":
            try:
                cands = [s.replace(year=s.year + i * step)]
            except ValueError:
                cands = []
        else:
            return [s] if lo - ev["length"] <= s < hi else []
        i += 1
        for c in cands:
            if (until and c > until) or (count is not None and n >= count):
                return out
            n += 1
            if c >= hi:
                return out
            if c + ev["length"] > lo and c not in ev["exdates"]:
                out.append(c)
        if cands and cands[0] >= hi:
            return out
    return out


def agenda(events, days=7, now=None):
    """Events from the start of today for `days` days, as the iPad shows them."""
    now = now or dt.datetime.now().astimezone()
    lo_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    hi_local = lo_local + dt.timedelta(days=days)
    moved = {(e.get("uid"), e["rid"]) for e in events if e["rid"] is not None}
    out = []
    for e in events:
        if e["cancelled"] and e["rid"] is None:
            continue
        tz = e["tz"]
        if e["allday"]:
            lo, hi = lo_local.replace(tzinfo=None), hi_local.replace(tzinfo=None)
        else:
            lo = lo_local.astimezone(tz).replace(tzinfo=None) if tz else lo_local.replace(tzinfo=None)
            hi = hi_local.astimezone(tz).replace(tzinfo=None) if tz else hi_local.replace(tzinfo=None)
        if e["rid"] is not None:             # a moved or cancelled single class of a repeating event
            if e["cancelled"]:
                continue
            starts = _occurrences({**e, "rrule": None}, lo, hi)
        else:
            starts = [c for c in _occurrences(e, lo, hi) if (e.get("uid"), c) not in moved]
        for c in starts:
            if e["allday"]:
                start = c.replace(tzinfo=lo_local.tzinfo)
            else:
                start = _aware(c, tz).astimezone()
            end = start + e["length"]
            out.append({"title": e["title"] or "(no title)", "location": e["location"], "allday": e["allday"],
                        "start": start.isoformat(), "end": end.isoformat(), "ts": start.timestamp(), "te": end.timestamp()})
    out.sort(key=lambda x: (x["ts"], not x["allday"]))
    return out[:200]


def events(force=False):
    """{events: [...], error, updated} for the next 7 days."""
    url = hostsettings.get("ical_url").strip()
    if not url:
        return {"events": [], "error": "Add your calendar's private iCal link in Settings > School", "updated": None}
    url = url.replace("webcal://", "https://", 1)
    with _lock:
        if force or url != _cache["url"] or time.time() - _cache["at"] > REFRESH:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "iPad Display calendar"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    text = r.read(8 << 20).decode("utf-8", "replace")
                if "BEGIN:VCALENDAR" not in text:
                    raise ValueError("that link isn't an iCal calendar (use the secret iCal address)")
                _cache.update(events=parse(text), error="", url=url)
            except Exception as e:
                _cache["error"] = f"couldn't get the calendar ({getattr(e, 'reason', e)})"
                if _cache["url"] != url:
                    _cache["events"] = None
                _cache["url"] = url
            _cache["at"] = time.time()
        evs = _cache["events"]
        return {"events": agenda(evs) if evs else [], "error": _cache["error"], "updated": _cache["at"]}
