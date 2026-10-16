"""Calendar events from iCal (.ics) addresses, e.g. Google Calendar's "Secret address in iCal format".

Standard library only: a small RFC 5545 reader that handles what calendar apps actually publish —
time zones, all-day events, repeating events (RRULE with the common DAILY / WEEKLY / MONTHLY /
YEARLY rules), skipped dates (EXDATE), moved or cancelled single occurrences (RECURRENCE-ID).
The addresses are secret, so they are never logged or sent to the browser.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Config

log = logging.getLogger("calendar")
USER_AGENT = "sahkovahti/1.0 github.com/essipassoja/sahkovahti"
TTL = 900                   # re-read each calendar every 15 minutes
MAX_BYTES = 20_000_000      # a calendar with many years of history can be several MB
MAX_STEPS = 20_000          # safety limit when expanding a repeating event
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


# ---------------- reading the file ----------------
def _lines(text: str) -> list[str]:
    """Unfold continuation lines (a line starting with a space or tab continues the previous one)."""
    out: list[str] = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        elif raw:
            out.append(raw)
    return out


def _prop(line: str) -> tuple[str, dict, str]:
    """'DTSTART;TZID=Europe/Helsinki:20261016T090000' → ('DTSTART', {'TZID': ...}, '20261016T090000')."""
    quoted, cut = False, len(line)
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            cut = i
            break
    head, value = line[:cut], line[cut + 1:]
    name, *params = head.split(";")
    p = {}
    for prm in params:
        k, _, v = prm.partition("=")
        p[k.upper()] = v.strip('"')
    return name.upper(), p, value


def _text(v: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v).strip()


def parse(text: str) -> tuple[str | None, list[dict]]:
    """→ (calendar name, raw events as {property: [(params, value), ...]})."""
    name, events, cur, depth = None, [], None, 0
    for line in _lines(text):
        key, params, value = _prop(line)
        if key == "BEGIN":
            if value.upper() == "VEVENT" and cur is None:
                cur, depth = {}, 0
            elif cur is not None:
                depth += 1              # e.g. a VALARM inside the event: skip its properties
            continue
        if key == "END":
            if cur is not None:
                if depth:
                    depth -= 1
                elif value.upper() == "VEVENT":
                    events.append(cur)
                    cur = None
            continue
        if cur is None:
            if key == "X-WR-CALNAME" and name is None:
                name = _text(value)
        elif not depth:
            cur.setdefault(key, []).append((params, value))
    return name, events


# ---------------- times ----------------
def _zone(params: dict, local: ZoneInfo):
    tzid = params.get("TZID")
    if tzid:
        try:
            return ZoneInfo(tzid.strip("/"))
        except Exception:
            pass                        # e.g. Outlook's "FLE Standard Time": use local time
    return local


def _when(params: dict, value: str, local: ZoneInfo) -> datetime | date:
    """A DATE (all-day) or a timezone-aware DATE-TIME."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or len(value) == 8:
        return datetime.strptime(value[:8], "%Y%m%d").date()
    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return dt.replace(tzinfo=timezone.utc)
    return dt.replace(tzinfo=_zone(params, local))


def _duration(v: str) -> timedelta:
    m = re.fullmatch(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", v.strip())
    if not m:
        return timedelta(0)
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def _key(t: datetime | date):
    """Comparable identity of an occurrence start (instant for timed, the date for all-day)."""
    return t.isoformat() if not isinstance(t, datetime) else int(t.timestamp())


# ---------------- repeating events ----------------
def _rrule(v: str) -> dict:
    return {k.upper(): val for k, _, val in (part.partition("=") for part in v.split(";")) if k}


def _nth_weekdays(year: int, month: int, spec: str) -> list[date]:
    """'2TU' → 2nd Tuesday, '-1FR' → last Friday, 'MO' → every Monday of the month."""
    m = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", spec)
    if not m or m.group(2) not in WEEKDAYS:
        return []
    n, wd = m.group(1), WEEKDAYS.index(m.group(2))
    first = date(year, month, 1)
    days = []
    d = first + timedelta(days=(wd - first.weekday()) % 7)
    while d.month == month:
        days.append(d)
        d += timedelta(days=7)
    if n is None:
        return days
    n = int(n)
    try:
        return [days[n - 1 if n > 0 else n]]
    except IndexError:
        return []


def _month_days(year: int, month: int, rule: dict, start: date) -> list[date]:
    if "BYDAY" in rule:
        out = [d for spec in rule["BYDAY"].split(",") for d in _nth_weekdays(year, month, spec)]
        if "BYMONTHDAY" in rule:        # e.g. "Friday the 13th"
            want = {int(x) for x in rule["BYMONTHDAY"].split(",")}
            out = [d for d in out if d.day in want]
        return sorted(set(out))
    last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)).day
    nums = [int(x) for x in rule["BYMONTHDAY"].split(",")] if "BYMONTHDAY" in rule else [start.day]
    return sorted({date(year, month, n if n > 0 else last + 1 + n) for n in nums if 1 <= (n if n > 0 else last + 1 + n) <= last})


