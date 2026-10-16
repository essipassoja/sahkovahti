"""Tiny JSON API + static file server (standard library only)."""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analysis
from .config import SECTIONS, Config
from .db import DB
from .meter import MeterService
from .notify import Notifier, send
from .prices import PriceUpdater
from .ical import CalendarSource
from .weather import Weather, geocode

STATIC = Path(__file__).parent / "static"
# the service worker and manifest are served from the root so they control the whole app
PAGES = {"/": "simple.html", "": "simple.html", "/details": "index.html", "/settings": "settings.html",
         "/sw.js": "sw.js", "/manifest.webmanifest": "manifest.webmanifest"}
mimetypes.add_type("application/manifest+json", ".webmanifest")
SETTINGS_KEY = "settings"
log = logging.getLogger("http")


class App:
    def __init__(self, cfg: Config, db: DB, meter: MeterService, prices: PriceUpdater,
                 notifier: Notifier | None = None):
        self.cfg, self.db, self.meter, self.prices, self.notifier = cfg, db, meter, prices, notifier
        self.weather_src = Weather(cfg)
        self.calendar_src = CalendarSource(cfg)
        try:
            cfg.apply(self._saved_settings())
        except (TypeError, ValueError) as e:
            log.warning("ignoring saved settings (%s); using config.toml values", e)

    def _saved_settings(self) -> dict:
        raw = self.db.get_meta(SETTINGS_KEY)
        return json.loads(raw) if raw else {}

    # ---------------- endpoints ----------------
    def live(self, _q):
        r = self.meter.latest
        now = int(time.time())
        tariff = self.cfg.tariff
        spot = analysis.spot_at(now, tariff, self.db)
        total = tariff.total_c_kwh(spot, now)
        energy = tariff.energy_c_kwh(spot, now)
        discounted = tariff.discount_active(now)
        total_full = tariff.total_c_kwh(spot, now, discount=False) if discounted else total
        step = tariff.slot_s
        slot_start = now - now % step
        nxt = analysis.spot_at(slot_start + step, tariff, self.db)
        today = datetime.now(self.cfg.tz).date()
        rep = analysis.day_report(today, self.cfg, self.db)
        cur = next((s for s in rep["slots"] if s["state"] == "now"), None)
        p = r.power_w if r and r.power_w is not None else None
        return {
            "ts": r.ts if r else None,
            "power_w": p,
            "export_power_w": r.export_power_w if r else None,
            "import_kwh": r.import_kwh if r else None,
            "phases": {k: v for k, v in (r.extra or {}).items() if k[:1] == "l"} if r else {},
            "spot": spot,
            "energy": round(energy, 3) if energy is not None else None,
            "total": round(total, 3) if total is not None else None,
            "total_full": round(total_full, 3) if total_full is not None else None,
            "discount_percent": tariff.spot_discount_percent if discounted else 0,
            "next_spot": nxt,
            "cost_rate_eur_h": round(p / 1000 * total / 100, 3) if p is not None and total is not None else None,
            "slot_s": step,
            "slot": {"start": slot_start, "kwh": cur["kwh"] if cur else None, "cost_eur": cur["cost_eur"] if cur else None},
            "today": rep["summary"],
            "light": analysis.traffic_light(self.cfg, self.db, now),
            "recent": self._recent_power(now),
        }

    def _recent_power(self, now: int):
        """Last hour of power for the sparkline: stored history + the newest live values."""
        pts = [(t, round(p or 0)) for t, p, _e in self.db.power_series(now - 3600, now + 1, 30)]
        last = pts[-1][0] if pts else 0
        pts += [(round(t), round(w)) for t, w in self.meter.recent if t > last + 30]
        return pts

    def day(self, q):
        d = q.get("date", [None])[0]
        d = date.fromisoformat(d) if d else datetime.now(self.cfg.tz).date()
        return analysis.day_report(d, self.cfg, self.db)

    def history(self, q):
        days = max(1, min(int(q.get("days", ["30"])[0]), 366))
        return {"days": analysis.history(days, self.cfg, self.db),
                "month": analysis.month_to_date(self.cfg, self.db)}

    def power(self, q):
        minutes = max(5, min(int(q.get("minutes", ["180"])[0]), 2880))
        now = int(time.time())
        bucket = 10 if minutes <= 60 else 60 if minutes <= 720 else 300
        rows = self.db.power_series(now - minutes * 60, now + 1, bucket)
        last = rows[-1][0] if rows else 0
        rows += [r for r in self.meter.pending_power(now - minutes * 60, bucket) if r[0] > last]
        return {"bucket_s": bucket, "points": [(t, round(p or 0), round(e or 0)) for t, p, e in rows]}

    def cheapest(self, _q):
        return {"windows": analysis.cheapest_windows(self.cfg, self.db)}

    def status(self, _q):
        t = self.cfg.tariff
        now = int(time.time())
        return {
            "meter": self.meter.status(),
            "prices": {
                "last_ok": self.prices.last_ok,
                "error": self.prices.last_error,
                "known_until": self.db.last_price_end(),
            },
            "tariff": {
                **t.as_dict(),
                "adders_c_kwh": round(t.adders_c_kwh(now), 3),
                "discount_active": t.discount_active(now),
                "monthly_fixed_eur": round(t.monthly_fixed_eur(datetime.now(self.cfg.tz).date()), 2),
            },
            "timezone": str(self.cfg.tz),
            "readings": self.db.reading_count(),
        }

    def weather(self, _q):
        return self.weather_src.get()

    def calendar(self, _q):
        return self.calendar_src.get()

    def outlook(self, _q):
        tomorrow = datetime.now(self.cfg.tz).date() + timedelta(days=1)
        return {"tomorrow": analysis.day_outlook(tomorrow, self.cfg, self.db)}

    def geocode(self, q):
        text = (q.get("q", [""])[0] or "").strip()
        if len(text) < 2:
            raise ValueError("type at least 2 letters")
        try:
            return {"results": geocode(text[:120])}
        except Exception as e:
            raise ValueError(f"place search failed: {e}") from None

    def refresh_prices(self, _q, _body=None):
        return {"stored": self.prices.refresh()}

    # ---------------- settings ----------------
    def settings(self, _q):
        n = self.cfg.notify.as_dict()
        suggested = not n["topic"]
        if suggested:          # suggestion only; stored when you save
            n["topic"] = "sahkovahti-" + secrets.token_hex(5)
        nf = self.notifier
        return {
            "values": {"tariff": self.cfg.tariff.as_dict(), "traffic_light": self.cfg.light.as_dict(),
                       "notifications": n, "weather": self.cfg.weather.as_dict(),
                       "calendar": self.cfg.calendar.as_dict()},
            "saved": self._saved_settings(),
            "now": self._preview(self.cfg.tariff, self.cfg.light),
            "notifier": {"last_sent": nf.last_sent, "last_error": nf.last_error} if nf else None,
            "topic_suggested": suggested,
        }

    def _overrides(self, body) -> dict:
        if not isinstance(body, dict):
            raise ValueError("expected a JSON object")
        return {k: body[k] for k in SECTIONS if isinstance(body.get(k), dict)}

    def _preview(self, tariff, light) -> dict:
        now = int(time.time())
        spot = analysis.spot_at(now, tariff, self.db)
        b = tariff.breakdown(spot, now)
        today = datetime.now(self.cfg.tz).date()
        b["monthly_fixed_eur"] = round(tariff.monthly_fixed_eur(today), 2)
        b["retailer_monthly_now"] = tariff.retailer_monthly_at(today)
        b["intro_fee_active"] = (tariff.retailer_monthly_promo_eur is not None
                                 and b["retailer_monthly_now"] == tariff.retailer_monthly_promo_eur)
        b["light"] = light.level(tariff.energy_c_kwh(spot, now) if light.basis == "spot" else b["total"])
        return b

    def preview_settings(self, _q, body):
        try:
            tariff, light, *_ = self.cfg.build(self._overrides(body))
        except TypeError as e:
            raise ValueError(str(e)) from None
        return self._preview(tariff, light)

    def test_notification(self, _q, body):
        """Send a test message with the settings currently in the form (saved or not)."""
        o = self._overrides(body)
        o.setdefault("notifications", {})["enabled"] = True     # testing works before switching on
        try:
            _t, _l, n, *_ = self.cfg.build(o)
        except TypeError as e:
            raise ValueError(str(e)) from None
        try:
            send(n, "Sähkövahti test", "Notifications work. You'll get a message when the light changes.",
                 ["white_check_mark"])
        except Exception as e:
            raise ValueError(f"could not send to {n.server}: {e}") from None
        return {"ok": True, "topic": n.topic}

    def save_settings(self, _q, body):
        o = self._overrides(body)
        try:
            self.cfg.build(o)          # validate before storing anything
        except TypeError as e:
            raise ValueError(str(e)) from None
        self.db.set_meta(SETTINGS_KEY, json.dumps(o))
        self.cfg.apply(o)
        log.info("settings saved")
        return self.settings(None)

    def reset_settings(self, _q, _body=None):
        self.db.set_meta(SETTINGS_KEY, "{}")
        self.cfg.apply({})
        return self.settings(None)

    ROUTES = {
        "/api/live": "live", "/api/day": "day", "/api/history": "history",
        "/api/power": "power", "/api/cheapest": "cheapest", "/api/status": "status",
        "/api/settings": "settings", "/api/weather": "weather", "/api/outlook": "outlook",
        "/api/calendar": "calendar",
        "/api/geocode": "geocode",
    }
    POST_ROUTES = {
        "/api/prices/refresh": "refresh_prices", "/api/settings": "save_settings",
        "/api/settings/preview": "preview_settings", "/api/settings/reset": "reset_settings",
        "/api/settings/test-notification": "test_notification",
    }


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Sahkovahti/1.0"

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, code, body: bytes, ctype: str, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 65536:
                raise ValueError("request body too large")
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                raise ValueError("request body is not valid JSON") from None

        def _dispatch(self, routes, post=False):
            u = urlparse(self.path)
            name = routes.get(u.path)
            if not name:
                return False
            try:
                q = parse_qs(u.query)
                fn = getattr(app, name)
                self._json(200, fn(q, self._body()) if post else fn(q))
            except ValueError as e:
                self._json(400, {"error": str(e)})
            except Exception as e:
                log.exception("api error")
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return True

        def do_GET(self):
            if self._dispatch(app.ROUTES):
                return
            path = urlparse(self.path).path
            rel = PAGES.get(path) or path.lstrip("/").removeprefix("static/")
            f = (STATIC / rel).resolve()
            if STATIC.resolve() not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, f.read_bytes(), ctype, "no-cache")

        def do_POST(self):
            # JSON-only: browsers can't send that cross-site without a CORS preflight (which we
            # never answer), so a random web page can't change settings on your home network.
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if urlparse(self.path).path.startswith("/api/settings") and ctype != "application/json":
                return self._json(415, {"error": "Content-Type must be application/json"})
            if not self._dispatch(app.POST_ROUTES, post=True):
                self._send(404, b"not found", "text/plain")

    return Handler


def serve(app: App):
    httpd = ThreadingHTTPServer((app.cfg.host, app.cfg.port), make_handler(app))
    httpd.daemon_threads = True
    log.info("dashboard on http://%s:%d", app.cfg.host, app.cfg.port)
    httpd.serve_forever()
