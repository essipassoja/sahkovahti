(function () {
  const $ = (id) => document.getElementById(id);
  let tz = "Europe/Helsinki";

  const nf = (d) => new Intl.NumberFormat("fi-FI", { minimumFractionDigits: d, maximumFractionDigits: d });
  const f0 = nf(0), f1 = nf(1), f2 = nf(2);
  const num = (v, f = f2) => (v == null || Number.isNaN(v) ? "–" : f.format(v));
  const hm = (t) => new Intl.DateTimeFormat("fi-FI", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(new Date(t * 1000));
  const isoDay = (t) => new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date(t * 1000));
  const when = (t) => {
    const today = isoDay(Date.now() / 1000), d = isoDay(t);
    if (d === today) return hm(t);
    if (d === isoDay(Date.now() / 1000 + 86400)) return hm(t) + " tomorrow";
    return hm(t) + " " + new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short" }).format(new Date(t * 1000));
  };

  const TEXT = {
    green: ["Good time to use electricity", "Go ahead with the sauna, EV charging, laundry or dishwasher."],
    yellow: ["Normal price", "Fine for everyday use. If a big load can wait, green is cheaper."],
    red: ["Expensive right now", "Postpone big loads like the sauna, EV charging or laundry if you can."],
    none: ["No price for right now", "Waiting for price data."],
  };
  const NAME = { green: "green", yellow: "yellow", red: "red" };

  async function get(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(url + " → " + r.status);
    return r.json();
  }

  async function loadStatus() {
    try {
      const s = await get("/api/status");
      tz = s.timezone || tz;
      const m = s.meter;
      $("status").className = "status " + (m.simulated ? "sim" : m.connected ? "ok" : "bad");
      $("statusText").textContent = m.simulated ? "Simulator" : m.connected ? "Meter connected" : "Meter offline";
      $("simNote").hidden = !m.simulated;
    } catch {
      $("status").className = "status bad"; $("statusText").textContent = "App not reachable";
    }
  }

  const SHORT = { green: "Green · good time for big loads", yellow: "Yellow · normal price",
                  red: "Red · postpone big loads", none: "No price for right now" };

  function renderLight(L) {
    const level = L.level || "none";
    $("spotTile").dataset.level = level;
    $("verdict").textContent = SHORT[level];
    $("spotTile").title = TEXT[level][1];

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

  async function loadLive() {
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

  // MET Norway symbol codes, e.g. "lightrainshowers_day" → icon + text
  const WX = [
    ["thunder", "⛈️", "Thunder"], ["heavysnow", "❄️", "Heavy snow"], ["snow", "🌨️", "Snow"],
    ["sleet", "🌨️", "Sleet"], ["heavyrain", "🌧️", "Heavy rain"], ["lightrain", "🌦️", "Light rain"],
    ["rain", "🌧️", "Rain"], ["fog", "🌫️", "Fog"], ["cloudy", "☁️", "Cloudy"],
  ];
  function wx(code) {
    if (!code) return ["", ""];
    const night = code.endsWith("_night") || code.endsWith("_polartwilight");
    const base = code.split("_")[0];
    if (base === "clearsky") return [night ? "🌙" : "☀️", "Clear"];
    if (base === "fair") return [night ? "🌙" : "🌤️", "Mostly clear"];
    if (base === "partlycloudy") return [night ? "☁️" : "⛅", "Partly cloudy"];
    const hit = WX.find(([k]) => base.includes(k));
    if (!hit) return ["", base];
    return [hit[1], hit[2] + (base.endsWith("showers") ? " showers" : "")];
  }
  const deg = (v) => (v == null ? "–" : f0.format(Math.round(v) || 0));

  const weekday = (t, style = "short") =>
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: style }).format(new Date(t * 1000));
  const dayMonth = (t) => new Intl.DateTimeFormat("fi-FI", { timeZone: tz, day: "numeric", month: "numeric" }).format(new Date(t * 1000));
  // name a day relative to today: "today", "tomorrow" or "on Saturday"
  const onDay = (t) => {
    const d = isoDay(t), now = Date.now() / 1000;
    if (d === isoDay(now)) return "today";
    if (d === isoDay(now + 86400)) return "tomorrow";
    return "on " + weekday(t + 43200, "long");
  };
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  function renderDays(days) {
    $("wxDays").replaceChildren(...days.map((d, k) => {
      const li = el("li", "opens");
      const [i, t] = wx(d.symbol);
      li.title = t;
      opens(li, () => openPop("weather", k + 1));
      const head = el("div", "d", weekday(d.ts + 43200));
      head.append(el("small", null, " " + dayMonth(d.ts + 43200)));
      const temps = el("div", "v", deg(d.tmax) + "°");
      temps.append(el("span", "lo", " / " + deg(d.tmin) + "°"));
      li.append(head, el("div", "i", i), temps,
        el("div", "p", d.precip_mm >= 0.1 ? `${num(d.precip_mm, f1)} mm` : ""),
        el("div", "w", d.wind == null ? "" : `wind ${num(d.wind, f0)} m/s`));
      return li;
    }));
  }

  function warnBox(w, full = false) {
    const box = el("article", "wx-warn");
    box.dataset.color = w.color;
    const head = el("div", "wh");
    head.append(el("span", "wi", "⚠️"), el("strong", null, w.headline || w.event || "Weather warning"));
    if (w.onset != null && w.onset <= Date.now() / 1000) head.append(el("span", "tag", "In effect"));
    box.append(head);
    if (w.impacts) box.append(el("p", null, w.impacts));
    if (full && w.description) box.append(el("p", null, w.description));
    else if (w.description) box.title = w.description;
    return box;
  }
  const renderWarnings = (list) => $("wxWarnings").replaceChildren(...list.map((w) => warnBox(w)));

  const ALERTS = {
    wind: ["💨", "Strong wind", (e) => [
      e.gust != null ? `gusts up to ${num(e.gust, f0)} m/s` : null,
      e.wind != null ? `mean wind up to ${num(e.wind, f0)} m/s` : null, `strongest ${when(e.ts)}`]],
    heat: ["🥵", "Hot weather", (e) => [`up to ${deg(e.temp)} °C`, when(e.ts)]],
    cold: ["🥶", "Severe cold", (e) => [`down to ${deg(e.temp)} °C`, when(e.ts)]],
    rain: ["🌧️", "Heavy rain", (e) => [`${num(e.total_mm, f0)} mm ${onDay(e.ts + 43200)}`,
      e.max_mm_h != null ? `up to ${num(e.max_mm_h, f1)} mm/h at ${when(e.max_ts)}` : null]],
    snow: ["❄️", "Heavy snowfall", (e) => [`roughly ${num(e.total_mm, f0)} cm of snow ${onDay(e.ts + 43200)}`]],
  };

  const item = (icon, title, detail) => {
    const li = el("li"), text = el("div");
    text.append(el("strong", null, title), el("span", null, detail.filter(Boolean).join(" · ")));
    li.append(el("span", "ai", icon), text);
    return li;
  };

  // Heads-up dashboard: dramatic weather and tomorrow's electricity, or a calm picture
  const hu = { weather: false, extremes: [], tomorrow: undefined };

  function powerItems(o) {
    if (!o) return [];
    const c = (v) => `${num(v, f1)} c`;
    const prices = `average ${num(o.avg, f1)} c/kWh (${c(o.min)} – ${c(o.max)})`;
    const out = [];
    if (o.verdict === "expensive") out.push(item("💸", "Very expensive day", [prices, "do laundry and EV charging today if you can"]));
    if (o.verdict === "cheap") out.push(item("💚", "Very cheap day", [prices, "save laundry, EV charging and sauna for tomorrow"]));
    if (o.red.length) {
      const shown = o.red.slice(0, 4).map((r) => `${hm(r.start)}–${hm(r.end)} up to ${c(r.max)}`);
      if (o.red.length > 4) shown.push(`${o.red.length - 4} more`);
      out.push(item("🔴", o.red.length > 1 ? "Expensive periods" : "Expensive period",
        [`over ${num(o.red_above, f0)} c/kWh: ` + shown.join(", ")]));
    }
    return out;
  }

  function renderHeadsUp() {
    const wxList = hu.extremes.filter((e) => ALERTS[e.kind]).map((e) => {
      const [icon, title, detail] = ALERTS[e.kind];
      return item(icon, title, detail(e));
    });
    $("huWeatherList").replaceChildren(...wxList);
    $("huWeather").hidden = !wxList.length;

    const pList = powerItems(hu.tomorrow);
    $("huPowerList").replaceChildren(...pList);
    $("huPower").hidden = !pList.length;
    if (hu.tomorrow) $("huPowerHead").textContent = `Electricity tomorrow, ${weekday(Date.now() / 1000 + 86400, "long")}`;

    const calm = !wxList.length && !pList.length;
    $("huCalm").hidden = !calm;
    const known = hu.tomorrow ? "nothing unusual in tomorrow's electricity prices" : null;
    $("huCalmText").textContent = (hu.weather
      ? ["No dramatic weather in the next 3 days", known]
      : [known || "Nothing to watch out for right now"]).filter(Boolean).join(", and ") + ".";
    $("huFoot").hidden = hu.tomorrow !== null;
    $("huFoot").textContent = "Tomorrow's electricity prices come out around 14.00.";
    $("headsUp").dataset.alert = calm ? "" : "1";
    dash.badges();
  }

  async function loadOutlook() {
    try { hu.tomorrow = (await get("/api/outlook")).tomorrow; } catch { return; }
    renderHeadsUp();
  }

  // Today's prices on the Electricity dashboard, one bar per billing slot coloured like the traffic light
  let pc = null;
  const LEVEL = (p) => (p < pc.green_below ? "green" : p > pc.red_above ? "red" : "yellow");

  // one day of prices as bars; slots without a price stay empty
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

  function renderPrices() {
    if (!pc) return;
    const box = $("elecChart");
    priceBars(box, pc.days[0], { height: Math.round(Math.max(170, Math.min(260, box.clientWidth * 0.26))), label: "Prices today" });
    if (pop.kind === "prices") renderPricePop();
  }

  // ---------- detail popups: prices today and tomorrow, power over the last 24 hours ----------
  const pop = { kind: null, day: 0, power: null, timer: null };
  const dlg = $("pop");
  const watts = (w, f = f2) => (w >= 1000 ? `${num(w / 1000, f)} kW` : `${num(w, f0)} W`);
  const stat = (label, value, sub) => {
    const box = el("div");
    box.append(el("span", null, label), el("b", null, value));
    if (sub) box.append(el("small", null, sub));
    return box;
  };

  // a tile that opens a popup, by click, tap, Enter or Space
  function opens(node, fn) {
    node.setAttribute("role", "button");
    node.setAttribute("tabindex", "0");
    node.setAttribute("aria-haspopup", "dialog");
    node.addEventListener("click", fn);
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); }
    });
  }

  function openPop(kind, day = 0) {
    pop.kind = kind;
    pop.day = day;
    dlg.append($("tip"));         // the dialog sits above the page, so chart tooltips have to live inside it
    if (!dlg.open) dlg.showModal();
    dash.restart();               // holds the dashboards still while the popup is open
    clearInterval(pop.timer);
    dlg.scrollTop = 0;
    if (kind === "prices") renderPricePop();
    else if (kind === "weather") renderWeatherPop();
    else {
      renderPowerPop();
      loadPowerPop();
      pop.timer = setInterval(loadPowerPop, 60000);
    }
  }
  dlg.addEventListener("close", () => {
    pop.kind = null;
    clearInterval(pop.timer);
    Charts.hideTip();
    document.body.append($("tip"));
    $("popBody").replaceChildren();
    dash.restart();
  });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // a click on the backdrop
  $("popClose").onclick = () => dlg.close();
  for (const [id, kind] of [["elecCard", "prices"], ["spotTile", "prices"], ["powerTile", "power"], ["wxNowBox", "weather"]]) {
    opens($(id), () => openPop(kind));
  }
  $("wxNext").addEventListener("click", () => openPop("weather"));      // the next hours: today's details

  // ---------- weather popup: one day at a time, today and the next days ----------
  const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const compass = (d) => COMPASS[Math.round(d / 45) % 8];
  const windArrow = (dir) => {      // points where the wind blows to
    const a = el("span", "arr", "↑");
    a.style.transform = `rotate(${dir + 180}deg)`;
    a.setAttribute("aria-label", `from ${compass(dir)}`);
    return a;
  };
  const span = (b) => (b.len > 3600 ? `${hm(b.ts)}–${hm(b.ts + b.len)}` : hm(b.ts));
  const maxOf = (rows, k) => {
    const v = rows.map((b) => b[k]).filter((x) => x != null);
    return v.length ? Math.max(...v) : null;
  };
  const minOf = (rows, k) => {
    const v = rows.map((b) => b[k]).filter((x) => x != null);
    return v.length ? Math.min(...v) : null;
  };

  function renderWeatherPop() {
    const w = wxData;
    $("popTitle").textContent = "Weather";
    $("popSub").textContent = w?.place || "";
    if (!w?.now) { $("popBody").replaceChildren(el("p", "pop-note", "Loading the forecast…")); return; }
    const list = [w.today, ...(w.days || [])];
    if (!list[pop.day]) pop.day = Math.max(0, list.findIndex(Boolean));
    const d = list[pop.day], mid = d.ts + 43200, today = pop.day === 0;
    const rows = (w.hourly || []).filter((b) => isoDay(b.ts) === d.date);

    const tabs = el("div", "seg pop-tabs");
    tabs.setAttribute("role", "tablist");
    list.forEach((x, k) => {
      if (!x) return;
      const t = x.ts + 43200;
      const b = el("button", k === pop.day ? "on" : null, k === 0 ? "Today" : k === 1 ? "Tomorrow" : `${weekday(t)} ${dayMonth(t)}`);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(k === pop.day));
      b.onclick = () => { pop.day = k; renderWeatherPop(); };
      tabs.append(b);
    });

    // the day at a glance; today from the weather now
    const [icon, text] = wx(today ? w.now.symbol : d.symbol);
    const head = el("div", "wxp-head"), what = el("div");
    what.append(el("h3", null, `${today ? "Today" : pop.day === 1 ? "Tomorrow" : weekday(mid, "long")}`),
      el("p", null, today ? `${text} · ${deg(w.now.temp)} °C now` : text));
    what.firstChild.append(el("small", null, ` ${today ? weekday(mid, "long") + " " : ""}${dayMonth(mid)}`));
    head.append(el("span", "wx-icon", icon), what);

    const stats = el("div", "pop-stats");
    stats.append(stat(today ? "Rest of today" : "Temperature", `${deg(d.tmax)}° / ${deg(d.tmin)}°`, "high / low"));
    const prob = maxOf(rows, "prob");
    stats.append(stat("Precipitation", `${num(d.precip_mm, f1)} mm`,
      [d.snow_mm >= 0.1 ? `${num(d.snow_mm, f1)} mm as snow` : null, prob != null ? `chance up to ${num(prob, f0)} %` : null]
        .filter(Boolean).join(" · ") || null));
    const windiest = rows.reduce((a, b) => (b.wind != null && (a == null || b.wind > a.wind) ? b : a), null);
    stats.append(stat("Wind", `${num(d.wind, f0)} m/s`,
      [d.gust != null ? `gusts ${num(d.gust, f0)} m/s` : null, windiest?.dir != null ? `from ${compass(windiest.dir)}` : null]
        .filter(Boolean).join(" · ") || null));
    const hLo = minOf(rows, "humidity"), hHi = maxOf(rows, "humidity");
    if (hHi != null) stats.append(stat("Humidity", hLo === hHi ? `${num(hHi, f0)} %` : `${num(hLo, f0)}–${num(hHi, f0)} %`));
    const clouds = rows.map((b) => b.cloud).filter((x) => x != null);
    if (clouds.length) stats.append(stat("Cloud cover", `${num(clouds.reduce((a, b) => a + b, 0) / clouds.length, f0)} %`, "on average"));
    const ps = rows.filter((b) => b.pressure != null);
    if (ps.length) {
      const change = ps[ps.length - 1].pressure - ps[0].pressure;
      stats.append(stat("Air pressure", `${num(ps[0].pressure, f0)} hPa`,
        ps.length < 2 ? null : Math.abs(change) < 2 ? "steady" : `${change > 0 ? "rising" : "falling"} to ${num(ps[ps.length - 1].pressure, f0)}`));
    }
    const uv = maxOf(rows, "uv");
    if (uv != null && uv >= 1) stats.append(stat("UV index", num(uv, f0), "highest, clear sky"));
    const thunder = maxOf(rows, "thunder");
    if (thunder != null && thunder >= 5) stats.append(stat("Thunder", `${num(thunder, f0)} %`, "highest chance"));

    // warnings in effect at some point of the day
    const dayEnd = d.ts + 86400;
    const warns = (w.warnings || []).filter((x) => (x.onset ?? 0) < dayEnd && x.expires > d.ts).map((x) => warnBox(x, true));

    const chart = el("div", "chart");
    const table = el("table", "wxp-table");
    table.innerHTML = `<thead><tr><th>Time</th><th>Weather</th><th>Temp</th><th>Precip.</th><th>Wind (gust) m/s</th>` +
      `<th class="opt">Humidity</th><th class="opt">Cloud</th></tr></thead>`;
    const tbody = el("tbody");
    for (const b of rows) {
      const tr = el("tr"), [i, t] = wx(b.symbol);
      const sky = el("td", "sky");
      sky.append(el("span", "i", i), el("span", "txt", t));
      const temp = el("td", "num", b.len > 3600 ? `${deg(b.tmax)}° / ${deg(b.tmin)}°` : `${deg(b.temp)}°`);
      const rain = el("td", "num", b.precip ? `${num(b.precip, f1)} mm` : "–");
      if (b.prob != null && b.prob >= 10) rain.append(el("small", null, ` ${num(b.prob, f0)} %`));
      const wind = el("td", "num");
      if (b.dir != null) wind.append(windArrow(b.dir));
      wind.append(b.wind == null ? "–" : num(b.wind, f0) + (b.gust != null ? ` (${num(b.gust, f0)})` : ""));
      tr.append(el("td", "num", span(b)), sky, temp, rain, wind,
        el("td", "num opt", b.humidity == null ? "–" : `${num(b.humidity, f0)} %`),
        el("td", "num opt", b.cloud == null ? "–" : `${num(b.cloud, f0)} %`));
      if (b.precip) tr.classList.add(b.snow ? "snowy" : "wet");
      tbody.append(tr);
    }
    table.append(tbody);
    const scroll = el("div", "wxp-scroll");
    scroll.append(table);

    const credit = el("p", "wx-credit");
    credit.innerHTML = `Forecast from <a href="https://www.yr.no/" target="_blank" rel="noopener">Yr</a>, data by MET Norway` +
      (w.updated_at ? ` · updated ${when(Date.parse(w.updated_at) / 1000)}` : "") +
      (rows.some((b) => b.len > 3600) ? " · 6-hour periods further ahead, as the forecast gets coarser" : "");

    $("popBody").replaceChildren(tabs, head, stats, ...warns, el("h4", "wxp-sub", "Temperature"), chart,
      el("h4", "wxp-sub", rows.every((b) => b.len === 3600) ? (today ? "Hour by hour, rest of today" : "Hour by hour") : "Through the day"),
      scroll, credit);

    // temperature over the day, drawn in place so it knows its width
    const pts = rows.filter((b) => b.temp != null).map((b) => ({ t: b.ts, y: b.temp, b }));
    const narrow = chart.clientWidth < 560, ticks = [];
    if (pts.length) {
      for (let t = Math.ceil(pts[0].t / 3600) * 3600; t <= pts[pts.length - 1].t; t += 3600) {
        if (+hm(t).slice(0, 2) % (narrow ? 6 : 3) === 0) ticks.push(t);
      }
    }
    Charts.line(chart, pts, {
      height: Math.round(Math.max(160, Math.min(220, chart.clientWidth * 0.24))),
      fit: true, minSpan: 4, cls: "temp", xTicks: ticks, xFmt: hm, yFmt: (v) => f0.format(v) + "°",
      tip: (p) => `<div class="t">${span(p.b)}</div>` +
        `<div class="r"><span>${wx(p.b.symbol)[1] || "Temperature"}</span><b>${num(p.y, f1)} °C</b></div>` +
        (p.b.precip ? `<div class="r"><span>${p.b.snow ? "Snow" : "Rain"}</span><b>${num(p.b.precip, f1)} mm</b></div>` : "") +
        (p.b.wind != null ? `<div class="r"><span>Wind</span><b>${num(p.b.wind, f0)} m/s</b></div>` : ""),
      label: "Temperature over the day, °C",
      emptyText: "Not enough forecast left for a chart",
    });
  }

  function renderPricePop() {
    $("popTitle").textContent = "Prices";
    $("popSub").textContent = pc ? (pc.basis === "spot" ? "your spot price" : "your total price") + ", c/kWh incl. VAT" : "";
    if (!pc) { $("popBody").replaceChildren(el("p", "pop-note", "Loading prices…")); return; }
    const step = pc.slot_s, charts = [];
    $("popBody").replaceChildren(...pc.days.map((d, k) => {
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
  }

  async function loadPowerPop() {
    let d;
    try { d = await get("/api/power?minutes=1440"); } catch { return; }
    pop.power = d;
    if (pop.kind === "power") renderPowerPop();
  }

  function renderPowerPop() {
    $("popTitle").textContent = "Electricity use";
    $("popSub").textContent = "last 24 hours, average power per 5 minutes";
    const now = Date.now() / 1000, t0 = now - 86400;
    const pts = (pop.power?.points || []).filter(([t]) => t >= t0).map(([t, p]) => ({ t, y: p }));
    const stats = el("div", "pop-stats"), box = el("div", "chart");
    if (pts.length > 1) {
      const sum = pts.reduce((a, p) => a + p.y, 0);
      const lo = pts.reduce((a, b) => (b.y < a.y ? b : a)), hi = pts.reduce((a, b) => (b.y > a.y ? b : a));
      stats.append(stat("Used", `≈ ${num(sum * pop.power.bucket_s / 3.6e6, f1)} kWh`, "from the readings shown"),
        stat("Average", watts(sum / pts.length)),
        stat("Peak", watts(hi.y), `at ${when(hi.t)}`),
        stat("Lowest", watts(lo.y), `always on · ${when(lo.t)}`));
    }
    $("popBody").replaceChildren(...(pts.length > 1 ? [stats] : []), box);
    const narrow = box.clientWidth < 560, ticks = [];
    for (let t = Math.ceil(t0 / 3600) * 3600; t <= now; t += 3600) {
      if (+hm(t).slice(0, 2) % (narrow ? 6 : 3) === 0) ticks.push(t);
    }
    Charts.line(box, pts, {
      height: Math.round(Math.max(200, Math.min(300, box.clientWidth * 0.32))),
      tMin: t0, tMax: now, xTicks: ticks, xFmt: hm,
      yFmt: (v) => (v >= 1000 ? f1.format(v / 1000) + "k" : f0.format(v)),
      tip: (p) => `<div class="t">${when(p.t)}</div><div class="r"><span>Power</span><b>${watts(p.y)}</b></div>`,
      label: "Power over the last 24 hours, watts",
      emptyText: pop.power ? "No meter readings in the last 24 hours" : "Loading…",
    });
  }

  async function loadPrices() {
    try { pc = await get("/api/prices"); } catch { return; }
    renderPrices();
  }

  // Calendar dashboard: today on the left, the next days on the right
  const CAL_NEXT_MAX = 8;           // keeps the slide within a tablet screen
  const dayName = (iso) => {
    const t = Date.parse(iso + "T12:00:00Z") / 1000, now = Date.now() / 1000;
    if (iso === isoDay(now + 86400)) return "Tomorrow";
    return weekday(t, "long") + " " + dayMonth(t);
  };

  function calRow(e, today) {
    const li = el("li");
    li.dataset.cal = e.calendar % 6;
    const now = Date.now() / 1000;
    const time = el("div", "ct");
    if (e.all_day) {
      time.textContent = "All day";
      if (e.last_date !== e.date) time.append(el("small", null, `until ${weekday(Date.parse(e.last_date + "T12:00:00Z") / 1000)}`));
    } else {
      time.textContent = hm(e.start);
      if (e.end > e.start) time.append(el("small", "end", "–" + hm(e.end)));
    }
    const body = el("div", "cb");
    body.append(el("strong", null, e.title));
    if (e.location) body.append(el("span", null, e.location));
    li.append(time, body);
    if (today && !e.all_day) {
      if (e.end <= now && e.end > e.start) li.classList.add("past");
      else if (e.start <= now && now < e.end) body.prepend(el("span", "now", "Now"));
    }
    return li;
  }

  async function loadCalendar() {
    let c;
    try { c = await get("/api/calendar"); } catch { return; }
    const show = !!c.enabled;
    if ($("calendar").hidden === show) { $("calendar").hidden = !show; dash.refresh(); }
    if (!show) return;
    const today = c.today;
    $("calTodayDate").textContent = dayMonth(Date.parse(today + "T12:00:00Z") / 1000);
    // the server only sends events still going today or later, so "started on or before today" = today's
    const todays = c.events.filter((e) => e.date <= today && (!e.all_day || e.last_date >= today));
    $("calToday").replaceChildren(...(todays.length ? todays.map((e) => calRow(e, true))
      : [el("li", "empty", "Nothing in the calendar today")]));

    const later = c.events.filter((e) => e.date > today).slice(0, CAL_NEXT_MAX);
    const groups = [];
    for (const e of later) {
      if (!groups.length || groups[groups.length - 1].date !== e.date) groups.push({ date: e.date, items: [] });
      groups[groups.length - 1].items.push(e);
    }
    const rest = c.events.filter((e) => e.date > today).length - later.length;
    const nodes = groups.map((g) => {
      const box = el("section", "cal-day");
      const list = el("ol", "cal-list");
      list.append(...g.items.map((e) => calRow(e, false)));
      box.append(el("h3", null, dayName(g.date)), list);
      return box;
    });
    if (!groups.length) nodes.push(el("p", "empty", `Nothing else in the next ${c.days - 1} days`));
    if (rest > 0) nodes.push(el("p", "more", `+ ${rest} more later`));
    $("calNext").replaceChildren(...nodes);

    const errors = c.calendars.filter((k) => k.error).map((k) => `${k.name}: ${k.error}`);
    $("calFoot").hidden = !errors.length;
    $("calFoot").textContent = errors.length ? "Couldn't update " + errors.join(" · ") : "";
  }

  let wxData = null;              // the latest forecast, for the weather popup

  async function loadWeather() {
    let w;
    try { w = await get("/api/weather"); } catch { return; }
    wxData = w;
    if (pop.kind === "weather") renderWeatherPop();
    const show = !!(w.enabled && w.now);
    if ($("weather").hidden === show) { $("weather").hidden = !show; dash.refresh(); }
    hu.weather = show;
    hu.extremes = (show && w.extremes) || [];
    renderHeadsUp();
    if (!show) return;
    const [icon, text] = wx(w.now.symbol);
    $("wxIcon").textContent = icon;
    $("wxPlace").textContent = w.place ? `Weather in ${w.place}` : "Weather now";
    $("wxTemp").textContent = deg(w.now.temp);
    const rain = w.now.precip_mm ? ` · ${num(w.now.precip_mm, f1)} mm/h` : "";
    $("wxDesc").textContent = `${text} · wind ${num(w.now.wind, f0)} m/s${rain}`;
    $("wxNext").replaceChildren(...w.next.map((p) => {
      const li = document.createElement("li");
      const [i, t] = wx(p.symbol);
      li.title = t;
      li.innerHTML = `<div class="t"></div><div class="i"></div><div class="v"></div><div class="p"></div><div class="w"></div>`;
      li.querySelector(".t").textContent = hm(p.ts);
      li.querySelector(".i").textContent = i;
      li.querySelector(".v").textContent = deg(p.temp) + "°";
      li.querySelector(".p").textContent = p.precip_mm ? `${num(p.precip_mm, f1)} mm` : "";
      li.querySelector(".w").textContent = p.wind == null ? "" : `${num(p.wind, f0)} m/s`;
      return li;
    }));
    renderDays(w.days || []);
    renderWarnings(w.warnings || []);
  }


  // Dashboards: one slide each, swipeable (CSS scroll-snap), auto-advancing every 10 s.
  // Any touch, wheel, key or dot click restarts the 10 s wait so a manual pick stays put for a while.
  const dash = (function () {
    const ROTATE_MS = 10000;
    const box = $("slides"), dotsEl = $("dots");
    let timer = null, held = false, idx = 0;
    const slides = () => [...box.children].filter((el) => !el.hidden);
    const current = () => Math.round(box.scrollLeft / (box.clientWidth || 1));

    function go(i, smooth = true) {
      const list = slides();
      if (!list.length) return;
      i = (i + list.length) % list.length;
      box.scrollTo({ left: i * box.clientWidth, behavior: smooth ? "smooth" : "auto" });
      mark(i);
    }
    function mark(i) {
      idx = i;
      [...dotsEl.children].forEach((d, k) => d.setAttribute("aria-selected", String(k === i)));
    }
    function restart() {
      clearTimeout(timer);
      if (held || document.hidden || dlg.open || slides().length < 2) return;
      timer = setTimeout(() => { go(idx + 1); restart(); }, ROTATE_MS);
    }
    function refresh() {
      const list = slides();
      dotsEl.hidden = list.length < 2;
      dotsEl.replaceChildren(...list.map((el, k) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "dot";
        b.setAttribute("role", "tab");
        b.setAttribute("aria-label", el.getAttribute("aria-label"));
        b.onclick = () => { go(k); restart(); };
        return b;
      }));
      go(Math.min(idx, list.length - 1), false);
      badges();
      restart();
    }
    function badges() {
      slides().forEach((el, k) => dotsEl.children[k]?.classList.toggle("alert", !!el.dataset.alert));
    }

    let scrollEnd;
    box.addEventListener("scroll", () => {
      clearTimeout(scrollEnd);
      scrollEnd = setTimeout(() => mark(current()), 80);
    }, { passive: true });
    box.addEventListener("pointerdown", () => { held = true; clearTimeout(timer); });
    window.addEventListener("pointerup", () => { if (held) { held = false; restart(); } });
    window.addEventListener("pointercancel", () => { if (held) { held = false; restart(); } });
    box.addEventListener("wheel", restart, { passive: true });
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input, textarea, select") || !$("drawer").hidden || dlg.open) return;
      if (e.key === "ArrowRight") { go(idx + 1); restart(); }
      else if (e.key === "ArrowLeft") { go(idx - 1); restart(); }
    });
    document.addEventListener("visibilitychange", restart);
    window.addEventListener("resize", () => go(idx, false));
    return { refresh, badges, restart };
  })();

  (async function boot() {
    dash.refresh();
    await loadStatus();
    await loadLive();
    setInterval(loadLive, 5000);
    setInterval(loadStatus, 120000);
    loadWeather();
    setInterval(loadWeather, 15 * 60000);
    loadOutlook();
    setInterval(loadOutlook, 5 * 60000);
    loadPrices();
    setInterval(loadPrices, 60000);         // moves "now" along
    let rt;
    window.addEventListener("resize", () => {
      clearTimeout(rt);
      rt = setTimeout(() => {
        renderPrices();
        if (pop.kind === "power") renderPowerPop();
        if (pop.kind === "weather") renderWeatherPop();
      }, 150);
    });
    loadCalendar();
    setInterval(loadCalendar, 60000);       // the server re-reads calendars every 15 min; this keeps "Now" current
  })();
})();