def _dates(rule: dict, start: date):
    """Candidate dates of a repeating event in order, from its first date on (unbounded)."""
    freq, step = rule.get("FREQ"), max(1, int(rule.get("INTERVAL", 1) or 1))
    if freq == "DAILY":
        d = start
        while True:
            yield d
            d += timedelta(days=step)
    elif freq == "WEEKLY":
        wds = sorted({WEEKDAYS.index(x[-2:]) for x in rule.get("BYDAY", "").split(",") if x[-2:] in WEEKDAYS}) \
            or [start.weekday()]
        wkst = WEEKDAYS.index(rule.get("WKST", "MO")) if rule.get("WKST", "MO") in WEEKDAYS else 0
        week = start - timedelta(days=(start.weekday() - wkst) % 7)
        while True:
            for wd in sorted(wds, key=lambda x: (x - wkst) % 7):
                yield week + timedelta(days=(wd - wkst) % 7)
            week += timedelta(weeks=step)
    elif freq == "MONTHLY":
        y, m = start.year, start.month
        while True:
            yield from _month_days(y, m, rule, start)
            m += step
            y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    elif freq == "YEARLY":
        months = [int(x) for x in rule["BYMONTH"].split(",")] if "BYMONTH" in rule else [start.month]
        y = start.year
        while True:
            for m in sorted(months):
                if "BYDAY" in rule or "BYMONTHDAY" in rule:
                    yield from _month_days(y, m, rule, start)
                else:
                    try:
                        yield date(y, m, start.day)
                    except ValueError:
                        pass            # 29 Feb in a non-leap year
            y += step


def _occurrences(start, rule: dict, window_end, local: ZoneInfo):
    """Starts of a repeating event up to window_end, honouring COUNT and UNTIL."""
    timed = isinstance(start, datetime)
    first = start.date() if timed else start
    until = None
    if "UNTIL" in rule:
        until = _when({}, rule["UNTIL"], local)
        if timed and not isinstance(until, datetime):
            until = datetime.combine(until, datetime.max.time(), start.tzinfo)
        elif not timed and isinstance(until, datetime):
            until = until.astimezone(local).date()
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    n = 0
    for i, d in enumerate(_dates(rule, first)):
        if i > MAX_STEPS or d < first:
            if i > MAX_STEPS:
                break
            continue
        # keep the wall-clock time in the event's own zone, so DST changes are right
        t = datetime.combine(d, start.timetz()).replace(tzinfo=start.tzinfo) if timed else d
        if until is not None and t > until:
            break
        if (t.astimezone(local).date() if timed else t) > window_end:
            break
        n += 1
        if count is not None and n > count:
            break
        yield t


