(function () {
  const $ = (id) => document.getElementById(id);
  const state = { tz: "Europe/Helsinki", day: null, unit: "kwh", hunit: "kwh", powerMin: 180, dayData: null, hist: null, status: null };

  // ---------- formatting ----------
  const nf = (d) => new Intl.NumberFormat("fi-FI", { minimumFractionDigits: d, maximumFractionDigits: d });
  const f0 = nf(0), f1 = nf(1), f2 = nf(2), f3 = nf(3);
  const num = (v, f = f2, dash = "–") => (v == null || Number.isNaN(v) ? dash : f.format(v));
  const tf = () => new Intl.DateTimeFormat("fi-FI", { timeZone: state.tz, hour: "2-digit", minute: "2-digit" });
  const hm = (t) => tf().format(new Date(t * 1000));
  const dayName = (t) => new Intl.DateTimeFormat("en-GB", { timeZone: state.tz, weekday: "short", day: "numeric", month: "short" }).format(new Date(t * 1000));
  const localParts = (t) => {
    const p = new Intl.DateTimeFormat("en-GB", { timeZone: state.tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(t * 1000));
    return { h: +p.find((x) => x.type === "hour").value, m: +p.find((x) => x.type === "minute").value };
  };
  const todayIso = () => new Intl.DateTimeFormat("en-CA", { timeZone: state.tz }).format(new Date());
  const shiftIso = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const row = (k, v) => `<div class="r"><span>${k}</span><b>${v}</b></div>`;

  async function get(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(url + " → " + r.status);
    return r.json();
  }

  // ---------- status ----------
  async function loadStatus() {
    try {
      const s = await get("/api/status");
      state.status = s;
      state.tz = s.timezone || state.tz;
      const t = s.tariff;
      $("adders").textContent = num(t.adders_c_kwh, f2) + " c" +
        (t.discount_active ? ` (and you get −${num(t.spot_discount_percent, f0)} % off spot)` : "");
      const st = $("status"), m = s.meter;
      st.className = "status " + (m.simulated ? "sim" : m.connected ? "ok" : "bad");
      $("statusText").textContent = m.simulated ? "Simulator" : m.connected ? `Meter connected (${m.source})` : `Meter offline${m.error ? " — " + m.error : ""}`;
      $("simBanner").hidden = !m.simulated;
      const known = s.prices.known_until ? `prices known until ${dayName(s.prices.known_until - 1)} ${hm(s.prices.known_until)}` : "no prices yet";
      $("foot").textContent = `${known}${s.prices.error ? " · price fetch error: " + s.prices.error : ""} · spot prices: porssisahko.net (Nord Pool) · ${f0.format(s.readings)} readings stored`;
    } catch (e) {
      $("status").className = "status bad"; $("statusText").textContent = "App not reachable";
    }
  }

  // ---------- live tiles ----------
  async function loadLive() {
    let d;
    try { d = await get("/api/live"); } catch { return; }
    const p = d.power_w;
    if (p != null) {
      const kw = p >= 1000;
      $("powerNow").textContent = kw ? f2.format(p / 1000) : f0.format(p);
      $("powerUnit").textContent = kw ? "kW" : "W";
    }
    $("priceNow").textContent = num(d.total, f2);
    $("priceSub").textContent = d.spot != null
      ? `spot ${num(d.spot, f2)} · next 15 min ${num(d.next_spot, f2)}`
      : "no price for this slot";
    $("rateNow").textContent = num(d.cost_rate_eur_h, f2);
    $("quarterSub").textContent = d.quarter.kwh != null ? `this 15 min: ${num(d.quarter.cost_eur * 100, f1)} c so far` : " ";
    const t = d.today;
    $("todayCost").textContent = num(t.cost_eur, f2);
    $("todaySub").textContent = `${num(t.kwh, f1)} kWh · avg ${num(t.avg_total_c_kwh, f1)} c/kWh`;
    Charts.spark($("spark"), d.recent);
    renderPhases(d.phases);
  }

  function renderPhases(ph) {
    const box = $("phases");
    if (!ph || !Object.keys(ph).length) { box.innerHTML = ""; return; }
    const max = 25; // A, typical main fuse
    box.innerHTML = [1, 2, 3].map((i) => {
      const a = ph[`l${i}_a`], v = ph[`l${i}_v`], kw = ph[`l${i}_kw`];
      const pct = a != null ? Math.min(100, (a / max) * 100) : 0;
      return `<div class="phase">L${i}<b>${num(a, f1)} A</b>${num(v, f0)} V · ${num(kw, f2)} kW
        <div class="meter" title="share of a 25 A main fuse"><span style="width:${pct}%"></span></div></div>`;
    }).join("");
  }

  // ---------- day view ----------
  async function loadDay() {
    const iso = state.day || todayIso();
    $("dayPick").value = iso;
    try { state.dayData = await get("/api/day?date=" + iso); } catch { return; }
    renderDay();
  }

  function renderDay() {
    const d = state.dayData;
    if (!d) return;
    const sl = d.slots;
    const nowIdx = sl.findIndex((s) => s.state === "now");
    const xLabel = (i) => { const { h, m } = localParts(sl[i].start); return m === 0 && h % 3 === 0 ? String(h).padStart(2, "0") : null; };
    const slotTip = (s) => {
      const head = `<div class="t">${hm(s.start)}–${hm(s.start + 900)}</div>`;
      return head + row("Spot", s.spot != null ? num(s.spot, f2) + " c/kWh" : "–") +
        row("Your total", s.total != null ? num(s.total, f2) + " c/kWh" : "–") +
        row("Used", s.kwh != null ? num(s.kwh, f3) + " kWh" : "–") +
        row("Cost", s.cost_eur != null ? num(s.cost_eur * 100, f1) + " c" : "–");
    };
    const avg = d.summary.avg_spot;
    Charts.bars($("priceChart"), sl.map((s) => ({ y: s.spot, cls: "bar-price", future: s.state === "future", tip: slotTip(s) })), {
      height: 170, yFmt: (v) => f0.format(v), xLabel, nowIndex: nowIdx, nowLabel: "now",
      ref: avg != null ? { value: avg, label: `day avg ${num(avg, f1)}` } : null,
      label: "Spot price per 15 minutes", emptyText: "No prices for this day yet (next day is published around 14:00)",
    });
    const eur = state.unit === "eur";
    $("useTitle").innerHTML = eur
      ? `Cost per 15 min <span class="muted">cents, incl. margin, transfer & tax</span>`
      : `Consumption per 15 min <span class="muted">kWh</span>`;
    Charts.bars($("useChart"), sl.map((s) => ({ y: eur ? (s.cost_eur != null ? s.cost_eur * 100 : null) : s.kwh, cls: "bar-use", tip: slotTip(s) })), {
      height: 150, yFmt: (v) => (eur ? f0.format(v) : f2.format(v)), xLabel, nowIndex: nowIdx,
      label: eur ? "Cost per 15 minutes" : "Consumption per 15 minutes", emptyText: "No meter readings for this day",
    });

    const s = d.summary;
    const ben = s.timing_benefit_c_kwh;
    $("daySummary").innerHTML =
      `<span>Used <b>${num(s.kwh, f1)} kWh</b></span>` +
      `<span>Cost <b>${num(s.cost_eur, f2)} €</b></span>` +
      `<span>You paid <b>${num(s.avg_total_c_kwh, f2)} c/kWh</b> on average</span>` +
      `<span>Spot: avg <b>${num(s.avg_spot, f2)}</b> · min <b>${num(s.min_spot, f2)}</b> · max <b>${num(s.max_spot, f2)}</b></span>` +
      (ben != null ? `<span>Timing ${ben >= 0 ? "saved" : "cost"} <b>${num(Math.abs(ben), f2)} c/kWh</b> vs. flat use</span>` : "");

    $("dayTable").innerHTML = `<div class="tbl-scroll"><table><thead><tr><th>Time</th><th>Spot c/kWh</th><th>Total c/kWh</th><th>kWh</th><th>Cost c</th></tr></thead><tbody>` +
      sl.map((s) => `<tr><td>${hm(s.start)}</td><td>${num(s.spot, f2)}</td><td>${num(s.total, f2)}</td><td>${num(s.kwh, f3)}</td><td>${s.cost_eur != null ? num(s.cost_eur * 100, f2) : "–"}</td></tr>`).join("") +
      `</tbody></table></div>`;
  }

  // ---------- power ----------
  async function loadPower() {
    let d;
    try { d = await get("/api/power?minutes=" + state.powerMin); } catch { return; }
    const pts = d.points.map(([t, p]) => ({ t, y: p }));
    const now = Date.now() / 1000, t0 = now - state.powerMin * 60;
    const stepH = state.powerMin <= 60 ? 900 : state.powerMin <= 180 ? 1800 : 3 * 3600;
    const ticks = []; for (let t = Math.ceil(t0 / stepH) * stepH; t <= now; t += stepH) ticks.push(t);
    $("liveTitle").textContent = `Power, last ${state.powerMin >= 1440 ? "24 hours" : state.powerMin / 60 + (state.powerMin === 60 ? " hour" : " hours")}`;
    Charts.line($("powerChart"), pts, {
      height: 200, tMin: t0, tMax: now, xTicks: ticks, xFmt: hm,
      yFmt: (v) => (v >= 1000 ? f1.format(v / 1000) + "k" : f0.format(v)),
      tip: (p) => `<div class="t">${hm(p.t)}</div>` + row("Power", p.y >= 1000 ? num(p.y / 1000, f2) + " kW" : num(p.y, f0) + " W"),
      label: "Power over time, watts",
    });
  }

  // ---------- cheapest windows ----------
  async function loadCheapest() {
    let d;
    try { d = await get("/api/cheapest"); } catch { return; }
    const today = todayIso();
    $("windows").innerHTML = d.windows.length ? d.windows.map((w) => {
      const sameDay = new Intl.DateTimeFormat("en-CA", { timeZone: state.tz }).format(new Date(w.start * 1000)) === today;
      return `<li><span class="dur">${w.hours} h</span>
        <span class="when">${hm(w.start)}–${hm(w.end)}<small>${sameDay ? "today" : dayName(w.start)}</small></span>
        <span class="p">${num(w.avg_total, f2)} c<small>spot ${num(w.avg_spot, f2)}</small></span></li>`;
    }).join("") : `<li><span></span><span class="when">No upcoming prices yet</span><span></span></li>`;
  }

  // ---------- history ----------
  async function loadHistory() {
    try { state.hist = await get("/api/history?days=30"); } catch { return; }
    renderHistory();
  }
  function renderHistory() {
    const h = state.hist;
    if (!h) return;
    const mo = h.month;
    $("monthLabel").textContent = "This month";
    $("monthCost").textContent = mo.cost_eur != null ? num(mo.cost_eur + (mo.fixed_eur || 0), f2) : "–";
    const partial = mo.price_coverage != null && mo.price_coverage < 0.99;
    $("monthSub").textContent = `${num(mo.kwh, f0)} kWh` + (partial ? ` · € covers ${f0.format(mo.price_coverage * 100)} % (prices stored since app start)` : "") +
      (mo.fixed_eur ? ` · incl. ${num(mo.fixed_eur, f2)} € fixed` : "");
    const key = { eur: "cost_eur", kwh: "kwh", price: "avg_total_c_kwh" }[state.hunit];
    const days = h.days;
    const today = todayIso();
    Charts.bars($("histChart"), days.map((d) => ({
      y: d.kwh ? d[key] ?? null : null, cls: "bar-use", future: d.date === today,
      tip: `<div class="t">${new Date(d.date + "T12:00:00Z").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}${d.date === today ? " (so far)" : ""}</div>` +
        row("Used", num(d.kwh, f1) + " kWh") + row("Cost", d.cost_eur != null ? num(d.cost_eur, f2) + " €" + (d.price_coverage < 0.99 ? " (partial)" : "") : "no prices stored") +
        row("You paid", num(d.avg_total_c_kwh, f2) + " c/kWh") + row("Avg spot", num(d.avg_spot, f2) + " c/kWh"),
    })), {
      height: 170, yFmt: (v) => (state.hunit === "eur" ? f0.format(v) : f0.format(v)),
      xLabel: (i) => { const dd = new Date(days[i].date + "T12:00:00Z"); return dd.getUTCDay() === 1 ? `${dd.getUTCDate()}.${dd.getUTCMonth() + 1}.` : null; },
      onClick: (i) => { state.day = days[i].date; loadDay(); window.scrollTo({ top: 0, behavior: "smooth" }); },
      label: "Daily totals for the last 30 days",
      emptyText: "No priced days yet — costs appear as the app collects prices day by day",
    });
  }

  // ---------- controls ----------
  document.querySelectorAll("[data-unit]").forEach((b) => b.addEventListener("click", () => {
    state.unit = b.dataset.unit;
    document.querySelectorAll("[data-unit]").forEach((x) => x.classList.toggle("on", x === b));
    renderDay();
  }));
  document.querySelectorAll("[data-hunit]").forEach((b) => b.addEventListener("click", () => {
    state.hunit = b.dataset.hunit;
    document.querySelectorAll("[data-hunit]").forEach((x) => x.classList.toggle("on", x === b));
    renderHistory();
  }));
  document.querySelectorAll("[data-min]").forEach((b) => b.addEventListener("click", () => {
    state.powerMin = +b.dataset.min;
    document.querySelectorAll("[data-min]").forEach((x) => x.classList.toggle("on", x === b));
    loadPower();
  }));
  $("prevDay").onclick = () => { state.day = shiftIso(state.day || todayIso(), -1); loadDay(); };
  $("nextDay").onclick = () => { state.day = shiftIso(state.day || todayIso(), 1); loadDay(); };
  $("todayBtn").onclick = () => { state.day = null; loadDay(); };
  $("dayPick").onchange = (e) => { state.day = e.target.value || null; loadDay(); };

  let rt;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { renderDay(); renderHistory(); loadPower(); }, 150); });

  // ---------- boot & polling ----------
  (async function boot() {
    await loadStatus();
    await Promise.all([loadLive(), loadDay(), loadPower(), loadCheapest(), loadHistory()]);
    setInterval(loadLive, 5000);
    setInterval(() => { if (!state.day || state.day === todayIso()) loadDay(); loadPower(); }, 60000);
    setInterval(() => { loadStatus(); loadCheapest(); }, 120000);
    setInterval(loadHistory, 600000);
  })();
})();
