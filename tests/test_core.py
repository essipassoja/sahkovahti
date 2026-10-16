"""Run: python3 -m unittest discover -s tests -v   (from the project folder)"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.analysis import cheapest_windows, day_report, spot_at, spot_prices, traffic_light  # noqa: E402
from datetime import date, datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.config import Config, Notifications, Tariff, TrafficLight  # noqa: E402
from app.db import DB  # noqa: E402
from app.meter.base import Reading  # noqa: E402
from app.meter.p1 import TelegramAssembler, check_crc, crc16, parse_telegram  # noqa: E402
from app.prices import parse_porssisahko  # noqa: E402
from app.weather import parse_warnings, summarize  # noqa: E402

BODY = (
    "/ADN9 6534\r\n"
    "\r\n"
    "0-0:1.0.0(261013081530S)\r\n"
    "1-0:1.8.0(00012345.678*kWh)\r\n"
    "1-0:2.8.0(00000012.500*kWh)\r\n"
    "1-0:1.7.0(0003.456*kW)\r\n"
    "1-0:2.7.0(0000.000*kW)\r\n"
    "1-0:21.7.0(0001.500*kW)\r\n"
    "1-0:41.7.0(0001.200*kW)\r\n"
    "1-0:61.7.0(0000.756*kW)\r\n"
    "1-0:32.7.0(231.4*V)\r\n"
    "1-0:52.7.0(229.8*V)\r\n"
    "1-0:72.7.0(230.6*V)\r\n"
    "1-0:31.7.0(006.5*A)\r\n"
    "1-0:51.7.0(005.2*A)\r\n"
    "1-0:71.7.0(003.3*A)\r\n"
    "!"
)


def telegram(body=BODY) -> bytes:
    return (body + f"{crc16(body.encode()):04X}\r\n").encode()


class P1Tests(unittest.TestCase):
    def test_crc_reference_value(self):
        self.assertEqual(crc16(b"123456789"), 0xBB3D)  # CRC-16/ARC check value

    def test_crc_valid_and_corrupt(self):
        tg = telegram()
        self.assertTrue(check_crc(tg))
        self.assertFalse(check_crc(tg.replace(b"12345.678", b"12345.679")))

    def test_parse(self):
        r = parse_telegram(telegram().decode(), ts=1000)
        self.assertAlmostEqual(r.import_kwh, 12345.678)
        self.assertAlmostEqual(r.export_kwh, 12.5)
        self.assertAlmostEqual(r.power_w, 3456)
        self.assertEqual(r.extra["l1_v"], 231.4)
        self.assertEqual(r.extra["l3_a"], 3.3)
        self.assertEqual(r.extra["meter"], "ADN9 6534")

    def test_assembler_handles_split_chunks_and_noise(self):
        stream = b"garbage\x00\xff" + telegram() + telegram()[:50]
        asm = TelegramAssembler()
        out = []
        for i in range(0, len(stream), 7):  # feed in small chunks like a serial port
            out += list(asm.feed(stream[i:i + 7]))
        self.assertEqual(len(out), 1)
        out += list(asm.feed(telegram()[50:]))
        self.assertEqual(len(out), 2)

    def test_assembler_drops_bad_crc(self):
        asm = TelegramAssembler()
        bad = telegram().replace(b"231.4", b"231.5")
        self.assertEqual(list(asm.feed(bad)), [])
        self.assertEqual(asm.bad_crc, 1)


class PriceTests(unittest.TestCase):
    def test_parse_fixture(self):
        payload = json.loads((ROOT / "tests/fixtures/prices_2026-10-13.json").read_text())
        rows = parse_porssisahko(payload)
        self.assertEqual(len(rows), 100)
        self.assertTrue(all(e - s == 900 for s, e, _ in rows))  # 15-min slots, exclusive end
        self.assertEqual(rows, sorted(rows))

    def test_vat_conversion(self):
        p = {"prices": [{"price": 10, "startDate": "2026-01-01T00:00:00.000Z",
                         "endDate": "2026-01-01T00:14:59.999Z"}]}
        self.assertAlmostEqual(parse_porssisahko(p, include_vat=False)[0][2], 12.55)


class EnergyTests(unittest.TestCase):
    def test_interpolated_slot_energy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            # constant 2 kW, readings every 7 s (not aligned to slots)
            kwh, rs = 100.0, []
            for t in range(0, 3600 + 7, 7):
                rs.append(Reading(ts=t, import_kwh=kwh + 2 * t / 3600, power_w=2000))
            db.insert_readings(rs)
            out = db.energy_per_slot([0, 900, 1800, 2700, 3600])
            for v in out:
                self.assertAlmostEqual(v, 0.5, places=3)  # 2 kW * 15 min

    def test_gap_gives_none(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            db.insert_readings([Reading(ts=0, import_kwh=1), Reading(ts=900, import_kwh=2),
                                Reading(ts=9000, import_kwh=3), Reading(ts=9900, import_kwh=4)])
            out = db.energy_per_slot([0, 900, 1800])
            self.assertAlmostEqual(out[0], 1.0)
            self.assertIsNone(out[1])  # 2.25 h gap without readings

    def test_compact_keeps_slot_energy(self):
        import random
        rnd = random.Random(1)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            t, kwh, rs = 0, 100.0, []
            while t < 3 * 86400:
                kwh += rnd.random() * 0.01
                rs.append(Reading(ts=t, import_kwh=kwh, export_kwh=kwh / 10 if t % 3 else None,
                                  power_w=1000, extra={"l1_v": 230.0}))
                t += rnd.choice([1, 7, 10, 13] * 50 + [900, 1800]) if t < 2 * 86400 else rnd.randint(1, 30)
                if rnd.random() < 0.002:
                    t += 5000   # outage longer than max_gap
            db.insert_readings(rs)
            bounds = list(range(0, 3 * 86400 + 900, 900))
            before = {c: db.energy_per_slot(bounds, c) for c in ("import_kwh", "export_kwh")}
            n_all = db.reading_count()
            deleted = db.compact(2 * 86400 + 123)
            db.reclaim_space()
            self.assertGreater(deleted, n_all // 2)
            self.assertEqual(db.reading_count(), n_all - deleted)
            for c in before:
                self.assertEqual(db.energy_per_slot(bounds, c), before[c])
            self.assertEqual(db._q("PRAGMA auto_vacuum")[0][0], 2)
            # recent readings keep full detail
            self.assertEqual(db._q("SELECT COUNT(*) FROM readings WHERE ts>=?", (2 * 86400,))[0][0],
                             sum(1 for r in rs if r.ts >= 2 * 86400))
            self.assertEqual(db.compact(2 * 86400), 0)   # running again changes nothing

    def test_old_database_switches_to_incremental_vacuum(self):
        import sqlite3
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = Path(d) / "t.db"
            c = sqlite3.connect(path)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE readings (ts INTEGER PRIMARY KEY, import_kwh REAL, export_kwh REAL,"
                      " power_w REAL, export_power_w REAL, extra TEXT)")
            c.executemany("INSERT INTO readings VALUES(?,?,?,?,?,?)",
                          [(t, t / 1000, None, 1.0, 0.0, '{"l1_v": 230.0}' * 5) for t in range(0, 86400, 10)])
            c.commit()
            c.close()
            db = DB(path)
            self.assertEqual(db._q("PRAGMA auto_vacuum")[0][0], 0)
            db.compact(86400)
            db.reclaim_space()
            self.assertEqual(db._q("PRAGMA auto_vacuum")[0][0], 2)
            self.assertEqual(db._q("SELECT COUNT(*) FROM readings WHERE extra IS NOT NULL")[0][0], 0)
            self.assertLess(path.stat().st_size, 100_000)


class MeterServiceTests(unittest.TestCase):
    def test_readings_buffered_until_flush(self):
        from app.meter import MeterService
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            from types import SimpleNamespace
            cfg = SimpleNamespace(tz=ZoneInfo("Europe/Helsinki"),
                                  meter={"source": "simulator", "simulator_backfill_days": 0, "store_interval_s": 10})
            m = MeterService(cfg, db)
            for t in range(1000, 1100, 5):
                m.on_reading(Reading(ts=t, import_kwh=t / 100, power_w=500, extra={"l1_v": 230.0}))
            self.assertEqual(db.reading_count(), 0)
            self.assertEqual(m.pending_power(0, 60), [(960, 500.0, None), (1020, 500.0, None), (1080, 500.0, None)])
            m.flush()
            self.assertEqual(db.reading_count(), 10)
            self.assertEqual(db.latest_reading()["extra"], {})
            self.assertEqual(m._pending, [])


class TrafficLightTests(unittest.TestCase):
    def test_levels_and_boundaries(self):
        tl = TrafficLight(green_below=5, red_above=15)
        self.assertEqual(tl.level(4.99), "green")
        self.assertEqual(tl.level(5), "yellow")
        self.assertEqual(tl.level(15), "yellow")
        self.assertEqual(tl.level(15.01), "red")
        self.assertIsNone(tl.level(None))

    def test_invalid_config(self):
        with self.assertRaises(ValueError):
            TrafficLight(basis="whatever")
        with self.assertRaises(ValueError):
            TrafficLight(green_below=20, red_above=10)

    def _run(self, prices, light=None, tariff=None):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            db.upsert_prices([(i * 900, (i + 1) * 900, p) for i, p in enumerate(prices)])
            cfg = Config(raw={}, tz=None, db_path=None, host="", port=0,
                         tariff=tariff or Tariff(), light=light or TrafficLight())
            return traffic_light(cfg, db, now=100)

    def test_next_change_and_next_green(self):
        r = self._run([10, 10, 20, 3, 3])
        self.assertEqual(r["level"], "yellow")
        self.assertEqual(r["next"], {"level": "red", "at": 1800})
        self.assertEqual(r["next_green"], 2700)

    def test_stays_green(self):
        r = self._run([1, 2, 3])
        self.assertEqual(r["level"], "green")
        self.assertIsNone(r["next"])
        self.assertIsNone(r["next_green"])

    def test_no_current_price(self):
        r = self._run([])
        self.assertIsNone(r["level"])

    def test_total_basis_adds_tariff(self):
        r = self._run([3], light=TrafficLight(basis="total"), tariff=Tariff(transfer_c_kwh=4))
        self.assertEqual(r["level"], "yellow")   # 3 c + 4 c transfer × 1.255 VAT = 8.02 c
        self.assertAlmostEqual(r["price"], 8.02)


HEL = ZoneInfo("Europe/Helsinki")


def ts(*args):
    return datetime(*args, tzinfo=HEL).timestamp()


class TariffTests(unittest.TestCase):
    def test_discount_and_negative_as_zero(self):
        t = Tariff(spot_discount_percent=30, negative_spot_as_zero=True, tz=HEL)
        now = ts(2026, 10, 13, 12)
        self.assertAlmostEqual(t.energy_c_kwh(10, now), 7)
        self.assertEqual(t.energy_c_kwh(-5, now), 0)
        self.assertAlmostEqual(Tariff(spot_discount_percent=30).energy_c_kwh(-5, now), -3.5)
        self.assertIsNone(t.energy_c_kwh(None, now))
        self.assertAlmostEqual(t.energy_c_kwh(10, now, discount=False), 10)
        self.assertEqual(t.energy_c_kwh(-5, now, discount=False), 0)

    def test_discount_until_is_inclusive_local_date(self):
        t = Tariff(spot_discount_percent=30, discount_until="2026-10-31", tz=HEL)
        self.assertAlmostEqual(t.energy_c_kwh(10, ts(2026, 10, 31, 23, 45)), 7)
        self.assertAlmostEqual(t.energy_c_kwh(10, ts(2026, 11, 1, 0, 0)), 10)

    def test_total(self):
        t = Tariff(spot_discount_percent=30, retailer_margin_c_kwh=0.5, transfer_c_kwh=4,
                   electricity_tax_c_kwh=2.827, tz=HEL)
        # stored spot includes VAT; margin, transfer and tax are entered without VAT
        self.assertAlmostEqual(t.total_c_kwh(10, ts(2026, 10, 13, 12)), 7 + (0.5 + 4 + 2.827) * 1.255)

    def test_vat_percent(self):
        now = ts(2026, 10, 13, 12)
        t = Tariff(vat_percent=24, retailer_margin_c_kwh=1, tz=HEL)
        self.assertAlmostEqual(t.energy_c_kwh(12.55, now), 10 * 1.24)   # 12.55 c incl. 25.5 % = 10 c
        self.assertAlmostEqual(t.total_c_kwh(12.55, now), 11 * 1.24)
        t0 = Tariff(vat_percent=0, retailer_monthly_eur=4, grid_monthly_eur=6, tz=HEL)
        self.assertAlmostEqual(t0.total_c_kwh(12.55, now), 10)
        self.assertAlmostEqual(Tariff(retailer_monthly_eur=4, grid_monthly_eur=6).monthly_fixed_eur(date(2026, 10, 1)), 12.55)

    def test_intro_basic_fee(self):
        t = Tariff(retailer_monthly_eur=4, retailer_monthly_promo_eur=0, retailer_monthly_promo_from="2026-10-01",
                   retailer_monthly_promo_until="2027-03-31", vat_percent=0)
        self.assertEqual(t.retailer_monthly_at(date(2026, 9, 30)), 4)
        self.assertEqual(t.retailer_monthly_at(date(2026, 10, 1)), 0)
        self.assertEqual(t.retailer_monthly_at(date(2027, 3, 31)), 0)
        self.assertEqual(t.retailer_monthly_at(date(2027, 4, 1)), 4)
        no_promo = Tariff(retailer_monthly_eur=4, retailer_monthly_promo_eur="", retailer_monthly_promo_until="2027-01-01")
        self.assertEqual(no_promo.retailer_monthly_at(date(2026, 10, 1)), 4)   # dates alone do nothing
        with self.assertRaises(ValueError):
            Tariff(retailer_monthly_promo_eur=1, retailer_monthly_promo_from="2027-01-01",
                   retailer_monthly_promo_until="2026-01-01")

    def test_breakdown_adds_up(self):
        t = Tariff(spot_discount_percent=30, retailer_margin_c_kwh=0.39, transfer_c_kwh=3.19,
                   electricity_tax_c_kwh=2.253, tz=HEL)
        b = t.breakdown(25.1, ts(2026, 10, 13, 12))
        self.assertAlmostEqual(b["spot"], 20, places=3)
        self.assertAlmostEqual(b["energy"] + b["margin"] + b["transfer"] + b["tax"] + b["vat"], b["total"], places=2)

    def test_day_night_transfer(self):
        t = Tariff(transfer_mode="day_night", transfer_peak_c_kwh=5, transfer_offpeak_c_kwh=2, tz=HEL)
        self.assertEqual(t.transfer_at(ts(2026, 10, 13, 6, 45)), 2)
        self.assertEqual(t.transfer_at(ts(2026, 10, 13, 7, 0)), 5)
        self.assertEqual(t.transfer_at(ts(2026, 10, 13, 21, 45)), 5)
        self.assertEqual(t.transfer_at(ts(2026, 10, 13, 22, 0)), 2)

    def test_hours_wrapping_midnight(self):
        t = Tariff(transfer_mode="day_night", peak_start_hour=22, peak_end_hour=6,
                   transfer_peak_c_kwh=1, transfer_offpeak_c_kwh=9, tz=HEL)
        self.assertEqual(t.transfer_at(ts(2026, 10, 13, 23)), 1)
        self.assertEqual(t.transfer_at(ts(2026, 10, 14, 5)), 1)
        self.assertEqual(t.transfer_at(ts(2026, 10, 14, 12)), 9)

    def test_seasonal_transfer(self):
        t = Tariff(transfer_mode="seasonal", transfer_peak_c_kwh=5, transfer_offpeak_c_kwh=2, tz=HEL)
        self.assertEqual(t.transfer_at(ts(2026, 10, 13, 12)), 2)   # October
        self.assertEqual(t.transfer_at(ts(2026, 12, 12, 12)), 5)   # Saturday in December
        self.assertEqual(t.transfer_at(ts(2026, 12, 13, 12)), 2)   # Sunday
        self.assertEqual(t.transfer_at(ts(2026, 12, 14, 23)), 2)   # Monday night
        self.assertEqual(t.transfer_at(ts(2027, 3, 31, 12)), 5)    # last winter day

    def test_validation(self):
        for bad in ({"spot_discount_percent": 120}, {"discount_until": "31.12.2026"},
                    {"transfer_mode": "cheap"}, {"peak_start_hour": 7.5}, {"retailer_margin_c_kwh": None},
                    {"transfer_mode": "day_night", "peak_start_hour": 7, "peak_end_hour": 7}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Tariff(**bad)


class SettingsTests(unittest.TestCase):
    def _cfg(self):
        return Config(raw={"tariff": {"transfer_c_kwh": 4.0}, "traffic_light": {}}, tz=HEL,
                      db_path=None, host="", port=0, tariff=Tariff(transfer_c_kwh=4.0, tz=HEL))

    def test_overrides_merge_with_config_toml(self):
        cfg = self._cfg()
        cfg.apply({"tariff": {"spot_discount_percent": 30}, "traffic_light": {"green_below": 3}})
        self.assertEqual(cfg.tariff.spot_discount_percent, 30)
        self.assertEqual(cfg.tariff.transfer_c_kwh, 4.0)       # kept from config.toml
        self.assertEqual(cfg.light.green_below, 3)
        cfg.apply({})
        self.assertEqual(cfg.tariff.spot_discount_percent, 0)

    def test_weather_location_override(self):
        cfg = self._cfg()
        self.assertEqual(cfg.weather.place, "Helsinki")         # default without a [weather] section
        cfg.apply({"weather": {"place": "Tampere", "latitude": 61.4978, "longitude": 23.761}})
        self.assertEqual((cfg.weather.place, cfg.weather.latitude), ("Tampere", 61.4978))
        self.assertTrue(cfg.weather.enabled)

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            self._cfg().build({"tariff": {"tz": "UTC"}})

    def test_save_persists_and_invalid_is_not_stored(self):
        from app.server import App
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            app = App(self._cfg(), db, None, None)
            app.save_settings({}, {"tariff": {"spot_discount_percent": 30}})
            with self.assertRaises(ValueError):
                app.save_settings({}, {"tariff": {"spot_discount_percent": 300}})
            self.assertEqual(app.cfg.tariff.spot_discount_percent, 30)
            fresh = App(self._cfg(), db, None, None)               # restart: settings come back
            self.assertEqual(fresh.cfg.tariff.spot_discount_percent, 30)
            fresh.reset_settings({})
            self.assertEqual(App(self._cfg(), db, None, None).cfg.tariff.spot_discount_percent, 0)

    def test_traffic_light_uses_discounted_spot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db = DB(Path(d) / "t.db")
            db.upsert_prices([(0, 900, 6.0)])
            cfg = self._cfg()
            cfg.apply({"tariff": {"spot_discount_percent": 30}})
            r = traffic_light(cfg, db, now=100)
            self.assertEqual(r["level"], "green")                 # 6 c × 0.7 = 4.2 c < 5 c
            self.assertAlmostEqual(r["price"], 4.2)


class HourlyPriceTests(unittest.TestCase):
    """Quarter prices from t=0 (02:00 Helsinki, a full hour) — hourly contracts pay the hour average."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = DB(Path(self.tmp.name) / "t.db")
        self.db.upsert_prices([(i * 900, (i + 1) * 900, p) for i, p in enumerate([2, 4, 6, 8, 20, 20, 20, 20])])

    def tearDown(self):
        self.tmp.cleanup()

    def test_validation(self):
        with self.assertRaises(ValueError):
            Tariff(price_resolution="30min")
        self.assertEqual(Tariff(price_resolution="hour").slot_s, 3600)

    def test_quarters_unchanged_by_default(self):
        t = Tariff(tz=HEL)
        self.assertEqual(spot_prices(0, 3600, t, self.db), {0: 2, 900: 4, 1800: 6, 2700: 8})
        self.assertEqual(spot_at(1000, t, self.db), 4)

    def test_hour_average_independent_of_range(self):
        t = Tariff(price_resolution="hour", tz=HEL)
        self.assertEqual(spot_prices(1800, 4500, t, self.db), {1800: 5, 2700: 5, 3600: 20})
        self.assertEqual(spot_at(2000, t, self.db), 5)

    def test_traffic_light_changes_on_full_hours(self):
        cfg = Config(raw={}, tz=HEL, db_path=None, host="", port=0,
                     tariff=Tariff(price_resolution="hour", tz=HEL), light=TrafficLight(green_below=6))
        r = traffic_light(cfg, self.db, now=100)
        self.assertEqual(r["level"], "green")                   # 5 c average, though 2 quarters cost 6–8 c
        self.assertEqual(r["next"], {"level": "red", "at": 3600})

    def test_day_report_has_hourly_slots(self):
        cfg = Config(raw={}, tz=HEL, db_path=None, host="", port=0, tariff=Tariff(price_resolution="hour", tz=HEL))
        rep = day_report(date(1970, 1, 1), cfg, self.db)
        self.assertEqual(rep["slot_s"], 3600)
        self.assertEqual(len(rep["slots"]), 24)
        self.assertEqual([s["spot"] for s in rep["slots"][:4]], [None, None, 5, 20])   # prices from 02:00

    def test_cheapest_window_is_a_billing_hour(self):
        import app.analysis as analysis
        cfg = Config(raw={}, tz=HEL, db_path=None, host="", port=0, tariff=Tariff(price_resolution="hour", tz=HEL))
        orig = analysis.time.time
        analysis.time.time = lambda: 1000
        try:
            w = cheapest_windows(cfg, self.db, hours=(1,))
        finally:
            analysis.time.time = orig
        self.assertEqual((w[0]["start"], w[0]["avg_spot"]), (0, 5))     # the current billing hour


