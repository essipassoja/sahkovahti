// Electricity page: today's prices coloured like the traffic light, spot price now, price now,
// power now. Popups: prices today and tomorrow, power over the last 24 hours.
import { $, el, get, num, f0, f1, f2, watts, hm, isoDay, when, weekday, dayMonth, hourTicks, stat } from "./core.js";
import * as popup from "./popup.js";

const NAME = { green: "green", yellow: "yellow", red: "red" };
const SHORT = { green: "Green · good time for big loads", yellow: "Yellow · normal price",
                red: "Red · postpone big loads", none: "No price for right now" };
const ADVICE = {
  green: "Go ahead with the sauna, EV charging, laundry or dishwasher.",
  yellow: "Fine for everyday use. If a big load can wait, green is cheaper.",
  red: "Postpone big loads like the sauna, EV charging or laundry if you can.",
  none: "Waiting for price data.",
};

// ---------- live tiles ----------
function renderLight(L) {
  const level = L.level || "none";
  $("spotTile").dataset.level = level;
  $("verdict").textContent = SHORT[level];
  $("spotTile").title = ADVICE[level];

  let next = "";
  if (L.next) next = `Turns ${NAME[L.next.level]} at ${when(L.next.at)}`;
  else if (L.level) next = `Stays ${NAME[L.level]} for all prices published so far`;
  if (L.level && L.level !== "green") {
    if (L.next_green && (!L.next || L.next_green !== L.next.at)) next += ` · green from ${when(L.next_green)}`;
    else if (!L.next_green) next += " · no green slots in published prices";
  }
  $("nextChange").textContent = next || " ";

  const basis = L.basis === "spot" ? "your spot price" : "your total price";
  $("legend").textContent = `${basis}, c/kWh · green under ${num(L.green_below, f0)} c · red over ${num(L.red_above, f0)} c`;
}

export async function loadLive() {
  let d;
  try { d = await get("/api/live"); } catch { return; }
  renderLight(d.light);

  // spot: with a discount, your discounted price big and the market spot price small;
  // without one, just the market spot price
  const disc = d.discount_percent || 0;
  $("spotLabel").textContent = disc ? `Spot price now (−${num(disc, f0)} %)` : "Spot price now";
  $("spotNow").textContent = num(disc ? d.energy : d.spot, f2);
  $("spotSub").textContent = d.spot == null ? "no price for this slot"
    : disc ? `market spot ${num(d.spot, f2)} c/kWh` : "market spot, incl. VAT";

  $("priceNow").textContent = num(d.total, f1);
  $("priceSub").textContent = d.spot == null ? "no price for this slot"
    : disc && d.total_full != null ? `${num(d.total_full, f1)} c without discount · incl. transfer, tax & margin`
    : "spot + transfer, tax & margin";

  const p = d.power_w;
  if (p != null) {
    const kw = p >= 1000;
    $("powerNow").textContent = kw ? f2.format(p / 1000) : f0.format(p);
    $("powerUnit").textContent = kw ? "kW" : "W";
  }
  $("powerSub").textContent = d.cost_rate_eur_h != null
    ? `costing ${num(d.cost_rate_eur_h, f2)} €/h · today ${num(d.today.cost_eur, f2)} €`
    : " ";
}

// ---------- prices ----------
let pc = null, fetchedAt = 0;
const LEVEL = (p) => (p < pc.green_below ? "green" : p > pc.red_above ? "red" : "yellow");

// one day of prices as bars, one per billing slot; slots without a price stay empty
function priceBars(box, d, { height, label, name = "" }) {
  const now = Date.now() / 1000, step = pc.slot_s;
  const byStart = new Map(d.prices), items = [];
  for (let t = d.start; t < d.end; t += step) {
    const p = byStart.get(t), lv = p == null ? null : LEVEL(p);
    items.push({
      t, y: p ?? null, cls: (lv ? "lvl-" + lv : "") + (t + step <= now ? " past" : ""),
      tip: p == null ? null : `<div class="t">${name}${hm(t)}–${hm(t + step)}</div>` +
        `<div class="r"><span><span class="sw" style="background:var(--lamp-${lv})"></span>${NAME[lv]}</span><b>${num(p, f2)} c/kWh</b></div>`,
    });
  }
  const narrow = box.clientWidth < 560;
  Charts.bars(box, items, {
    height, label,
    yFmt: (v) => f0.format(v),
    refs: [{ value: pc.red_above, label: `red over ${num(pc.red_above, f0)} c` },
           { value: pc.green_below, label: `green under ${num(pc.green_below, f0)} c` }],
    nowIndex: items.findIndex((it) => it.t <= now && now < it.t + step), nowLabel: "now",
    xLabel: (i) => {
      const t = hm(items[i].t);           // "06.00"
      return (narrow ? /^(00|12)\.00$/ : /^(00|06|12|18)\.00$/).test(t) ? t.slice(0, 2) : null;
    },
    emptyText: "No prices yet",
  });
}

function drawToday() {
  if (!pc) return;
  const box = $("elecChart");
  priceBars(box, pc.days[0], { height: Math.round(Math.max(170, Math.min(260, box.clientWidth * 0.26))), label: "Prices today" });
}
function renderPrices() {
  drawToday();
  popup.refresh(pricesView);
}

