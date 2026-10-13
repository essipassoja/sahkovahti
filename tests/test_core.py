"""Run: python3 -m unittest discover -s tests -v   (from the project folder)"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.analysis import traffic_light  # noqa: E402
from datetime import date, datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from app.config import Config, Tariff, TrafficLight  # noqa: E402
from app.db import DB  # noqa: E402
from app.meter.base import Reading  # noqa: E402
from app.meter.p1 import TelegramAssembler, check_crc, crc16, parse_telegram  # noqa: E402
from app.prices import parse_porssisahko  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
