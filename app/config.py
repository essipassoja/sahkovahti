"""Configuration loading (config.toml, stdlib tomllib) and tariff / price rules.

Contract details (tariff, traffic light) can also be edited on the settings page; those
values are stored in the database and override the [tariff] / [traffic_light] sections here.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent

TRANSFER_MODES = ("flat", "day_night", "seasonal")
PRICE_RESOLUTIONS = {"15min": 900, "hour": 3600}   # seconds per billing slot
WINTER_MONTHS = (11, 12, 1, 2, 3)   # kausisiirto: "talviarkipäivä" = Nov–Mar, Mon–Sat


def _num(name, v, lo=None, hi=None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ValueError(f"{name} must be a number")
    try:
        v = float(v)
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return v


@dataclass
class Tariff:
    """Everything you pay per kWh. Prices and fees are entered WITHOUT VAT; vat_percent is added
    on top of all of them (spot, margin, transfer, electricity tax and basic fees)."""
    # electricity contract (sähkösopimus)
    retailer_name: str = ""
    retailer_margin_c_kwh: float = 0.0
    spot_discount_percent: float = 0.0     # e.g. 30 → you pay 70 % of the spot price
    discount_until: str = ""               # last day the discount applies (YYYY-MM-DD), "" = no end
    negative_spot_as_zero: bool = False    # negative spot prices are billed as 0
    retailer_monthly_eur: float = 0.0          # normal basic fee (perusmaksu), €/month
    retailer_monthly_promo_eur: float | None = None   # introductory basic fee, e.g. 0 € for 6 months
    retailer_monthly_promo_from: str = ""      # first day of the intro fee, "" = from the start
    retailer_monthly_promo_until: str = ""     # last day of the intro fee, "" = no end
    # "15min" = billed per 15-min spot price; "hour" = billed per hour at the average of its
    # four 15-min prices (older contracts / meters that only report hourly)
    price_resolution: str = "15min"
    # grid / transfer (sähkönsiirto)
    grid_company: str = ""
    transfer_mode: str = "flat"            # flat | day_night | seasonal
    transfer_c_kwh: float = 0.0            # flat price
    transfer_peak_c_kwh: float = 0.0       # day (day_night) or winter weekday daytime (seasonal)
    transfer_offpeak_c_kwh: float = 0.0    # night / all other times
    peak_start_hour: int = 7
    peak_end_hour: int = 22
    grid_monthly_eur: float = 0.0
    electricity_tax_c_kwh: float = 0.0     # sähkövero, a fixed c/kWh excise (VAT is added on top)
    vat_percent: float = 25.5              # arvonlisävero
    tz: ZoneInfo | None = field(default=None, repr=False, compare=False, metadata={"internal": True})
    # VAT already included in the stored spot prices ([prices] vat_percent); removed before
    # applying the discount, then your vat_percent is added back
    stored_spot_vat_percent: float = field(default=25.5, repr=False, compare=False, metadata={"internal": True})

    def __post_init__(self):
        for f in ("retailer_margin_c_kwh", "transfer_c_kwh", "transfer_peak_c_kwh",
                  "transfer_offpeak_c_kwh", "electricity_tax_c_kwh"):
            setattr(self, f, _num(f, getattr(self, f), -100, 1000))
        for f in ("retailer_monthly_eur", "grid_monthly_eur"):
            setattr(self, f, _num(f, getattr(self, f), 0, 10000))
        self.spot_discount_percent = _num("spot_discount_percent", self.spot_discount_percent, 0, 100)
        self.vat_percent = _num("vat_percent", self.vat_percent, 0, 100)
        for f in ("peak_start_hour", "peak_end_hour"):
            v = _num(f, getattr(self, f), 0, 24)
            if v != int(v):
                raise ValueError(f"{f} must be a whole hour")
            setattr(self, f, int(v))
        if self.price_resolution not in PRICE_RESOLUTIONS:
            raise ValueError(f"price_resolution must be one of {', '.join(PRICE_RESOLUTIONS)}")
        if self.transfer_mode not in TRANSFER_MODES:
            raise ValueError(f"transfer_mode must be one of {', '.join(TRANSFER_MODES)}")
        if self.transfer_mode != "flat" and self.peak_start_hour == self.peak_end_hour:
            raise ValueError("peak_start_hour and peak_end_hour must differ")
        for f in ("discount_until", "retailer_monthly_promo_from", "retailer_monthly_promo_until"):
            v = str(getattr(self, f) or "").strip()
            setattr(self, f, v)
            if v:
                try:
                    date.fromisoformat(v)
                except ValueError:
                    raise ValueError(f"{f} must be a date like 2026-12-31") from None
        if self.retailer_monthly_promo_eur in (None, ""):
            self.retailer_monthly_promo_eur = None
        else:
            self.retailer_monthly_promo_eur = _num("retailer_monthly_promo_eur", self.retailer_monthly_promo_eur, 0, 10000)
        a, b = self.retailer_monthly_promo_from, self.retailer_monthly_promo_until
        if a and b and a > b:
            raise ValueError("retailer_monthly_promo_from must not be after retailer_monthly_promo_until")
        if not isinstance(self.negative_spot_as_zero, bool):
            raise ValueError("negative_spot_as_zero must be true or false")
        self.retailer_name = str(self.retailer_name or "").strip()[:80]
        self.grid_company = str(self.grid_company or "").strip()[:80]

    # ---------- settings (de)serialisation ----------
    @classmethod
    def keys(cls) -> list[str]:
        return [f.name for f in fields(cls) if not f.metadata.get("internal")]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.keys()}

    # ---------- price rules ----------
    @property
    def slot_s(self) -> int:
        """Length of one billing slot in seconds (900 or 3600)."""
        return PRICE_RESOLUTIONS[self.price_resolution]

    @property
    def vat_factor(self) -> float:
        return 1 + self.vat_percent / 100

    def retailer_monthly_at(self, day: date) -> float:
        """Retailer basic fee on a given day (intro fee if one is set and valid), without VAT."""
        p, a, b = self.retailer_monthly_promo_eur, self.retailer_monthly_promo_from, self.retailer_monthly_promo_until
        if p is not None and (not a or day >= date.fromisoformat(a)) and (not b or day <= date.fromisoformat(b)):
            return p
        return self.retailer_monthly_eur

    def monthly_fixed_eur(self, day: date) -> float:
        """Basic fees incl. VAT for the month containing `day` (fee valid on that day)."""
        return (self.retailer_monthly_at(day) + self.grid_monthly_eur) * self.vat_factor

    def _local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, self.tz or ZoneInfo("Europe/Helsinki"))

    def discount_active(self, ts: float) -> bool:
        if not self.spot_discount_percent:
            return False
        return not self.discount_until or self._local(ts).date() <= date.fromisoformat(self.discount_until)

    def spot_ex_vat(self, spot_c_kwh: float | None) -> float | None:
        """Stored spot prices include VAT; this is the VAT-free market price."""
        return None if spot_c_kwh is None else spot_c_kwh / (1 + self.stored_spot_vat_percent / 100)

    def _energy_ex_vat(self, spot_c_kwh: float | None, ts: float, discount: bool = True) -> float | None:
        e = self.spot_ex_vat(spot_c_kwh)
        if e is None:
            return None
        if self.negative_spot_as_zero:
            e = max(e, 0.0)
        if discount and self.discount_active(ts):
            e *= 1 - self.spot_discount_percent / 100
        return e

    def energy_c_kwh(self, spot_c_kwh: float | None, ts: float, discount: bool = True) -> float | None:
        """Your spot part incl. VAT: after the 'negative = 0' rule and your discount
        (discount=False: what it would be without the discount)."""
        e = self._energy_ex_vat(spot_c_kwh, ts, discount)
        return None if e is None else e * self.vat_factor

    def transfer_period(self, ts: float) -> str:
        """'flat', 'peak' or 'offpeak' for the slot starting at ts (local time)."""
        if self.transfer_mode == "flat":
            return "flat"
        t = self._local(ts)
        a, b = self.peak_start_hour, self.peak_end_hour
        in_hours = a <= t.hour < b if a < b else (t.hour >= a or t.hour < b)
        if self.transfer_mode == "seasonal" and (t.month not in WINTER_MONTHS or t.weekday() == 6):
            return "offpeak"
        return "peak" if in_hours else "offpeak"

    def transfer_at(self, ts: float) -> float:
        """Transfer price without VAT."""
        return {"flat": self.transfer_c_kwh, "peak": self.transfer_peak_c_kwh,
                "offpeak": self.transfer_offpeak_c_kwh}[self.transfer_period(ts)]

    def adders_c_kwh(self, ts: float) -> float:
        """Margin + transfer + electricity tax at time ts, incl. VAT."""
        return (self.retailer_margin_c_kwh + self.transfer_at(ts) + self.electricity_tax_c_kwh) * self.vat_factor

    def total_c_kwh(self, spot_c_kwh: float | None, ts: float, discount: bool = True) -> float | None:
        """What you pay per kWh, incl. VAT (discount=False: without your spot discount)."""
        e = self.energy_c_kwh(spot_c_kwh, ts, discount)
        return None if e is None else e + self.adders_c_kwh(ts)

    def breakdown(self, spot_c_kwh: float | None, ts: float) -> dict:
        """Price parts without VAT, then the VAT and the total (for the settings page)."""
        e = self._energy_ex_vat(spot_c_kwh, ts)
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        ex = None if e is None else e + self.retailer_margin_c_kwh + self.transfer_at(ts) + self.electricity_tax_c_kwh
        return {
            "spot": r(self.spot_ex_vat(spot_c_kwh)), "energy": r(e), "discount_active": self.discount_active(ts),
            "margin": self.retailer_margin_c_kwh, "transfer": self.transfer_at(ts),
            "transfer_period": self.transfer_period(ts), "tax": self.electricity_tax_c_kwh,
            "vat_percent": self.vat_percent, "vat": r(None if ex is None else ex * self.vat_percent / 100),
            "total": r(self.total_c_kwh(spot_c_kwh, ts)),
        }


@dataclass
class TrafficLight:
    """Price levels for "is now a good time to run big loads?"."""
    green_below: float = 5.0     # c/kWh: below this → green
    red_above: float = 15.0      # c/kWh: above this → red; in between → yellow
    basis: str = "spot"          # "spot" = your spot price after discount, "total" = + margin, transfer, tax
    # (both incl. VAT, like the prices most Finnish apps show)

    def __post_init__(self):
        self.green_below = _num("green_below", self.green_below, -100, 1000)
        self.red_above = _num("red_above", self.red_above, -100, 1000)
        if self.basis not in ("spot", "total"):
            raise ValueError(f"basis must be 'spot' or 'total', not {self.basis!r}")
        if self.green_below > self.red_above:
            raise ValueError("green_below must not be greater than red_above")

    @classmethod
    def keys(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.keys()}

    def level(self, price_c_kwh: float | None) -> str | None:
        if price_c_kwh is None:
            return None
        if price_c_kwh < self.green_below:
            return "green"
        if price_c_kwh > self.red_above:
            return "red"
        return "yellow"


@dataclass
class Notifications:
    """Phone notifications through ntfy (https://ntfy.sh): install the ntfy app, subscribe to the topic."""
    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str = ""                # random-looking name; anyone who knows it can read the messages
    on_green: bool = True          # light turns green
    on_red: bool = False           # light turns red
    on_tomorrow: bool = True       # after tomorrow's prices are out (~14:15), warn if its average is extreme
    tomorrow_cheap_below: float = 3.0       # c/kWh: below this → "very cheap" (traffic light basis)
    tomorrow_expensive_above: float = 20.0  # c/kWh: above this → "very expensive"
    min_minutes: int = 30          # skip green/red periods shorter than this
    quiet_enabled: bool = False
    quiet_start_hour: int = 22
    quiet_end_hour: int = 7
    click_url: str = ""            # opened when you tap the notification, e.g. http://192.168.1.20:8080

    def __post_init__(self):
        for f in ("enabled", "on_green", "on_red", "on_tomorrow", "quiet_enabled"):
            if not isinstance(getattr(self, f), bool):
                raise ValueError(f"{f} must be true or false")
        self.server = str(self.server or "").strip().rstrip("/") or "https://ntfy.sh"
        if not re.match(r"^https?://[^\s/]+", self.server):
            raise ValueError("server must start with http:// or https://")
        self.topic = str(self.topic or "").strip()
        if self.topic and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.topic):
            raise ValueError("topic may only contain letters, digits, - and _ (max 64)")
        if self.enabled and not self.topic:
            raise ValueError("topic is needed when notifications are on")
        self.click_url = str(self.click_url or "").strip()
        if self.click_url and not re.match(r"^https?://", self.click_url):
            raise ValueError("click_url must start with http:// or https://")
        self.tomorrow_cheap_below = _num("tomorrow_cheap_below", self.tomorrow_cheap_below, -100, 1000)
        self.tomorrow_expensive_above = _num("tomorrow_expensive_above", self.tomorrow_expensive_above, -100, 1000)
        if self.tomorrow_cheap_below > self.tomorrow_expensive_above:
            raise ValueError("tomorrow_cheap_below must not be greater than tomorrow_expensive_above")
        v = _num("min_minutes", self.min_minutes, 0, 1440)
        self.min_minutes = int(v)
        for f in ("quiet_start_hour", "quiet_end_hour"):
            v = _num(f, getattr(self, f), 0, 24)
            if v != int(v):
                raise ValueError(f"{f} must be a whole hour")
            setattr(self, f, int(v))

    @classmethod
    def keys(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.keys()}

    def is_quiet(self, hour: int) -> bool:
        a, b = self.quiet_start_hour, self.quiet_end_hour
        if not self.quiet_enabled or a == b:
            return False
        return a <= hour < b if a < b else (hour >= a or hour < b)


