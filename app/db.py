"""SQLite storage. All timestamps are UTC epoch seconds (int)."""
from __future__ import annotations

import json
import sqlite3
import threading
from bisect import bisect_left
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    start   INTEGER PRIMARY KEY,   -- slot start, UTC epoch s
    end     INTEGER NOT NULL,      -- slot end (exclusive)
    spot    REAL NOT NULL          -- spot price c/kWh incl. VAT
);
CREATE TABLE IF NOT EXISTS readings (
    ts             INTEGER PRIMARY KEY,
    import_kwh     REAL,           -- cumulative counter from the meter (1.8.0)
    export_kwh     REAL,           -- cumulative counter (2.8.0)
    power_w        REAL,           -- instantaneous import (1.7.0)
    export_power_w REAL,           -- instantaneous export (2.7.0)
    extra          TEXT            -- unused since phase values are kept in memory only (always NULL)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

QUARTER = 900


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")   # takes effect on a new file only
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, fewer flushes to the SD card
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    # ---------- generic ----------
    def _q(self, sql, args=()):
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _x(self, sql, args=()):
        with self._lock, self._conn:
            self._conn.execute(sql, args)

    def _xm(self, sql, rows):
        with self._lock, self._conn:
            self._conn.executemany(sql, rows)

    def get_meta(self, key, default=None):
        r = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return r[0][0] if r else default

    def set_meta(self, key, value):
        self._x("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))

    # ---------- prices ----------
    def upsert_prices(self, rows: list[tuple[int, int, float]]):
        self._xm("INSERT OR REPLACE INTO prices(start,end,spot) VALUES(?,?,?)", rows)

    def prices_between(self, t0: int, t1: int) -> list[tuple[int, int, float]]:
        return self._q(
            "SELECT start,end,spot FROM prices WHERE end>? AND start<? ORDER BY start", (t0, t1)
        )

    def price_at(self, t: int) -> float | None:
        r = self._q("SELECT spot FROM prices WHERE start<=? AND end>? LIMIT 1", (t, t))
        return r[0][0] if r else None

    def last_price_end(self) -> int | None:
        r = self._q("SELECT MAX(end) FROM prices")
        return r[0][0] if r and r[0][0] else None

    # ---------- readings ----------
    def insert_reading(self, r) -> None:
        self.insert_readings([r])

    def insert_readings(self, readings) -> None:
        # phase values (r.extra) are not stored: only the live view shows them, from memory
        self._xm(
            "INSERT OR REPLACE INTO readings(ts,import_kwh,export_kwh,power_w,export_power_w)"
            " VALUES(?,?,?,?,?)",
            [(int(r.ts), r.import_kwh, r.export_kwh, r.power_w, r.export_power_w) for r in readings],
        )

    def latest_reading(self):
        r = self._q(
            "SELECT ts,import_kwh,export_kwh,power_w,export_power_w,extra FROM readings"
            " ORDER BY ts DESC LIMIT 1"
        )
        if not r:
            return None
        ts, imp, exp, p, ep, extra = r[0]
        return {
            "ts": ts, "import_kwh": imp, "export_kwh": exp, "power_w": p,
            "export_power_w": ep, "extra": json.loads(extra) if extra else {},
        }

    def compact(self, before: int) -> int:
        """Thin out readings older than `before`; returns the number of rows deleted.

        Per 15-min slot only the first and last reading with each counter are kept. Those are
        exactly the readings energy_per_slot interpolates between at the slot boundaries, so
        every 15-min and hourly figure stays the same; only the old power curve loses detail.
        """
        before -= before % QUARTER
        keep = " UNION ".join(
            f"SELECT {f}(ts) FROM readings WHERE ts<:b AND {c} IS NOT NULL GROUP BY ts/{QUARTER}"
            for f in ("MIN", "MAX") for c in ("import_kwh", "export_kwh")
        )
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM readings WHERE ts<:b AND ts NOT IN ({keep})", {"b": before})
            self._conn.execute("UPDATE readings SET extra=NULL WHERE extra IS NOT NULL")
            return cur.rowcount

    def reclaim_space(self) -> None:
        """Give the space of deleted rows back to the file system."""
        with self._lock:
            if self._conn.execute("PRAGMA auto_vacuum").fetchone()[0] != 2:
                # database made before auto_vacuum was on: switch once (VACUUM rewrites the file)
                self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
                self._conn.execute("VACUUM")
            else:
                self._conn.execute("PRAGMA incremental_vacuum")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def reading_count(self) -> int:
        return self._q("SELECT COUNT(*) FROM readings")[0][0]

    def power_series(self, t0: int, t1: int, bucket_s: int = 60):
        """Average power per bucket — for the live power chart."""
        return self._q(
            "SELECT (ts/?)*? AS b, AVG(power_w), AVG(export_power_w) FROM readings"
            " WHERE ts>=? AND ts<? GROUP BY b ORDER BY b",
            (bucket_s, bucket_s, t0, t1),
        )

    def counter_series(self, t0: int, t1: int, column: str = "import_kwh"):
        assert column in ("import_kwh", "export_kwh")
        return self._q(
            f"SELECT ts,{column} FROM readings WHERE ts>=? AND ts<=? AND {column} IS NOT NULL"
            " ORDER BY ts",
            (t0, t1),
        )

    # ---------- energy per slot ----------
    def energy_per_slot(self, boundaries: list[int], column="import_kwh", max_gap_s=3600):
        """kWh consumed between consecutive boundaries, from the cumulative meter counter.

        The counter value at each boundary is linearly interpolated between the nearest
        readings, so energy is attributed to the right 15-min slot even when readings
        arrive at irregular intervals. Slots with missing data return None.
        The still-running slot uses the latest reading as its end value.
        """
        if len(boundaries) < 2:
            return []
        rows = self.counter_series(boundaries[0] - max_gap_s, boundaries[-1] + max_gap_s, column)
        if not rows:
            return [None] * (len(boundaries) - 1)
        ts = [r[0] for r in rows]
        vals = [r[1] for r in rows]
        latest_ts = self._q("SELECT MAX(ts) FROM readings")[0][0]

        def at(b):
            i = bisect_left(ts, b)
            if i < len(ts) and ts[i] == b:
                return vals[i]
            if i == 0:
                return vals[0] if ts[0] - b <= 60 else None  # tiny tolerance at start
            if i == len(ts):
                # beyond the newest reading: only the still-running slot may use it
                if ts[-1] == latest_ts and b - ts[-1] <= max_gap_s:
                    return ("open", vals[-1])
                return None
            t_a, t_b = ts[i - 1], ts[i]
            if t_b - t_a > max_gap_s:
                return None
            v_a, v_b = vals[i - 1], vals[i]
            return v_a + (v_b - v_a) * (b - t_a) / (t_b - t_a)

        values = [at(b) for b in boundaries]
        out = []
        for a, b in zip(values, values[1:]):
            if a is None or b is None or isinstance(a, tuple):
                out.append(None)
                continue
            end = b[1] if isinstance(b, tuple) else b
            out.append(max(0.0, end - a))
        return out
