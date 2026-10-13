"""Spot price fetching (porssisahko.net v2, 15-minute slots)."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .config import ROOT, Config
from .db import DB

log = logging.getLogger("prices")
USER_AGENT = "sahkovahti/1.0 (home energy dashboard)"


def _iso_to_epoch(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def parse_porssisahko(payload: dict, include_vat: bool = True, vat_percent: float = 25.5):
    """Return [(start, end, spot_c_kwh_incl_vat)] from a porssisahko.net v2 payload.

    The API's endDate is inclusive (…14:59.999Z); we store an exclusive end.
    """
    rows = []
    for p in payload.get("prices", []):
        start = _iso_to_epoch(p["startDate"])
        end = round(datetime.fromisoformat(p["endDate"].replace("Z", "+00:00")).timestamp())
        price = float(p["price"])
        if not include_vat:
            price *= 1 + vat_percent / 100
        rows.append((start, end, round(price, 4)))
    rows.sort()
    return rows


def fetch(cfg: Config):
    pc = cfg.prices
    fixture = pc.get("fixture_file")
    if fixture:
        path = Path(fixture)
        payload = json.loads((path if path.is_absolute() else ROOT / path).read_text())
    else:
        req = urllib.request.Request(pc.get("url"), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
    return parse_porssisahko(
        payload, pc.get("prices_include_vat", True), pc.get("vat_percent", 25.5)
    )


class PriceUpdater(threading.Thread):
    """Fetches prices at start-up and then periodically.

    Tomorrow's day-ahead prices are published around 13:45–14:00 Finnish time,
    so in the afternoon we poll more often until they appear.
    """

    def __init__(self, cfg: Config, db: DB):
        super().__init__(daemon=True, name="prices")
        self.cfg, self.db = cfg, db
        self.last_ok: float | None = None
        self.last_error: str | None = None

    def refresh(self) -> int:
        rows = fetch(self.cfg)
        self.db.upsert_prices(rows)
        self.last_ok, self.last_error = time.time(), None
        log.info("stored %d price slots", len(rows))
        return len(rows)

    def _next_wait(self) -> int:
        base = int(self.cfg.prices.get("refresh_minutes", 30)) * 60
        now = datetime.now(self.cfg.tz)
        last_end = self.db.last_price_end() or 0
        tomorrow_end = datetime(now.year, now.month, now.day, tzinfo=self.cfg.tz).timestamp() + 2 * 86400
        if now.hour >= 14 and last_end < tomorrow_end - 3600:
            return min(base, 600)  # waiting for tomorrow's prices: poll every 10 min
        return base

    def run(self):
        while True:
            try:
                self.refresh()
            except Exception as e:  # network down etc. — keep going
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("price fetch failed: %s", self.last_error)
                time.sleep(120)
                continue
            time.sleep(self._next_wait())
