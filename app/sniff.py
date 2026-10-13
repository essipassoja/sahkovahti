"""First-day hardware check: print what the meter sends, without starting the dashboard.

    python3 -m app.sniff --serial /dev/ttyUSB0
    python3 -m app.sniff --tcp 192.168.1.50:23
    python3 -m app.sniff --file captured.txt        (replay a saved capture)

Shows each raw telegram, whether its CRC is valid, and the values the app will use.
If nothing printable appears (only gibberish), your meter probably sends binary
DLMS/COSEM frames instead of ASCII — see the README section "If your meter sends binary".
"""
import argparse
import socket
import sys
import time

from .meter.p1 import TelegramAssembler, check_crc, parse_telegram


def chunks(args):
    if args.serial:
        import serial
        with serial.Serial(args.serial, args.baud, timeout=15) as s:
            while True:
                c = s.read(s.in_waiting or 1)
                if not c:
                    print("…no data for 15 s (is the HAN port enabled by your grid company?)", file=sys.stderr)
                yield c
    elif args.tcp:
        host, port = args.tcp.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=15) as s:
            while True:
                c = s.recv(4096)
                if not c:
                    return
                yield c
    else:
        data = open(args.file, "rb").read()
        for i in range(0, len(data), 64):
            yield data[i:i + 64]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--serial")
    g.add_argument("--tcp", help="host:port")
    g.add_argument("--file")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--save", help="also append raw bytes to this file")
    args = ap.parse_args()

    asm = TelegramAssembler()
    raw_seen = 0
    out = open(args.save, "ab") if args.save else None
    for c in chunks(args):
        raw_seen += len(c)
        if out:
            out.write(c)
            out.flush()
        for tg in asm.feed(c):
            print("=" * 60, time.strftime("%H:%M:%S"))
            print(tg.strip())
            ok = check_crc(tg.encode())
            r = parse_telegram(tg)
            print(f"-- CRC: {'OK' if ok else 'none' if ok is None else 'BAD'} | "
                  f"import {r.import_kwh} kWh | export {r.export_kwh} kWh | "
                  f"power {r.power_w} W | export power {r.export_power_w} W")
        if raw_seen > 4096 and asm.bad_crc == 0 and not asm.buf.startswith(b"/") and raw_seen % 4096 < 64:
            print(f"(received {raw_seen} bytes but no ASCII telegram yet — binary DLMS? "
                  f"first bytes: {c[:16].hex(' ')})", file=sys.stderr)


if __name__ == "__main__":
    main()
