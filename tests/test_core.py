"""Run: python3 -m unittest discover -s tests -v   (from the project folder)"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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
        with tempfile.TemporaryDirectory() as d:
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
        with tempfile.TemporaryDirectory() as d:
            db = DB(Path(d) / "t.db")
            db.insert_readings([Reading(ts=0, import_kwh=1), Reading(ts=900, import_kwh=2),
                                Reading(ts=9000, import_kwh=3), Reading(ts=9900, import_kwh=4)])
            out = db.energy_per_slot([0, 900, 1800])
            self.assertAlmostEqual(out[0], 1.0)
            self.assertIsNone(out[1])  # 2.25 h gap without readings


if __name__ == "__main__":
    unittest.main()
