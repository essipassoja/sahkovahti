"""Weather forecast from MET Norway (the data behind yr.no), Locationforecast 2.0.

Terms (https://api.met.no/doc/TermsOfService): identify the app in User-Agent, cache responses
and don't re-download before the Expires time, use at most 4 decimals in coordinates, and
credit "MET Norway" / yr.no. The data is free (CC BY 4.0) and needs no API key.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from .config import Config

log = logging.getLogger("weather")
URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
GEOCODE_URL = "https://nominatim.openstreetmap.org/search?"
USER_AGENT = "sahkovahti/1.0 github.com/essipassoja/sahkovahti"


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


def summarize(payload: dict, now: float, hours: int = 12, step: int = 3) -> dict:
    """Weather now plus a few upcoming points from a Locationforecast 'compact' payload."""
    series = payload.get("properties", {}).get("timeseries", [])

    def point(e):
        d = e["data"]
        nxt = d.get("next_1_hours") or d.get("next_6_hours") or {}
        inst = d["instant"]["details"]
        return {
            "ts": _epoch(e["time"]),
            "temp": inst.get("air_temperature"),
            "wind": inst.get("wind_speed"),
            "symbol": nxt.get("summary", {}).get("symbol_code"),
            "precip_mm": nxt.get("details", {}).get("precipitation_amount"),
        }

    # forecast points are hourly for the first ~2 days; "now" = the hour we're in
    pts = [point(e) for e in series]
    past = [p for p in pts if p["ts"] <= now]
    cur = past[-1] if past else (pts[0] if pts else None)
    later = [p for p in pts if cur and cur["ts"] < p["ts"] <= cur["ts"] + hours * 3600
             and (p["ts"] - cur["ts"]) % (step * 3600) == 0]
    return {"now": cur, "next": later,
            "updated_at": payload.get("properties", {}).get("meta", {}).get("updated_at")}


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
                **summarize(payload, time.time(), hours=24)}
