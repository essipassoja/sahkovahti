"""Combining consumption and prices into per-slot / per-day figures."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from .config import Config
from .db import DB, QUARTER


def day_bounds(d: date, cfg: Config) -> tuple[int, int]:
    start = datetime(d.year, d.month, d.day, tzinfo=cfg.tz)
    nd = d + timedelta(days=1)
    end = datetime(nd.year, nd.month, nd.day, tzinfo=cfg.tz)
    return int(start.timestamp()), int(end.timestamp())   # 23/24/25 h around DST changes


def slots(t0: int, t1: int, cfg: Config, db: DB) -> list[dict]:
    boundaries = list(range(t0, t1 + 1, QUARTER))
    kwh = db.energy_per_slot(boundaries, "import_kwh")
    exp = db.energy_per_slot(boundaries, "export_kwh")
    prices = {}
    for s, e, p in db.prices_between(t0, t1):
        # hourly data (older sources) is spread over its quarters
        for q in range(s, e, QUARTER):
            prices[q] = p
    now = time.time()
    out = []
    for i, s in enumerate(boundaries[:-1]):
        spot = prices.get(s)
        total = cfg.tariff.total_c_kwh(spot)
        k = kwh[i] if i < len(kwh) else None
        out.append({
            "start": s,
            "spot": spot,
            "total": round(total, 3) if total is not None else None,
            "kwh": round(k, 4) if k is not None else None,
            "export_kwh": round(exp[i], 4) if i < len(exp) and exp[i] else None,
            "cost_eur": round(k * total / 100, 4) if k is not None and total is not None else None,
            "spot_cost_eur": round(k * spot / 100, 4) if k is not None and spot is not None else None,
            "state": "past" if s + QUARTER <= now else ("now" if s <= now else "future"),
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
    return {"date": d.isoformat(), "start": t0, "end": t1, "slots": sl, "summary": summarize(sl, cfg)}


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
    s["fixed_eur"] = cfg.tariff.monthly_fixed_eur
    s["month"] = first.strftime("%Y-%m")
    return s


def cheapest_windows(cfg: Config, db: DB, hours=(1, 2, 3, 4)) -> list[dict]:
    """Cheapest contiguous windows from the current quarter onward (known prices only)."""
    now = int(time.time())
    q0 = now - now % QUARTER
    rows = db.prices_between(q0, q0 + 3 * 86400)
    series = []
    for s, e, p in rows:
        for q in range(s, e, QUARTER):
            if q >= q0:
                series.append((q, p))
    # require contiguity
    out = []
    for h in hours:
        n = h * 4
        best = None
        for i in range(len(series) - n + 1):
            if series[i + n - 1][0] - series[i][0] != (n - 1) * QUARTER:
                continue
            avg = sum(p for _, p in series[i:i + n]) / n
            if best is None or avg < best[1]:
                best = (series[i][0], avg)
        if best:
            out.append({"hours": h, "start": best[0], "end": best[0] + n * QUARTER,
                        "avg_spot": round(best[1], 3),
                        "avg_total": round(cfg.tariff.total_c_kwh(best[1]), 3)})
    return out


def traffic_light(cfg: Config, db: DB, now: int | None = None) -> dict:
    """Current green/yellow/red level and when it next changes (known prices only)."""
    now = int(time.time()) if now is None else now
    q0 = now - now % QUARTER
    tl = cfg.light
    series = []
    for s, e, p in db.prices_between(q0, q0 + 2 * 86400):
        for q in range(s, e, QUARTER):
            if q >= q0:
                price = p if tl.basis == "spot" else cfg.tariff.total_c_kwh(p)
                series.append((q, price))
    series.sort()
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
