"""Run with:  python3 -m app            (from the project folder)
          python3 -m app --config /path/to/config.toml
"""
import argparse
import logging

from . import config
from .db import DB
from .meter import MeterService
from .notify import Notifier
from .prices import PriceUpdater
from .server import App, serve


def main():
    ap = argparse.ArgumentParser(description="Sähkövahti – home electricity & spot price dashboard")
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    cfg = config.load(args.config)
    db = DB(cfg.db_path)
    prices = PriceUpdater(cfg, db)
    meter = MeterService(cfg, db)
    notifier = Notifier(cfg, db)
    app = App(cfg, db, meter, prices, notifier)   # loads saved settings before the notifier starts
    prices.start()
    meter.start()
    notifier.start()
    serve(app)


if __name__ == "__main__":
    main()
