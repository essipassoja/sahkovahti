"""Tiny JSON API + static file server (standard library only)."""
from __future__ import annotations

import json
import logging
import mimetypes
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import analysis
from .config import Config
from .db import DB, QUARTER
from .meter import MeterService
from .prices import PriceUpdater

STATIC = Path(__file__).parent / "static"
PAGES = {"/": "simple.html", "": "simple.html", "/details": "index.html"}
log = logging.getLogger("http")


class App:
    def __init__(self, cfg: Config, db: DB, meter: MeterService, prices: PriceUpdater):
        self.cfg, self.db, self.meter, self.prices = cfg, db, meter, prices

    # ---------------- endpoints ----------------
    def live(self, _q):
        r = self.meter.latest
        now = int(time.time())
        spot = self.db.price_at(now)
        total = self.cfg.tariff.total_c_kwh(spot)
        q_start = now - now % QUARTER
        nxt = self.db.price_at(q_start + QUARTER)
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
            "total": round(total, 3) if total is not None else None,
            "next_spot": nxt,
            "cost_rate_eur_h": round(p / 1000 * total / 100, 3) if p is not None and total is not None else None,
            "quarter": {"start": q_start, "kwh": cur["kwh"] if cur else None, "cost_eur": cur["cost_eur"] if cur else None},
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
        return {"bucket_s": bucket, "points": [(t, round(p or 0), round(e or 0)) for t, p, e in rows]}

    def cheapest(self, _q):
        return {"windows": analysis.cheapest_windows(self.cfg, self.db)}

    def status(self, _q):
        t = self.cfg.tariff
        return {
            "meter": self.meter.status(),
            "prices": {
                "last_ok": self.prices.last_ok,
                "error": self.prices.last_error,
                "known_until": self.db.last_price_end(),
            },
            "tariff": {
                "retailer_margin_c_kwh": t.retailer_margin_c_kwh,
                "transfer_c_kwh": t.transfer_c_kwh,
                "electricity_tax_c_kwh": t.electricity_tax_c_kwh,
                "adders_c_kwh": round(t.adders_c_kwh, 3),
                "monthly_fixed_eur": t.monthly_fixed_eur,
            },
            "timezone": str(self.cfg.tz),
            "readings": self.db.reading_count(),
        }

    def refresh_prices(self, _q):
        return {"stored": self.prices.refresh()}

    ROUTES = {
        "/api/live": "live", "/api/day": "day", "/api/history": "history",
        "/api/power": "power", "/api/cheapest": "cheapest", "/api/status": "status",
    }
    POST_ROUTES = {"/api/prices/refresh": "refresh_prices"}


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

        def _dispatch(self, routes):
            u = urlparse(self.path)
            name = routes.get(u.path)
            if not name:
                return False
            try:
                self._json(200, getattr(app, name)(parse_qs(u.query)))
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
            if not self._dispatch(app.POST_ROUTES):
                self._send(404, b"not found", "text/plain")

    return Handler


def serve(app: App):
    httpd = ThreadingHTTPServer((app.cfg.host, app.cfg.port), make_handler(app))
    httpd.daemon_threads = True
    log.info("dashboard on http://%s:%d", app.cfg.host, app.cfg.port)
    httpd.serve_forever()
