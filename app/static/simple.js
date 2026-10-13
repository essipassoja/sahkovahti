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

  async function loadWeather() {
    let w;
    try { w = await get("/api/weather"); } catch { return; }
    if (!w.enabled || !w.now) { $("weather").hidden = true; return; }
    $("weather").hidden = false;
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
      li.innerHTML = `<div class="t"></div><div class="i"></div><div class="v"></div><div class="p"></div>`;
      li.querySelector(".t").textContent = hm(p.ts);
      li.querySelector(".i").textContent = i;
      li.querySelector(".v").textContent = deg(p.temp) + "°";
      li.querySelector(".p").textContent = p.precip_mm ? `${num(p.precip_mm, f1)} mm` : "";
      return li;
    }));
  }

  (async function boot() {
    await loadStatus();
    await loadLive();
    setInterval(loadLive, 5000);
    setInterval(loadStatus, 120000);
    loadWeather();
    setInterval(loadWeather, 15 * 60000);
  })();
})();