# ---------------- events in a time window ----------------
def expand(events: list[dict], day0: date, day1: date, local: ZoneInfo) -> list[dict]:
    """Events overlapping the local days day0..day1 (inclusive)."""
    t0 = datetime.combine(day0, datetime.min.time(), local)
    t1 = datetime.combine(day1 + timedelta(days=1), datetime.min.time(), local)
    one = lambda ev, k: ev.get(k, [({}, "")])[0]   # noqa: E731

    # single occurrences that were moved or cancelled replace the repeating event's original
    moved: set = set()
    for ev in events:
        if "RECURRENCE-ID" in ev:
            p, v = one(ev, "RECURRENCE-ID")
            try:
                moved.add((one(ev, "UID")[1], _key(_when(p, v, local))))
            except ValueError:
                pass

    out = []
    for ev in events:
        try:
            if "DTSTART" not in ev or one(ev, "STATUS")[1].upper() == "CANCELLED":
                continue
            start = _when(*one(ev, "DTSTART"), local)
            if "DTEND" in ev:
                length = _when(*one(ev, "DTEND"), local) - start
            elif "DURATION" in ev:
                length = _duration(one(ev, "DURATION")[1])
            else:
                length = timedelta(days=1) if not isinstance(start, datetime) else timedelta(0)
            uid = one(ev, "UID")[1]
            if "RRULE" in ev and "RECURRENCE-ID" not in ev:
                skip = {_key(_when(p, x, local)) for p, v in ev.get("EXDATE", []) for x in v.split(",") if x}
                starts = [s for s in _occurrences(start, _rrule(one(ev, "RRULE")[1]), day1, local)
                          if _key(s) not in skip and (uid, _key(s)) not in moved]
            else:
                starts = [start]
        except (ValueError, KeyError, IndexError):
            continue                    # one broken event shouldn't hide the rest
        for s in starts:
            e = s + length
            if isinstance(s, datetime):
                a, b = s.astimezone(local), e.astimezone(local)
                if a >= t1 or (b <= t0 and not (a == b == t0)):
                    continue
                out.append({"start": int(a.timestamp()), "end": int(b.timestamp()), "all_day": False,
                            "date": a.date().isoformat()})
            else:
                last = e - timedelta(days=1) if e > s else s
                if last < day0 or s > day1:
                    continue
                out.append({"start": int(datetime.combine(s, datetime.min.time(), local).timestamp()),
                            "end": int(datetime.combine(last + timedelta(days=1), datetime.min.time(), local).timestamp()),
                            "all_day": True, "date": s.isoformat(), "last_date": last.isoformat()})
            out[-1].update(title=_text(one(ev, "SUMMARY")[1]) or "(no title)",
                           location=_text(one(ev, "LOCATION")[1]) or None)
    out.sort(key=lambda e: (e["start"], not e["all_day"], e["title"]))
    return out


class CalendarSource:
    """Fetches each calendar address on demand, at most every 15 minutes; keeps the last good copy."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}     # url → {"next": t, "name": .., "events": [...], "error": ..}

    def _fetch(self, url: str) -> None:
        c = self._cache.setdefault(url, {"next": 0.0, "name": None, "events": None, "error": None})
        if time.time() < c["next"]:
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("calendar is too big")
            if not body.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"BEGIN:VCALENDAR"):
                raise ValueError("that address didn't return a calendar (.ics)")
            name, events = parse(body.decode("utf-8-sig", "replace"))
            c.update(next=time.time() + TTL, name=name, events=events, error=None)
        except Exception as e:
            # the message must not contain the secret address
            msg = f"HTTP {e.code}" if hasattr(e, "code") else getattr(e, "reason", None) or e
            log.warning("calendar %s fetch failed: %s", url.split("/")[2], msg)
            c.update(next=time.time() + 300, error=str(msg))

    def get(self) -> dict:
        cal = self.cfg.calendar
        if not cal.enabled or not cal.urls:
            return {"enabled": False}
        tz = self.cfg.tz
        today = datetime.now(tz).date()
        cals, events = [], []
        with self._lock:
            for k, url in enumerate(cal.urls):
                self._fetch(url)
                c = self._cache[url]
                cals.append({"name": c["name"] or f"Calendar {k + 1}", "error": c["error"],
                             "events": None if c["events"] is None else len(c["events"])})
                if c["events"]:
                    for e in expand(c["events"], today, today + timedelta(days=cal.days - 1), tz):
                        events.append({**e, "calendar": k})
            for url in list(self._cache):           # forget addresses no longer in the settings
                if url not in cal.urls:
                    del self._cache[url]
        events.sort(key=lambda e: (e["start"], not e["all_day"], e["title"]))
        return {"enabled": True, "days": cal.days, "today": today.isoformat(),
                "calendars": cals, "events": events}