@dataclass
class WeatherSettings:
    """Front-page weather from MET Norway (yr.no) for this location."""
    enabled: bool = True
    place: str = "Helsinki"        # shown on the front page
    latitude: float = 60.1699
    longitude: float = 24.9384

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be true or false")
        self.place = str(self.place or "").strip()[:80]
        # MET asks for at most 4 decimals (~10 m), more only defeats their cache
        self.latitude = round(_num("latitude", self.latitude, -90, 90), 4)
        self.longitude = round(_num("longitude", self.longitude, -180, 180), 4)

    @classmethod
    def keys(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.keys()}


SECTIONS = {"tariff": Tariff, "traffic_light": TrafficLight, "notifications": Notifications,
            "weather": WeatherSettings}


@dataclass
class Config:
    raw: dict
    tz: ZoneInfo
    db_path: Path
    host: str
    port: int
    tariff: Tariff
    light: TrafficLight = field(default_factory=TrafficLight)
    notify: Notifications = field(default_factory=Notifications)
    weather: WeatherSettings = field(default_factory=WeatherSettings)
    prices: dict = field(default_factory=dict)
    meter: dict = field(default_factory=dict)

    def defaults(self) -> dict:
        """Settings sections from config.toml, before settings-page overrides."""
        return {k: self.raw.get(k, {}) for k in SECTIONS}

    def build(self, overrides: dict | None) -> tuple[Tariff, TrafficLight, Notifications, WeatherSettings]:
        """Validate config.toml values + overrides; raises ValueError with a readable message."""
        o, d = overrides or {}, self.defaults()
        for section, cls in SECTIONS.items():
            unknown = set(o.get(section, {})) - set(cls.keys())
            if unknown:
                raise ValueError(f"unknown {section} setting(s): {', '.join(sorted(unknown))}")
        tariff = Tariff(**{**d["tariff"], **o.get("tariff", {})}, tz=self.tz,
                        stored_spot_vat_percent=self.prices.get("vat_percent", 25.5))
        light = TrafficLight(**{**d["traffic_light"], **o.get("traffic_light", {})})
        notify = Notifications(**{**d["notifications"], **o.get("notifications", {})})
        weather = WeatherSettings(**{**d["weather"], **o.get("weather", {})})
        return tariff, light, notify, weather

    def apply(self, overrides: dict | None) -> None:
        self.tariff, self.light, self.notify, self.weather = self.build(overrides)


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config.toml"
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    app = raw.get("app", {})
    db_path = Path(app.get("db_path", "data/energy.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    tz = ZoneInfo(app.get("timezone", "Europe/Helsinki"))
    try:
        tariff = Tariff(**raw.get("tariff", {}), tz=tz,
                        stored_spot_vat_percent=raw.get("prices", {}).get("vat_percent", 25.5))
        light = TrafficLight(**raw.get("traffic_light", {}))
        notify = Notifications(**raw.get("notifications", {}))
        weather = WeatherSettings(**raw.get("weather", {}))
    except (TypeError, ValueError) as e:
        raise SystemExit(f"config.toml: {e}") from None
    return Config(
        raw=raw,
        tz=tz,
        db_path=db_path,
        host=app.get("host", "0.0.0.0"),
        port=int(app.get("port", 8080)),
        tariff=tariff,
        light=light,
        notify=notify,
        weather=weather,
        prices=raw.get("prices", {}),
        meter=raw.get("meter", {}),
    )