class NotifierTests(unittest.TestCase):
    """Price slots start at t=0 (02:00 Helsinki time on 1 Jan 1970), 15 min each."""

    def setUp(self):
        import app.notify as notify
        self.notify, self.sent = notify, []
        self._orig = notify.send
        notify.send = lambda n, title, message, tags=None, priority=3: self.sent.append(title)
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = DB(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.notify.send = self._orig
        self.tmp.cleanup()

    def _notifier(self, prices, **n):
        self.db.upsert_prices([(i * 900, (i + 1) * 900, p) for i, p in enumerate(prices)])
        cfg = Config(raw={}, tz=HEL, db_path=None, host="", port=0, tariff=Tariff(tz=HEL),
                     notify=Notifications(**{"enabled": True, "topic": "t", **n}))
        return self.notify.Notifier(cfg, self.db)

    def test_not_at_startup_then_on_change(self):
        nt = self._notifier([3, 3, 3, 3, 20, 20, 3, 3, 3, 3, 20])
        self.assertIsNone(nt.check(100))                   # long green already at start-up: stay quiet
        self.assertIsNone(nt.check(1000))
        nt.check(3700)                                     # red, but on_red is off
        nt.check(5500)                                     # green 5400–9000 = 60 min
        self.assertEqual(self.sent, ["Green: good time to use electricity"])
        nt.check(6400)                                     # same green period: only once
        self.assertEqual(len(self.sent), 1)

    def test_short_period_skipped(self):
        nt = self._notifier([20, 3, 20, 20])
        nt.check(100)
        nt.check(1000)                                     # green only until 1800 (13 min left)
        self.assertEqual(self.sent, [])

    def test_red_when_enabled(self):
        nt = self._notifier([3, 20, 20, 20, 3], on_red=True)
        nt.check(100)
        nt.check(1000)
        self.assertEqual(self.sent, ["Red: electricity is expensive"])

    def test_quiet_hours_delay(self):
        nt = self._notifier([20] + [3] * 15, quiet_enabled=True, quiet_start_hour=2, quiet_end_hour=3)
        nt.check(100)
        nt.check(1000)                                     # 02:16, quiet
        self.assertEqual(self.sent, [])
        nt.check(3700)                                     # 03:01, still green → send now
        self.assertEqual(len(self.sent), 1)

    def test_disabled_sends_nothing(self):
        nt = self._notifier([20, 3, 3, 3, 3, 3], enabled=False)
        nt.check(100)
        nt.check(1000)
        self.assertEqual(self.sent, [])

    def test_failure_is_recorded(self):
        def boom(*a, **k):
            raise OSError("network down")
        self.notify.send = boom
        nt = self._notifier([20, 3, 3, 3, 3, 3])
        nt.check(100)
        nt.check(1000)
        self.assertIn("network down", nt.last_error)

    def _tomorrow(self, prices, **n):
        """Prices for all of 2 Jan 1970 (Helsinki); 'now' is 1 Jan 15:00."""
        t0 = int(datetime(1970, 1, 2, tzinfo=HEL).timestamp())
        self.db.upsert_prices([(t0 + i * 900, t0 + (i + 1) * 900, p) for i, p in enumerate(prices)])
        cfg = Config(raw={}, tz=HEL, db_path=None, host="", port=0, tariff=Tariff(tz=HEL),
                     notify=Notifications(**{"enabled": True, "topic": "t", **n}))
        return self.notify.Notifier(cfg, self.db), int(datetime(1970, 1, 1, 15, tzinfo=HEL).timestamp())

    def test_tomorrow_cheap_once(self):
        nt, now = self._tomorrow([1] * 48 + [4] * 48)          # average 2.5
        msg = nt.check_tomorrow(now)
        self.assertEqual(msg["title"], "Tomorrow is very cheap")
        self.assertIn("Average 2,5 c/kWh (min 1,0, max 4,0)", msg["message"])
        self.assertIsNone(nt.check_tomorrow(now + 60))
        self.assertIsNone(self.notify.Notifier(nt.cfg, self.db).check_tomorrow(now + 120))  # after restart
        self.assertEqual(len(self.sent), 1)

    def test_tomorrow_expensive(self):
        nt, now = self._tomorrow([10] * 48 + [32] * 48)        # average 21, though half the day is cheapish
        self.assertEqual(nt.check_tomorrow(now)["title"], "Tomorrow is very expensive")

    def test_tomorrow_ordinary_day_is_quiet(self):
        nt, now = self._tomorrow([1] * 48 + [39] * 48)         # average exactly 20: not above
        self.assertIsNone(nt.check_tomorrow(now))
        self.assertEqual(self.sent, [])

    def test_tomorrow_waits_for_full_day(self):
        nt, now = self._tomorrow([1] * 50)
        self.assertIsNone(nt.check_tomorrow(now))
        self.assertEqual(self.sent, [])

    def test_day_outlook(self):
        from app.analysis import day_outlook
        nt, _ = self._tomorrow([10] * 68 + [30, 40, 30, 30] + [10] * 4 + [25] * 4 + [32] * 20)   # average 15.9
        o = day_outlook(date(1970, 1, 2), nt.cfg, self.db)
        self.assertIsNone(o["verdict"])
        t0 = int(datetime(1970, 1, 2, tzinfo=HEL).timestamp())
        self.assertEqual([(r["start"] - t0, r["end"] - t0, r["max"]) for r in o["red"]],
                         [(17 * 3600, 18 * 3600, 40), (19 * 3600, 24 * 3600, 32)])
        nt, _ = self._tomorrow([40] * 96)
        self.assertEqual(day_outlook(date(1970, 1, 2), nt.cfg, self.db)["verdict"], "expensive")
        self.assertIsNone(day_outlook(date(1970, 1, 3), nt.cfg, self.db))     # not published

    def test_tomorrow_off(self):
        nt, now = self._tomorrow([1] * 96, on_tomorrow=False)
        self.assertIsNone(nt.check_tomorrow(now))
        self.assertEqual(self.sent, [])

    def test_validation(self):
        for bad in ({"enabled": True, "topic": ""}, {"topic": "has space"}, {"server": "ntfy.sh"},
                    {"click_url": "192.168.1.2"}, {"quiet_start_hour": 25},
                    {"tomorrow_cheap_below": 25, "tomorrow_expensive_above": 20}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Notifications(**bad)


class WeatherTest(unittest.TestCase):
    def test_summarize_now_and_every_third_hour(self):
        def entry(h, temp):
            return {"time": f"2026-10-13T{h:02d}:00:00Z", "data": {
                "instant": {"details": {"air_temperature": temp, "wind_speed": 2.0}},
                "next_1_hours": {"summary": {"symbol_code": "rain"}, "details": {"precipitation_amount": 0.4}}}}
        payload = {"properties": {"timeseries": [entry(h, float(h)) for h in range(8, 24)]}}
        now = datetime(2026, 10, 13, 9, 30, tzinfo=ZoneInfo("UTC")).timestamp()
        w = summarize(payload, now)
        self.assertEqual(w["now"]["temp"], 9.0)
        self.assertEqual(w["now"]["symbol"], "rain")
        self.assertEqual([p["temp"] for p in w["next"]], [12.0, 15.0, 18.0, 21.0])

    def test_location_settings(self):
        from app.config import WeatherSettings
        w = WeatherSettings(latitude="61.49781234", longitude=23.761)
        self.assertEqual((w.latitude, w.longitude), (61.4978, 23.761))
        for bad in ({"latitude": 91}, {"longitude": "x"}, {"enabled": "yes"}):
            with self.assertRaises(ValueError):
                WeatherSettings(**bad)

    def test_summarize_empty(self):
        self.assertEqual(summarize({}, 0), {"now": None, "next": [], "days": [], "extremes": [],
                                            "updated_at": None})

    def test_days_and_extremes(self):
        # hourly for 48 h, then 6-hourly, like MET; Helsinki is UTC+3 in October
        def entry(t, temp, wind, gust, precip, sym="rain"):
            nxt = {"summary": {"symbol_code": sym}, "details": {"precipitation_amount": precip}}
            return {"time": datetime.fromtimestamp(t, ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "data": {"instant": {"details": {"air_temperature": temp, "wind_speed": wind,
                                                     "wind_speed_of_gust": gust}},
                             "next_1_hours": nxt, "next_6_hours": nxt}}
        t0 = int(datetime(2026, 10, 13, 21, 0, tzinfo=ZoneInfo("UTC")).timestamp())   # 00.00 local
        series = [entry(t0 + h * 3600, 5.0, 4.0, 8.0, 0.2) for h in range(48)]
        series += [entry(t0 + 48 * 3600 + k * 6 * 3600, -1.0, 9.0, 23.0, 3.0, "snow") for k in range(8)]
        w = summarize({"properties": {"timeseries": series}}, t0 + 600)
        self.assertEqual([d["date"] for d in w["days"]], ["2026-10-15", "2026-10-16", "2026-10-17"])
        self.assertEqual(w["days"][0]["precip_mm"], 4.8)        # 24 × 0.2 mm, nothing counted twice
        self.assertEqual(w["days"][1]["snow_mm"], 12.0)         # 4 × 3 mm in 6-hour blocks
        self.assertEqual(w["days"][1]["gust"], 23.0)
        kinds = {e["kind"]: e for e in w["extremes"]}
        self.assertEqual(set(kinds), {"wind", "snow"})
        self.assertEqual(kinds["wind"]["gust"], 23.0)
        self.assertEqual(kinds["snow"]["total_mm"], 12.0)

    def test_warnings_for_location(self):
        def alert(ident, poly, msg="Alert", refs="", expires="2026-10-20T00:00:00+03:00", color="yellow"):
            return f"""<entry><content><alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
              <identifier>{ident}</identifier><status>Actual</status><msgType>{msg}</msgType>
              <references>{refs}</references>
              <info><language>fi-FI</language><headline>Suomeksi</headline></info>
              <info><language>en-GB</language><event>Wind warning</event><headline>{ident} warning</headline>
                <onset>2026-10-19T13:00:00+03:00</onset><expires>{expires}</expires>
                <parameter><valueName>color</valueName><value>{color}</value></parameter>
                <area><areaDesc>Somewhere</areaDesc><polygon>{poly}</polygon></area></info>
            </alert></content></entry>"""
        here = "60,24 61,24 61,26 60,26 60,24"       # box around Helsinki
        away = "65,24 66,24 66,26 65,26 65,24"
        xml = f"""<feed xmlns="http://www.w3.org/2005/Atom">
          {alert("old", here)}
          {alert("new", here, "Update", "urn:sender,old,2026-10-16T05:00:00+03:00", color="orange")}
          {alert("far", away)}
          {alert("past", here, expires="2026-10-01T00:00:00+03:00")}
          {alert("gone", here, "Cancel")}
          {alert("also", here)}
        </feed>""".encode()
        now = datetime(2026, 10, 16, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()
        ws = parse_warnings(xml, 60.1699, 24.9384, now)
        self.assertEqual([w["headline"] for w in ws], ["new warning", "also warning"])   # orange first
        self.assertEqual(ws[0]["color"], "orange")

if __name__ == "__main__":
    unittest.main()
