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

  function renderLight(L) {
    const level = L.level || "none";
    $("lightCard").dataset.level = level;
    const [title, advice] = TEXT[level];
    $("verdict").textContent = title;
    $("advice").textContent = advice;

    let next = "";
    if (L.next) next = `Turns ${NAME[L.next.level]} at ${when(L.next.at)}`;
    else if (L.level) next = `Stays ${NAME[L.level]} for all prices published so far`;
    if (L.level && L.level !== "green") {
      if (L.next_green && (!L.next || L.next_green !== L.next.at)) next += ` · green from ${when(L.next_green)}`;
      else if (!L.next_green) next += " · no green slots in published prices";
    }
    $("nextChange").textContent = next || " ";

    const basis = L.basis === "spot" ? "your spot price" : "your total price";
    $("legend").textContent = `Based on ${basis}${L.price != null ? ` (now ${num(L.price, f2)} c/kWh)` : ""}: ` +
      `green under ${num(L.green_below, f0)} c · red over ${num(L.red_above, f0)} c`;
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
    $("wxDays").replaceChildren(...days.map((d) => {
      const li = el("li");
      const [i, t] = wx(d.symbol);
      li.title = t;
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

  function renderWarnings(list) {
    const now = Date.now() / 1000;
    $("wxWarnings").replaceChildren(...list.map((w) => {
      const box = el("article", "wx-warn");
      box.dataset.color = w.color;
      const head = el("div", "wh");
      head.append(el("span", "wi", "⚠️"), el("strong", null, w.headline || w.event || "Weather warning"));
      if (w.onset != null && w.onset <= now) head.append(el("span", "tag", "In effect"));
      box.append(head);
      if (w.impacts) box.append(el("p", null, w.impacts));
      if (w.description) box.title = w.description;
      return box;
    }));
  }

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

  async function loadWeather() {
    let w;
    try { w = await get("/api/weather"); } catch { return; }
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
      if (held || document.hidden || slides().length < 2) return;
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
      if (e.target.closest("input, textarea, select") || !$("drawer").hidden) return;
      if (e.key === "ArrowRight") { go(idx + 1); restart(); }
      else if (e.key === "ArrowLeft") { go(idx - 1); restart(); }
    });
    document.addEventListener("visibilitychange", restart);
    window.addEventListener("resize", () => go(idx, false));
    return { refresh, badges };
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
  })();
})();
