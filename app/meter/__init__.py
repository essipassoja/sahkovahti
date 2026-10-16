"""Meter service: runs the configured source, keeps the live value, persists readings.

To spare the SD card, readings are collected in memory and written in one transaction every
`write_interval_s`, and once a day readings older than `keep_detail_days` are thinned out
(see DB.compact) — the power chart only goes back two days.
"""
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
        self.write_interval = float(mc.get("write_interval_s", 60))
        self.keep_detail_days = float(mc.get("keep_detail_days", 3))
        self.latest: Reading | None = None
        self.recent = deque(maxlen=900)  # (ts, power_w) for the live sparkline
        self._last_stored = 0.0
        self._pending: list[Reading] = []   # stored readings not yet written to the database
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
        threading.Thread(target=self._writer, name="db-writer", daemon=True).start()

    def flush(self):
        """Write pending readings to the database (also called at shutdown)."""
        with self._lock:
            batch, self._pending = self._pending, []
        if not batch:
            return
        try:
            self.db.insert_readings(batch)
        except Exception:
            with self._lock:   # keep them for the next try
                self._pending[:0] = batch
            raise

    def pending_power(self, t0: float, bucket_s: int):
        """Like DB.power_series, for the readings still waiting to be written."""
        sums: dict[int, list] = {}
        with self._lock:
            for r in self._pending:
                if r.ts >= t0:
                    acc = sums.setdefault(int(r.ts) // bucket_s * bucket_s, [0.0, 0, 0.0, 0])
                    if r.power_w is not None:
                        acc[0] += r.power_w; acc[1] += 1
                    if r.export_power_w is not None:
                        acc[2] += r.export_power_w; acc[3] += 1
        return [(b, p / n if n else None, e / m if m else None) for b, (p, n, e, m) in sorted(sums.items())]

    def maintain(self):
        before = int(time.time() - self.keep_detail_days * 86400)
        n = self.db.compact(before)
        self.db.reclaim_space()
        self.db.set_meta("compacted_at", int(time.time()))
        log.info("thinned out %d readings older than %g days", n, self.keep_detail_days)

    def _writer(self):
        while True:
            time.sleep(self.write_interval)
            try:
                self.flush()
                if time.time() - float(self.db.get_meta("compacted_at", 0)) >= 86400:
                    self.maintain()
            except Exception:
                log.exception("database write failed")

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
                self._pending.append(r)
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
