"""P1 / HAN telegram parser for Finnish (and Swedish/Danish) smart meters.

Finnish meters with a HAN port (Aidon, Kaifa, Landis+Gyr, …) send a DSMR-style ASCII
telegram on the RJ12 port at 115200 baud 8N1, typically every 1–10 seconds:

    /ADN9 6534
    0-0:1.0.0(261013081530S)
    1-0:1.8.0(00012345.678*kWh)      cumulative imported active energy
    1-0:2.8.0(00000000.000*kWh)      cumulative exported active energy
    1-0:1.7.0(0001.234*kW)           instantaneous import power
    1-0:2.7.0(0000.000*kW)           instantaneous export power
    1-0:21.7.0(0000.456*kW)          L1 import power (41.7.0 = L2, 61.7.0 = L3)
    1-0:32.7.0(230.1*V)              L1 voltage (52.7.0 L2, 72.7.0 L3)
    1-0:31.7.0(001.9*A)              L1 current (51.7.0 L2, 71.7.0 L3)
    !ABCD                            CRC16 over everything from '/' to '!' inclusive

Some meters/readers use the binary DLMS/COSEM (HDLC) format instead. If yours does,
use an ESP32 reader that decodes it and publishes JSON over MQTT (see README).
"""
from __future__ import annotations

import re
import time
from typing import Iterator

from .base import Reading

LINE_RE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)\((.*)\)\s*$")
VALUE_RE = re.compile(r"^([-+]?\d+(?:\.\d+)?)(?:\*(\w+))?$")

# OBIS code -> (field name, unit we want)
OBIS = {
    "1-0:1.8.0": "import_kwh",
    "1-0:2.8.0": "export_kwh",
    "1-0:1.7.0": "power_kw",
    "1-0:2.7.0": "export_power_kw",
    "1-0:21.7.0": "l1_kw", "1-0:41.7.0": "l2_kw", "1-0:61.7.0": "l3_kw",
    "1-0:22.7.0": "l1_export_kw", "1-0:42.7.0": "l2_export_kw", "1-0:62.7.0": "l3_export_kw",
    "1-0:32.7.0": "l1_v", "1-0:52.7.0": "l2_v", "1-0:72.7.0": "l3_v",
    "1-0:31.7.0": "l1_a", "1-0:51.7.0": "l2_a", "1-0:71.7.0": "l3_a",
}
UNIT_SCALE = {"wh": 0.001, "kwh": 1.0, "w": 0.001, "kw": 1.0, "v": 1.0, "a": 1.0}


def crc16(data: bytes) -> int:
    """CRC-16/ARC (poly 0x8005 reflected = 0xA001, init 0), as used by DSMR 5 / P1."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def check_crc(telegram: bytes) -> bool | None:
    """True/False if the telegram has a CRC, None if it has none (older formats)."""
    i = telegram.rfind(b"!")
    if i < 0:
        return False
    given = telegram[i + 1 : i + 5].strip()
    if not given:
        return None
    try:
        return int(given, 16) == crc16(telegram[: i + 1])
    except ValueError:
        return False


def parse_telegram(text: str, ts: float | None = None) -> Reading:
    values: dict[str, float] = {}
    meter_id = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("/"):
            meter_id = line[1:]
            continue
        m = LINE_RE.match(line)
        if not m or m.group(1) not in OBIS:
            continue
        # some codes have several (…)(…) groups — the value is the last one
        payload = m.group(2).split(")(")[-1]
        v = VALUE_RE.match(payload)
        if not v:
            continue
        number = float(v.group(1))
        unit = (v.group(2) or "").lower()
        number *= UNIT_SCALE.get(unit, 1.0)
        values[OBIS[m.group(1)]] = number

    extra = {k: v for k, v in values.items() if k[:2] in ("l1", "l2", "l3")}
    if meter_id:
        extra["meter"] = meter_id
    return Reading(
        ts=ts if ts is not None else time.time(),
        import_kwh=values.get("import_kwh"),
        export_kwh=values.get("export_kwh"),
        power_w=values["power_kw"] * 1000 if "power_kw" in values else None,
        export_power_w=values["export_power_kw"] * 1000 if "export_power_kw" in values else None,
        extra=extra,
    )


class TelegramAssembler:
    """Feed raw bytes from serial/TCP; yields complete, CRC-checked telegrams."""

    def __init__(self, require_crc: bool = False):
        self.buf = bytearray()
        self.require_crc = require_crc
        self.bad_crc = 0

    def feed(self, chunk: bytes) -> Iterator[str]:
        self.buf.extend(chunk)
        while True:
            start = self.buf.find(b"/")
            if start < 0:
                self.buf.clear()
                return
            if start:
                del self.buf[:start]
            bang = self.buf.find(b"!")
            if bang < 0:
                if len(self.buf) > 8192:  # garbage, resync
                    del self.buf[:1]
                    continue
                return
            nl = self.buf.find(b"\n", bang)
            if nl < 0:
                return
            telegram = bytes(self.buf[: nl + 1])
            del self.buf[: nl + 1]
            ok = check_crc(telegram)
            if ok is False or (ok is None and self.require_crc):
                self.bad_crc += 1
                continue
            yield telegram.decode("ascii", errors="replace")