async function loadPrices() {
  try { pc = await get("/api/prices"); } catch { return; }
  fetchedAt = Date.now();
  renderPrices();
}

// every minute: moves "now" along. Prices change once a day (tomorrow's come out around 14),
// so new ones every 5 minutes, and right away when the day changes.
export function tickPrices() {
  if (!pc || Date.now() - fetchedAt >= 5 * 60000 || pc.days[0].date !== isoDay(Date.now() / 1000)) loadPrices();
  else renderPrices();
}

const pricesView = {
  render() {
    popup.head("Prices", pc ? (pc.basis === "spot" ? "your spot price" : "your total price") + ", c/kWh incl. VAT" : "");
    if (!pc) return popup.note("Loading prices…");
    const step = pc.slot_s, charts = [];
    popup.body(...pc.days.map((d, k) => {
      const sec = el("section", "pop-day"), mid = d.start + 43200;
      const head = el("h3", null, k === 0 ? "Today" : "Tomorrow");
      head.append(el("small", null, ` ${weekday(mid, "long")} ${dayMonth(mid)}`));
      sec.append(head);
      if (!d.prices.length) {
        sec.append(el("p", "pop-note", k === 0 ? "No prices for today yet." : "Tomorrow's prices come out around 14.00."));
        return sec;
      }
      const ps = d.prices.map((x) => x[1]);
      const lo = d.prices.reduce((a, b) => (b[1] < a[1] ? b : a)), hi = d.prices.reduce((a, b) => (b[1] > a[1] ? b : a));
      const stats = el("div", "pop-stats three");
      stats.append(stat("Average", `${num(ps.reduce((a, b) => a + b, 0) / ps.length, f1)} c`),
        stat("Lowest", `${num(lo[1], f1)} c`, `at ${hm(lo[0])}`),
        stat("Highest", `${num(hi[1], f1)} c`, `at ${hm(hi[0])}`));
      // hours at each traffic light level
      const levels = el("ul", "pop-levels");
      levels.append(...["green", "yellow", "red"].map((lv) => {
        const min = ps.filter((p) => LEVEL(p) === lv).length * step / 60;
        const li = el("li"), sw = el("span", "sw");
        sw.style.background = `var(--lamp-${lv})`;
        li.append(sw, `${NAME[lv]} ${Math.floor(min / 60)} h` + (min % 60 ? ` ${min % 60} min` : ""));
        return li;
      }));
      const box = el("div", "chart pc-chart");
      sec.append(stats, levels, box);
      if (!d.complete) sec.append(el("p", "pop-note", "Only part of the day's prices are published so far."));
      charts.push([box, d, k]);
      return sec;
    }));
    // drawn once in place, so the charts know their width
    for (const [box, d, k] of charts) {
      priceBars(box, d, { height: Math.round(Math.max(170, Math.min(240, box.clientWidth * 0.28))),
        label: k === 0 ? "Prices today" : "Prices tomorrow", name: k === 0 ? "" : "Tomorrow " });
    }
  },
};

// ---------- power over the last 24 hours ----------
let power = null;

const powerView = {
  pollMs: 60000,
  async poll() {
    try { power = await get("/api/power?minutes=1440"); } catch { return; }
    popup.refresh(powerView);
  },
  render() {
    popup.head("Electricity use", "last 24 hours, average power per 5 minutes");
    const now = Date.now() / 1000, t0 = now - 86400;
    const pts = (power?.points || []).filter(([t]) => t >= t0).map(([t, p]) => ({ t, y: p }));
    const stats = el("div", "pop-stats"), box = el("div", "chart");
    if (pts.length > 1) {
      const sum = pts.reduce((a, p) => a + p.y, 0);
      const lo = pts.reduce((a, b) => (b.y < a.y ? b : a)), hi = pts.reduce((a, b) => (b.y > a.y ? b : a));
      stats.append(stat("Used", `≈ ${num(sum * power.bucket_s / 3.6e6, f1)} kWh`, "from the readings shown"),
        stat("Average", watts(sum / pts.length)),
        stat("Peak", watts(hi.y), `at ${when(hi.t)}`),
        stat("Lowest", watts(lo.y), `always on · ${when(lo.t)}`));
    }
    popup.body(...(pts.length > 1 ? [stats] : []), box);
    Charts.line(box, pts, {
      height: Math.round(Math.max(200, Math.min(300, box.clientWidth * 0.32))),
      tMin: t0, tMax: now, xTicks: hourTicks(t0, now, box.clientWidth < 560), xFmt: hm,
      yFmt: (v) => (v >= 1000 ? f1.format(v / 1000) + "k" : f0.format(v)),
      tip: (p) => `<div class="t">${when(p.t)}</div><div class="r"><span>Power</span><b>${watts(p.y)}</b></div>`,
      label: "Power over the last 24 hours, watts",
      emptyText: power ? "No meter readings in the last 24 hours" : "Loading…",
    });
  },
};

popup.opens($("elecCard"), () => popup.open(pricesView));
popup.opens($("spotTile"), () => popup.open(pricesView));
popup.opens($("powerTile"), () => popup.open(powerView));

let rt;
window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(drawToday, 150); });     // the popup redraws itself
