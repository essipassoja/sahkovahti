"""Meter service: runs the configured source, keeps the live value, persists readings."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from ..config import Config
from ..db import DB
from .base import Reading
from .sources import SOURCES, SimulatorSource

log = logging.getLogger("meter")


class MeterService:
    def __init__(self, cfg: Config, db: DB):
        self.cfg, self.db = cfg, db
        mc = cfg.meter
        self.kind = mc.get("source", "simulator")
        self.store_interval = float(mc.get("store_interval_s", 10))
        self.latest: Reading | None = None
        self.recent = deque(maxlen=900)  # (ts, power_w) for the live sparkline
        self._last_stored = 0.0
        self._lock = threading.Lock()

        cls = SOURCES.get(self.kind)
        if cls is None:
            raise ValueError(f"unknown meter source {self.kind!r}; choose from {list(SOURCES)}")
        if cls is SimulatorSource:
            self.source = SimulatorSource({}, self.on_reading, cfg.tz, db=db)
            days = int(mc.get("simulator_backfill_days", 14))
            if days and db.get_meta("simulator_backfilled") is None and db.reading_count() == 0:
                self.source.backfill(days)
                db.set_meta("simulator_backfilled", int(time.time()))
        else:
            self.source = cls(mc.get(self.kind, {}), self.on_reading, cfg.tz)

    def start(self):
        self.source.start()

    def on_reading(self, r: Reading):
        with self._lock:
            # a meter counter never goes backwards; ignore glitches
            if (self.latest and r.import_kwh is not None and self.latest.import_kwh is not None
                    and r.import_kwh < self.latest.import_kwh - 0.001):
                log.warning("counter went backwards (%s -> %s), ignored", self.latest.import_kwh, r.import_kwh)
                return
            self.latest = r
            if r.power_w is not None:
                self.recent.append((r.ts, r.power_w))
            if r.ts - self._last_stored >= self.store_interval:
                self.db.insert_reading(r)
                self._last_stored = r.ts

    def status(self) -> dict:
        age = time.time() - self.latest.ts if self.latest else None
        return {
            "source": self.kind,
            "connected": bool(self.source.connected and age is not None and age < 60),
            "last_reading_age_s": round(age, 1) if age is not None else None,
            "error": self.source.last_error,
            "simulated": self.kind == "simulator",
        }
