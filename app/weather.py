"""Weather forecast from MET Norway (the data behind yr.no), Locationforecast 2.0,
plus weather warnings from the Finnish Meteorological Institute (FMI).

Terms (https://api.met.no/doc/TermsOfService): identify the app in User-Agent, cache responses
and don't re-download before the Expires time, use at most 4 decimals in coordinates, and
credit "MET Norway" / yr.no. The data is free (CC BY 4.0) and needs no API key.
FMI warnings come from their open CAP feed (CC BY 4.0), which covers Finland only.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .config import Config

log = logging.getLogger("weather")
URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat={lat}&lon={lon}"   # complete has gusts
WARNINGS_URL = "https://alerts.fmi.fi/cap/feed/atom_en-GB.xml"
GEOCODE_URL = "https://nominatim.openstreetmap.org/search?"
USER_AGENT = "sahkovahti/1.0 github.com/essipassoja/sahkovahti"

# "Dramatic weather" thresholds for the heads-up block on the weather dashboard
GUST_STRONG = 20.0      # m/s; FMI's land wind warnings start around here
WIND_STRONG = 15.0      # m/s mean wind
HOT = 25.0              # °C, above
COLD = -25.0            # °C, below
RAIN_HEAVY_DAY = 15.0   # mm in a day
RAIN_HEAVY_HOUR = 5.0   # mm in an hour
SNOW_HEAVY_DAY = 8.0    # mm water in a day, roughly 8–15 cm of fresh snow


def geocode(text: str, limit: int = 5) -> list[dict]:
    """Place name or address → [{name, label, latitude, longitude}] via OpenStreetMap Nominatim.

    Only used when someone presses Search on the settings page (their policy: max 1 request/s).
    """
    url = GEOCODE_URL + urlencode({"q": text, "format": "jsonv2", "limit": limit,
                                   "accept-language": "fi,en"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.load(resp)
    return [{"name": r.get("name") or r["display_name"].split(",")[0], "label": r["display_name"],
             "latitude": round(float(r["lat"]), 4), "longitude": round(float(r["lon"]), 4)}
            for r in rows]


def _epoch(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def summarize(payload: dict, now: float, tz: ZoneInfo | None = None,
              hours: int = 12, step: int = 3, days: int = 3) -> dict:
    """Weather now, a few upcoming points, daily summaries for the next days and any
    dramatic weather ahead, from a Locationforecast payload."""
    tz = tz or ZoneInfo("Europe/Helsinki")
    series = payload.get("properties", {}).get("timeseries", [])

    def point(e):
        d = e["data"]
        nxt = d.get("next_1_hours") or d.get("next_6_hours") or {}
        inst = d["instant"]["details"]
        return {
            "ts": _epoch(e["time"]),
            "temp": inst.get("air_temperature"),
            "wind": inst.get("wind_speed"),
            "gust": inst.get("wind_speed_of_gust"),
            "symbol": nxt.get("summary", {}).get("symbol_code"),
            "precip_mm": nxt.get("details", {}).get("precipitation_amount"),
        }

    # forecast points are hourly for the first ~2 days, then 6-hourly; "now" = the hour we're in
    pts = [point(e) for e in series]
    past = [p for p in pts if p["ts"] <= now]
    cur = past[-1] if past else (pts[0] if pts else None)
    later = [p for p in pts if cur and cur["ts"] < p["ts"] <= cur["ts"] + hours * 3600
             and (p["ts"] - cur["ts"]) % (step * 3600) == 0]
    blocks = _blocks(series, pts, cur["ts"] if cur else now)
    day_list = _days(blocks, now, tz, days)
    return {"now": cur, "next": later, "days": day_list,
            "extremes": _extremes(blocks, now, tz, days),
            "updated_at": payload.get("properties", {}).get("meta", {}).get("updated_at")}


def _blocks(series: list, pts: list, start: float) -> list[dict]:
    """Forecast cut into back-to-back periods (1 h where hourly data exists, else 6 h), so
    precipitation can be summed without counting the same hours twice."""
    out = []
    for i, (e, p) in enumerate(zip(series, pts)):
        if p["ts"] < start or i + 1 >= len(pts):
            continue
        d, gap = e["data"], pts[i + 1]["ts"] - p["ts"]
        nxt = d.get("next_1_hours") if gap == 3600 else d.get("next_6_hours") if gap == 6 * 3600 else None
        if not nxt:
            continue
        det = nxt.get("details", {})
        temps = [t for t in (p["temp"], det.get("air_temperature_max"), det.get("air_temperature_min"))
                 if t is not None]
        sym = nxt.get("summary", {}).get("symbol_code") or ""
        out.append({"ts": p["ts"], "len": gap, "symbol": sym, "precip": det.get("precipitation_amount") or 0.0,
                    "tmax": max(temps, default=None), "tmin": min(temps, default=None),
                    "wind": p["wind"], "gust": p["gust"],
                    # sleet counts as rain; snow when the symbol says so or it's freezing
                    "snow": "snow" in sym or (p["temp"] is not None and p["temp"] <= 0.5 and "sleet" not in sym)})
    return out


def _local_day(ts: float, tz: ZoneInfo):
    return datetime.fromtimestamp(ts, tz).date()


def _midnight(day, tz: ZoneInfo) -> int:
    return int(datetime.combine(day, datetime.min.time(), tz).timestamp())


def _days(blocks: list, now: float, tz: ZoneInfo, n: int) -> list[dict]:
    """Daily summary for the n days after today: min/max temperature, rain, wind, a midday symbol."""
    today = _local_day(now, tz)
    out = []
    for k in range(1, n + 1):
        day = today + timedelta(days=k)
        bs = [b for b in blocks if _local_day(b["ts"], tz) == day]
        if not bs:
            break
        noon = _midnight(day, tz) + 12 * 3600
        mid = min(bs, key=lambda b: abs(b["ts"] + b["len"] / 2 - noon))
        winds = [b["wind"] for b in bs if b["wind"] is not None]
        gusts = [b["gust"] for b in bs if b["gust"] is not None]
        out.append({
            "date": day.isoformat(),
            "ts": _midnight(day, tz),
            "symbol": mid["symbol"].replace("_night", "_day").replace("_polartwilight", "_day"),
            "tmax": max((b["tmax"] for b in bs if b["tmax"] is not None), default=None),
            "tmin": min((b["tmin"] for b in bs if b["tmin"] is not None), default=None),
            "precip_mm": round(sum(b["precip"] for b in bs), 1),
            "snow_mm": round(sum(b["precip"] for b in bs if b["snow"]), 1),
            "wind": max(winds, default=None),
            "gust": max(gusts, default=None),
        })
    return out


def _extremes(blocks: list, now: float, tz: ZoneInfo, n: int) -> list[dict]:
    """Dramatic weather from now to the end of the last summarized day, one entry per kind."""
    end = _midnight(_local_day(now, tz) + timedelta(days=n + 1), tz)
    bs = [b for b in blocks if b["ts"] < end]
    out = []

    def peak(key, pick=max):
        have = [b for b in bs if b[key] is not None]
        return pick(have, key=lambda b: b[key]) if have else None

    g, w = peak("gust"), peak("wind")
    if (g and g["gust"] >= GUST_STRONG) or (w and w["wind"] >= WIND_STRONG):
        top = g if g and g["gust"] >= GUST_STRONG else w
        out.append({"kind": "wind", "ts": top["ts"], "gust": g["gust"] if g else None,
                    "wind": w["wind"] if w else None})
    hot, cold = peak("tmax"), peak("tmin", min)
    if hot and hot["tmax"] > HOT:
        out.append({"kind": "heat", "ts": hot["ts"], "temp": hot["tmax"]})
    if cold and cold["tmin"] < COLD:
        out.append({"kind": "cold", "ts": cold["ts"], "temp": cold["tmin"]})

    # rain and snow per local day, the rest of today included; report the first heavy day
    per_day: dict = {}
    for b in bs:
        d = per_day.setdefault(_local_day(b["ts"], tz), {"rain": 0.0, "snow": 0.0, "hour": None})
        d["snow" if b["snow"] else "rain"] += b["precip"]
        if b["len"] == 3600 and not b["snow"] and (d["hour"] is None or b["precip"] > d["hour"]["precip"]):
            d["hour"] = b
    for kind, limit in (("rain", RAIN_HEAVY_DAY), ("snow", SNOW_HEAVY_DAY)):
        for day, d in sorted(per_day.items()):
            hour = d["hour"] if kind == "rain" and d["hour"] and d["hour"]["precip"] >= RAIN_HEAVY_HOUR else None
            if d[kind] >= limit or hour:
                e = {"kind": kind, "ts": _midnight(day, tz), "total_mm": round(d[kind], 1)}
                if hour:
                    e.update(max_mm_h=round(hour["precip"], 1), max_ts=hour["ts"])
                out.append(e)
                break
    return out


def _inside(lat: float, lon: float, polygon: str) -> bool:
    """Ray-casting point-in-polygon; CAP polygons are "lat,lon lat,lon ..."."""
    pts = [tuple(map(float, p.split(","))) for p in polygon.split()]
    hit = False
    for (y1, x1), (y2, x2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


CAP = "{urn:oasis:names:tc:emergency:cap:1.2}"
COLOR_RANK = {"red": 0, "orange": 1, "yellow": 2}


def parse_warnings(xml: bytes, lat: float, lon: float, now: float) -> list[dict]:
    """FMI CAP feed → warnings in effect now or later whose area covers this point, worst first."""
    root = ET.fromstring(xml)
    alerts = [a for a in root.iter(CAP + "alert")
              if a.findtext(CAP + "status") == "Actual" and a.findtext(CAP + "msgType") in ("Alert", "Update")]
    # an update replaces the alert it references; drop the older copy if both are in the feed
    replaced = {ref.split(",")[1] for a in alerts for ref in (a.findtext(CAP + "references") or "").split()
                if ref.count(",") == 2}
    out = []
    for a in alerts:
        if a.findtext(CAP + "identifier") in replaced:
            continue
        infos = a.findall(CAP + "info")
        info = next((i for i in infos if i.findtext(CAP + "language") == "en-GB"), infos[0] if infos else None)
        if info is None:
            continue
        expires = info.findtext(CAP + "expires")
        if not expires or _epoch(expires) <= now:
            continue
        polys = [poly.text or "" for ar in info.findall(CAP + "area") for poly in ar.findall(CAP + "polygon")]
        if not any(_inside(lat, lon, poly) for poly in polys):
            continue
        params = {p.findtext(CAP + "valueName"): p.findtext(CAP + "value") for p in info.findall(CAP + "parameter")}
        onset = info.findtext(CAP + "onset") or info.findtext(CAP + "effective")
        out.append({
            "color": params.get("color") or "yellow",
            "event": info.findtext(CAP + "event"),
            "headline": info.findtext(CAP + "headline"),
            "description": info.findtext(CAP + "description"),
            "impacts": params.get("typicalImpacts"),
            "onset": _epoch(onset) if onset else None,
            "expires": _epoch(expires),
        })
    out.sort(key=lambda w: (COLOR_RANK.get(w["color"], 3), w["onset"] or 0))
    return out


class Warnings:
    """FMI warnings feed (~1 MB, all of Finland), re-read every 15 minutes."""
    TTL = 900

    def __init__(self):
        self._lock = threading.Lock()
        self._xml: bytes | None = None
        self._next = 0.0
        self.last_error: str | None = None

    def get(self, lat: float, lon: float) -> list[dict]:
        with self._lock:
            if time.time() >= self._next:
                try:
                    req = urllib.request.Request(WARNINGS_URL, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        self._xml = resp.read()
                    self._next, self.last_error = time.time() + self.TTL, None
                except Exception as e:
                    log.warning("warnings fetch failed: %s", e)
                    self._next, self.last_error = time.time() + 300, str(e)   # keep the old feed meanwhile
            xml = self._xml
        if not xml:
            return []
        try:
            return parse_warnings(xml, lat, lon, time.time())
        except ET.ParseError as e:
            log.warning("warnings feed unreadable: %s", e)
            return []


class Weather:
    """Fetches on demand and keeps the response until MET's Expires time."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._payload: dict | None = None
        self._loc: tuple[float, float] | None = None     # location the cached payload is for
        self._tried: tuple[float, float] | None = None   # location of the last attempt
        self._expires = 0.0
        self._last_modified: str | None = None
        self.last_error: str | None = None
        self.warnings = Warnings()

    def _fetch(self, loc: tuple[float, float]) -> None:
        url = URL.format(lat=loc[0], lon=loc[1])
        headers = {"User-Agent": USER_AGENT}
        if loc != self._loc:            # location changed on the settings page: start over
            self._payload = self._last_modified = None
        if self._last_modified and self._payload:
            headers["If-Modified-Since"] = self._last_modified
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                self._payload, self._loc = json.load(resp), loc
                self._last_modified = resp.headers.get("Last-Modified")
                expires = resp.headers.get("Expires")
        except urllib.error.HTTPError as e:
            if e.code != 304:
                raise
            expires = e.headers.get("Expires")
        try:
            self._expires = parsedate_to_datetime(expires).timestamp() if expires else 0
        except (TypeError, ValueError):
            self._expires = 0
        self._expires = max(self._expires, time.time() + 600)   # never more often than every 10 min

    def get(self) -> dict:
        w = self.cfg.weather
        if not w.enabled:
            return {"enabled": False}
        loc = (w.latitude, w.longitude)
        with self._lock:
            if time.time() >= self._expires or loc != self._tried:
                self._tried = loc
                try:
                    self._fetch(loc)
                    self.last_error = None
                except Exception as e:
                    log.warning("weather fetch failed: %s", e)
                    self.last_error = str(e)
                    self._expires = time.time() + 300   # retry in 5 min, keep showing old data
            payload = self._payload if loc == self._loc else None
        if payload is None:
            return {"enabled": True, "error": self.last_error or "no data"}
        return {"enabled": True, "place": w.place,
                "warnings": self.warnings.get(*loc),
                **summarize(payload, time.time(), self.cfg.tz, hours=24)}
