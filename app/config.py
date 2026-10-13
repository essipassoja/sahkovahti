"""Configuration loading (config.toml, stdlib tomllib)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Tariff:
    retailer_margin_c_kwh: float = 0.0
    transfer_c_kwh: float = 0.0
    electricity_tax_c_kwh: float = 0.0
    monthly_fixed_eur: float = 0.0

    @property
    def adders_c_kwh(self) -> float:
        """Everything paid per kWh on top of the spot price."""
        return self.retailer_margin_c_kwh + self.transfer_c_kwh + self.electricity_tax_c_kwh

    def total_c_kwh(self, spot_c_kwh: float | None) -> float | None:
        if spot_c_kwh is None:
            return None
        return spot_c_kwh + self.adders_c_kwh


@dataclass
class TrafficLight:
    """Price levels for "is now a good time to run big loads?"."""
    green_below: float = 5.0     # c/kWh: below this → green
    red_above: float = 15.0      # c/kWh: above this → red; in between → yellow
    basis: str = "spot"          # "spot" (incl. VAT) or "total" (spot + margin, transfer, tax)

    def __post_init__(self):
        if self.basis not in ("spot", "total"):
            raise ValueError(f"[traffic_light] basis must be 'spot' or 'total', not {self.basis!r}")
        if self.green_below > self.red_above:
            raise ValueError("[traffic_light] green_below must not be greater than red_above")

    def level(self, price_c_kwh: float | None) -> str | None:
        if price_c_kwh is None:
            return None
        if price_c_kwh < self.green_below:
            return "green"
        if price_c_kwh > self.red_above:
            return "red"
        return "yellow"


@dataclass
class Config:
    raw: dict
    tz: ZoneInfo
    db_path: Path
    host: str
    port: int
    tariff: Tariff
    light: TrafficLight = field(default_factory=TrafficLight)
    prices: dict = field(default_factory=dict)
    meter: dict = field(default_factory=dict)


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.toml"
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    app = raw.get("app", {})
    db_path = Path(app.get("db_path", "data/energy.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Config(
        raw=raw,
        tz=ZoneInfo(app.get("timezone", "Europe/Helsinki")),
        db_path=db_path,
        host=app.get("host", "0.0.0.0"),
        port=int(app.get("port", 8080)),
        tariff=Tariff(**raw.get("tariff", {})),
        light=TrafficLight(**raw.get("traffic_light", {})),
        prices=raw.get("prices", {}),
        meter=raw.get("meter", {}),
    )
