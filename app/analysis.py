"""Combining consumption and prices into per-slot / per-day figures."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from .config import Config, Tariff
from .db import DB, QUARTER

HOUR = 3600


def day_bounds(d: date, cfg: Config) -> tuple[int, int]:
    start = datetime(d.year, d.month, d.day, tzinfo=cfg.tz)
    nd = d + timedelta(days=1)
    end = datetime(nd.year, nd.month, nd.day, tzinfo=cfg.tz)
    return int(start.timestamp()), int(end.timestamp())   # 23/24/25 h around DST changes


def spot_prices(t0: int, t1: int, tariff: Tariff, db: DB) -> dict[int, float]:
    """Spot price per quarter start in [t0, t1), as you are billed for it.

    With an hourly contract every quarter gets its hour's average (the mean of the known
    quarter prices in that hour), so the price only changes on full hours.
    """
    hourly = tariff.slot_s == HOUR
    # whole hours, so an hour's average doesn't depend on where the range is cut
    a, b = (t0 - t0 % HOUR, t1 + (-t1) % HOUR) if hourly else (t0, t1)
    prices = {}
    for s, e, p in db.prices_between(a, b):
        # hourly data (older sources) is spread over its quarters
        for q in range(max(s, a), min(e, b), QUARTER):
            prices[q] = p
    if hourly:
        per_hour: dict[int, list[float]] = {}
        for q, p in prices.items():
            per_hour.setdefault(q - q % HOUR, []).append(p)
        avg = {h: sum(v) / len(v) for h, v in per_hour.items()}
        prices = {q: avg[q - q % HOUR] for q in prices}
    return {q: p for q, p in prices.items() if t0 <= q < t1}


def spot_at(t: int, tariff: Tariff, db: DB) -> float | None:
    """Billed spot price for the moment t (hour average on an hourly contract)."""
    q = t - t % QUARTER
    p = spot_prices(q, q + QUARTER, tariff, db).get(q)
    return None if p is None else round(p, 4)


def slots(t0: int, t1: int, cfg: Config, db: DB) -> list[dict]:
    step = cfg.tariff.slot_s
    boundaries = list(range(t0, t1 + 1, step))
    kwh = db.energy_per_slot(boundaries, "import_kwh")
    exp = db.energy_per_slot(boundaries, "export_kwh")
    prices = spot_prices(t0, t1, cfg.tariff, db)
    now = time.time()
    out = []
    for i, s in enumerate(boundaries[:-1]):
        spot = prices.get(s)
        total = cfg.tariff.total_c_kwh(spot, s)
        energy = cfg.tariff.energy_c_kwh(spot, s)
        k = kwh[i] if i < len(kwh) else None
        out.append({
            "start": s,
            "spot": round(spot, 4) if spot is not None else None,
            "energy": round(energy, 3) if energy is not None else None,   # your spot part after discount
            "total": round(total, 3) if total is not None else None,
            "kwh": round(k, 4) if k is not None else None,
            "export_kwh": round(exp[i], 4) if i < len(exp) and exp[i] else None,
            "cost_eur": round(k * total / 100, 4) if k is not None and total is not None else None,
            "spot_cost_eur": round(k * spot / 100, 4) if k is not None and spot is not None else None,
            "state": "past" if s + step <= now else ("now" if s <= now else "future"),
        })
    return out


def summarize(sl: list[dict], cfg: Config) -> dict:
    with_kwh = [s for s in sl if s["kwh"] is not None]
    priced = [s for s in with_kwh if s["cost_eur"] is not None]
    spots = [s["spot"] for s in sl if s["spot"] is not None]
    kwh = sum(s["kwh"] for s in with_kwh)
    kwh_p = sum(s["kwh"] for s in priced)
    cost = sum(s["cost_eur"] for s in priced)
    spot_cost = sum(s["spot_cost_eur"] for s in priced)
    avg_spot = sum(spots) / len(spots) if spots else None
    paid_spot = spot_cost / kwh_p * 100 if kwh_p else None
    return {
        "kwh": round(kwh, 3),
        "cost_eur": round(cost, 3) if priced else None,
        # share of consumption that had a known price (history starts when the app starts)
        "price_coverage": round(kwh_p / kwh, 3) if kwh else None,
        "priced_kwh": round(kwh_p, 3),
        "avg_spot": round(avg_spot, 3) if avg_spot is not None else None,
        "min_spot": round(min(spots), 3) if spots else None,
        "max_spot": round(max(spots), 3) if spots else None,
        # consumption-weighted spot you actually paid; timing_benefit > 0 means you used
        # more electricity in cheap slots than in expensive ones
        "weighted_spot": round(paid_spot, 3) if paid_spot is not None else None,
        "timing_benefit_c_kwh": (round(avg_spot - paid_spot, 3)
                                 if avg_spot is not None and paid_spot is not None else None),
        "avg_total_c_kwh": round(cost / kwh_p * 100, 3) if kwh_p else None,
    }


def day_report(d: date, cfg: Config, db: DB) -> dict:
    t0, t1 = day_bounds(d, cfg)
    sl = slots(t0, t1, cfg, db)
    return {"date": d.isoformat(), "start": t0, "end": t1, "slot_s": cfg.tariff.slot_s,
            "slots": sl, "summary": summarize(sl, cfg)}


def history(days: int, cfg: Config, db: DB) -> list[dict]:
    today = datetime.now(cfg.tz).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        s = day_report(d, cfg, db)["summary"]
        out.append({"date": d.isoformat(), **s})
    return out


def month_to_date(cfg: Config, db: DB) -> dict:
    today = datetime.now(cfg.tz).date()
    first = today.replace(day=1)
    t0, _ = day_bounds(first, cfg)
    _, t1 = day_bounds(today, cfg)
    s = summarize(slots(t0, t1, cfg, db), cfg)
    s["fixed_eur"] = round(cfg.tariff.monthly_fixed_eur(today), 2)
    s["month"] = first.strftime("%Y-%m")
    return s


def cheapest_windows(cfg: Config, db: DB, hours=(1, 2, 3, 4)) -> list[dict]:
    """Cheapest contiguous windows from the current quarter onward (known prices only).

    Ranked by your total price, so a cheaper night transfer price counts too. On an hourly
    contract windows are whole billing hours, starting with the current one."""
    now = int(time.time())
    hourly = cfg.tariff.slot_s == HOUR
    q0 = now - now % (HOUR if hourly else QUARTER)
    series = [(q, p, cfg.tariff.total_c_kwh(p, q))
              for q, p in sorted(spot_prices(q0, q0 + 3 * 86400, cfg.tariff, db).items())]
    # require contiguity
    out = []
    for h in hours:
        n = h * 4
        best = None
        for i in range(len(series) - n + 1):
            if series[i + n - 1][0] - series[i][0] != (n - 1) * QUARTER:
                continue
            if hourly and series[i][0] % HOUR:
                continue
            win = series[i:i + n]
            avg_total = sum(t for _, _, t in win) / n
            if best is None or avg_total < best[2]:
                best = (series[i][0], sum(p for _, p, _ in win) / n, avg_total)
        if best:
            out.append({"hours": h, "start": best[0], "end": best[0] + n * QUARTER,
                        "avg_spot": round(best[1], 3), "avg_total": round(best[2], 3)})
    return out


def _light_series(t0: int, t1: int, cfg: Config, db: DB) -> list[tuple[int, float]]:
    """(quarter start, price the traffic light compares) for known prices in [t0, t1)."""
    basis = cfg.light.basis
    return [(q, cfg.tariff.energy_c_kwh(p, q) if basis == "spot" else cfg.tariff.total_c_kwh(p, q))
            for q, p in sorted(spot_prices(t0, t1, cfg.tariff, db).items())]


def day_light_prices(d: date, cfg: Config, db: DB) -> dict | None:
    """Average / min / max of a whole day's prices on the traffic light's basis.

    None until every quarter of the day has a price (tomorrow's are published ~14 Finnish time).
    """
    t0, t1 = day_bounds(d, cfg)
    prices = [p for _, p in _light_series(t0, t1, cfg, db)]
    if len(prices) != (t1 - t0) // QUARTER:
        return None
    return {"date": d.isoformat(), "avg": round(sum(prices) / len(prices), 3),
            "min": round(min(prices), 3), "max": round(max(prices), 3)}


def day_outlook(d: date, cfg: Config, db: DB) -> dict | None:
    """A whole day's prices summed up for the heads-up dashboard: whether the day is very cheap or
    very expensive (the tomorrow notification's limits) and its red periods. None until published."""
    day = day_light_prices(d, cfg, db)
    if day is None:
        return None
    n = cfg.notify
    verdict = ("cheap" if day["avg"] < n.tomorrow_cheap_below
               else "expensive" if day["avg"] > n.tomorrow_expensive_above else None)
    red: list[dict] = []
    for q, p in _light_series(*day_bounds(d, cfg), cfg, db):
        if cfg.light.level(p) != "red":
            continue
        if red and q - red[-1]["end"] <= 2 * QUARTER:     # a dip of up to 30 min doesn't split a period
            red[-1]["end"], red[-1]["max"] = q + QUARTER, max(red[-1]["max"], round(p, 3))
        else:
            red.append({"start": q, "end": q + QUARTER, "max": round(p, 3)})
    return {**day, "verdict": verdict, "red": red, "red_above": cfg.light.red_above}


def traffic_light(cfg: Config, db: DB, now: int | None = None) -> dict:
    """Current green/yellow/red level and when it next changes (known prices only)."""
    now = int(time.time()) if now is None else now
    q0 = now - now % QUARTER
    tl = cfg.light
    series = _light_series(q0, q0 + 2 * 86400, cfg, db)
    if not series or series[0][0] != q0:
        level, price = None, None
    else:
        price = series[0][1]
        level = tl.level(price)
    nxt = next_green = None
    prev_q = q0
    for q, p in series[1:]:
        if q - prev_q != QUARTER:      # gap in known prices: stop looking ahead
            break
        prev_q = q
        lv = tl.level(p)
        if nxt is None and lv != level:
            nxt = {"level": lv, "at": q}
        if next_green is None and lv == "green" and level != "green":
            next_green = q
        if nxt is not None and (next_green is not None or level == "green"):
            break
    return {
        "level": level,
        "price": round(price, 3) if price is not None else None,
        "basis": tl.basis,
        "green_below": tl.green_below,
        "red_above": tl.red_above,
        "next": nxt,
        "next_green": next_green,
    }
