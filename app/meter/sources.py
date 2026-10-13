"""Meter data sources. Each runs in its own thread and calls `emit(Reading)`.

simulator  – realistic fake house, so the app is usable before the HAN hardware arrives
p1_serial  – USB HAN/P1 cable plugged into this computer (needs: pip install pyserial)
p1_tcp     – Wi-Fi HAN reader exposing the raw telegram on a TCP port
mqtt       – Wi-Fi HAN reader publishing to an MQTT broker (needs: pip install paho-mqtt)
"""
from __future__ import annotations

import json
import logging
import math
import random
import socket
import threading
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from .base import Reading
from .p1 import TelegramAssembler, parse_telegram

log = logging.getLogger("meter")
Emit = Callable[[Reading], None]


class Source(threading.Thread):
    name_ = "source"

    def __init__(self, conf: dict, emit: Emit, tz: ZoneInfo):
        super().__init__(daemon=True, name=self.name_)
        self.conf, self.emit, self.tz = conf, emit, tz
        self.connected = False
        self.last_error: str | None = None

    def run(self):
        backoff = 2
        while True:
            try:
                self.loop()
                backoff = 2
            except Exception as e:
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("%s: %s — retrying in %ss", self.name_, self.last_error, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def loop(self):
        raise NotImplementedError


# --------------------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------------------
class SimulatedHouse:
    """Detached house with direct electric heating, a 300 L water heater on a night timer,
    cooking peaks, and sauna on Fridays/Saturdays. Deterministic for a given minute, so the
    backfilled history and live data look the same."""

    def __init__(self, tz: ZoneInfo, seed: int = 42):
        self.tz, self.seed = tz, seed

    def power_w(self, t: float) -> float:
        lt = datetime.fromtimestamp(t, self.tz)
        h = lt.hour + lt.minute / 60
        minute = int(t // 60)
        rnd = random.Random(minute * 7919 + self.seed)

        p = 280 + 90 * (1 if (t % 2400) < 900 else 0)          # base + fridge/freezer cycling
        # electric heating: thermostat cycles, colder at night
        duty = 0.32 + 0.14 * math.cos(2 * math.pi * (h - 4) / 24)
        if (t % 1200) / 1200 < duty:
            p += 2800
        # water heater, night timer 22:00–00:15
        if h >= 22 or h < 0.25:
            p += 3000
        # morning: coffee, kettle, shower pump
        if 6.5 <= h < 8:
            p += 250 + (1800 if rnd.random() < 0.12 else 0)
        # cooking
        if 16.5 <= h < 18:
            p += 600 + (1700 if rnd.random() < 0.45 else 0)
        # evening TV, lights, laundry sometimes
        if 18 <= h < 23:
            p += 350
            if lt.weekday() in (1, 4) and 19 <= h < 20.5:
                p += 1900  # washing machine + dryer
        # sauna Fri/Sat
        if lt.weekday() in (4, 5) and 19 <= h < 20.75:
            p += 6000 if h < 19.8 else 3000
        # daylight: fewer lights
        if 9 <= h < 16:
            p -= 60
        return max(120.0, p * (0.95 + 0.1 * rnd.random()))

    def reading(self, t: float, import_kwh: float) -> Reading:
        p = self.power_w(t)
        rnd = random.Random(int(t) + self.seed)
        split = (0.42, 0.33, 0.25)
        extra = {}
        for i, s in enumerate(split, 1):
            v = 230 + rnd.uniform(-2.5, 2.5)
            extra[f"l{i}_v"] = round(v, 1)
            extra[f"l{i}_kw"] = round(p * s / 1000, 3)
            extra[f"l{i}_a"] = round(p * s / v, 2)
        extra["meter"] = "SIMULATOR"
        return Reading(ts=t, import_kwh=round(import_kwh, 4), export_kwh=0.0,
                       power_w=round(p, 1), export_power_w=0.0, extra=extra)


class SimulatorSource(Source):
    name_ = "simulator"

    def __init__(self, conf, emit, tz, db=None, interval_s: float = 5.0):
        super().__init__(conf, emit, tz)
        self.house = SimulatedHouse(tz)
        self.db = db
        self.interval = interval_s

    def backfill(self, days: int) -> float:
        """Generate `days` of 1-minute history ending now. Returns the final counter value."""
        now = time.time()
        t = int(now // 60 * 60) - days * 86400
        kwh = 12000.0
        batch = []
        while t < now:
            p = self.house.power_w(t)
            batch.append(self.house.reading(t, kwh))
            kwh += p * 60 / 3_600_000
            t += 60
            if len(batch) >= 5000:
                self.db.insert_readings(batch)
                batch = []
        if batch:
            self.db.insert_readings(batch)
        log.info("simulator: backfilled %d days", days)
        return kwh

    def loop(self):
        last = self.db.latest_reading() if self.db else None
        kwh = last["import_kwh"] if last and last["import_kwh"] is not None else 12000.0
        t_prev = last["ts"] if last else time.time()
        if time.time() - t_prev > 6 * 3600:
            t_prev = time.time()  # long downtime: don't invent a huge jump
        self.connected = True
        while True:
            now = time.time()
            # integrate in ≤60 s steps so the counter matches the power curve
            t = t_prev
            while t < now:
                step = min(60, now - t)
                kwh += self.house.power_w(t) * step / 3_600_000
                t += step
            t_prev = now
            self.emit(self.house.reading(now, kwh))
            time.sleep(self.interval)


# --------------------------------------------------------------------------------------
# Real meter sources
# --------------------------------------------------------------------------------------
class P1SerialSource(Source):
    name_ = "p1_serial"

    def loop(self):
        import serial  # pip install pyserial

        port = self.conf.get("port", "/dev/ttyUSB0")
        baud = int(self.conf.get("baudrate", 115200))
        asm = TelegramAssembler()
        with serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=15) as ser:
            self.connected, self.last_error = True, None
            log.info("p1_serial: opened %s @ %d", port, baud)
            while True:
                chunk = ser.read(ser.in_waiting or 1)
                if not chunk:
                    raise TimeoutError("no data from meter in 15 s — is the HAN port enabled?")
                if self.conf.get("invert"):
                    chunk = bytes(b ^ 0xFF for b in chunk)
                for tg in asm.feed(chunk):
                    self.emit(parse_telegram(tg))


class P1TcpSource(Source):
    name_ = "p1_tcp"

    def loop(self):
        host, port = self.conf.get("host"), int(self.conf.get("port", 23))
        asm = TelegramAssembler()
        with socket.create_connection((host, port), timeout=15) as s:
            s.settimeout(30)
            self.connected, self.last_error = True, None
            log.info("p1_tcp: connected %s:%d", host, port)
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    raise ConnectionError("reader closed the connection")
                for tg in asm.feed(chunk):
                    self.emit(parse_telegram(tg))


class MqttSource(Source):
    name_ = "mqtt"

    def loop(self):
        import paho.mqtt.client as mqtt  # pip install paho-mqtt

        c = self.conf
        mode = c.get("payload", "telegram")
        fields = c.get("json_fields", {})
        done = threading.Event()

        def num(d, key, scale=1.0):
            k = fields.get(key)
            v = d.get(k) if k else None
            return float(v) * scale if v is not None else None

        def on_message(_c, _u, msg):
            try:
                text = msg.payload.decode("utf-8", errors="replace")
                if mode == "telegram":
                    r = parse_telegram(text)
                else:
                    d = json.loads(text)
                    r = Reading(ts=time.time(), import_kwh=num(d, "import_kwh"),
                                export_kwh=num(d, "export_kwh"), power_w=num(d, "power_w"),
                                export_power_w=num(d, "export_power_w"))
                self.emit(r)
            except Exception as e:
                self.last_error = f"bad payload: {e}"

        def on_connect(client, *_a, **_k):
            self.connected, self.last_error = True, None
            client.subscribe(c.get("topic", "han/telegram"))

        def on_disconnect(*_a, **_k):
            self.connected = False
            done.set()

        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:  # paho-mqtt < 2
            client = mqtt.Client()
        if c.get("username"):
            client.username_pw_set(c["username"], c.get("password") or None)
        client.on_message, client.on_connect, client.on_disconnect = on_message, on_connect, on_disconnect
        client.connect(c.get("host", "localhost"), int(c.get("port", 1883)), keepalive=60)
        client.loop_start()
        done.wait()
        client.loop_stop()
        raise ConnectionError("MQTT disconnected")


SOURCES = {
    "simulator": SimulatorSource,
    "p1_serial": P1SerialSource,
    "p1_tcp": P1TcpSource,
    "mqtt": MqttSource,
}
