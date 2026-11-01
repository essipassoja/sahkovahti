// Weather page: weather now, the next 24 hours, the next 3 days and FMI warnings.
// Popup: one day at a time, today and the next days, with the forecast hour by hour.
import { $, el, get, num, f0, f1, deg, hm, isoDay, when, weekday, dayMonth, hourTicks, stat } from "./core.js";
import { carousel } from "./carousel.js";
import * as popup from "./popup.js";
import { setWeather } from "./headsup.js";

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

let data = null;              // the latest forecast

function renderDays(days) {
  $("wxDays").replaceChildren(...days.map((d, k) => {
    const li = el("li", "opens");
    const [i, t] = wx(d.symbol);
    li.title = t;
    popup.opens(li, () => openDay(k + 1));
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

function renderNext(next) {
  $("wxNext").replaceChildren(...next.map((p) => {
    const li = el("li"), [i, t] = wx(p.symbol);
    li.title = t;
    li.append(el("div", "t", hm(p.ts)), el("div", "i", i), el("div", "v", deg(p.temp) + "°"),
      el("div", "p", p.precip_mm ? `${num(p.precip_mm, f1)} mm` : ""),
      el("div", "w", p.wind == null ? "" : `${num(p.wind, f0)} m/s`));
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

export async function loadWeather() {
  let w;
  try { w = await get("/api/weather"); } catch { return; }
  data = w;
  const on = !!(w.enabled && w.now);
  carousel.show("weather", on);
  setWeather(on, (on && w.extremes) || []);
  popup.refresh(dayView);
  if (!on) return;
  const [icon, text] = wx(w.now.symbol);
  $("wxIcon").textContent = icon;
  $("wxPlace").textContent = w.place ? `Weather in ${w.place}` : "Weather now";
  $("wxTemp").textContent = deg(w.now.temp);
  const rain = w.now.precip_mm ? ` · ${num(w.now.precip_mm, f1)} mm/h` : "";
  $("wxDesc").textContent = `${text} · wind ${num(w.now.wind, f0)} m/s${rain}`;
  renderNext(w.next);
  renderDays(w.days || []);
  $("wxWarnings").replaceChildren(...(w.warnings || []).map((x) => warnBox(x)));
}

// ---------- popup: one day ----------
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compass = (d) => COMPASS[Math.round(d / 45) % 8];
const windArrow = (dir) => {      // points where the wind blows to
  const a = el("span", "arr", "↑");
  a.style.transform = `rotate(${dir + 180}deg)`;
  a.setAttribute("aria-label", `from ${compass(dir)}`);
  return a;
};
const span = (b) => (b.len > 3600 ? `${hm(b.ts)}–${hm(b.ts + b.len)}` : hm(b.ts));
const values = (rows, k) => rows.map((b) => b[k]).filter((x) => x != null);
const maxOf = (rows, k) => { const v = values(rows, k); return v.length ? Math.max(...v) : null; };
const minOf = (rows, k) => { const v = values(rows, k); return v.length ? Math.min(...v) : null; };
const joined = (parts) => parts.filter(Boolean).join(" · ") || null;

let day = 0;                  // 0 = today, 1 = tomorrow, ...
function openDay(k) {
  day = k;
  popup.open(dayView);
}

function tabs(list) {
  const box = el("div", "seg pop-tabs");
  box.setAttribute("role", "tablist");
  list.forEach((x, k) => {
    if (!x) return;
    const t = x.ts + 43200;
    const b = el("button", k === day ? "on" : null, k === 0 ? "Today" : k === 1 ? "Tomorrow" : `${weekday(t)} ${dayMonth(t)}`);
    b.type = "button";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(k === day));
    b.onclick = () => { day = k; dayView.render(); };
    box.append(b);
  });
  return box;
}

function figures(d, rows, today) {
  const stats = el("div", "pop-stats");
  stats.append(stat(today ? "Rest of today" : "Temperature", `${deg(d.tmax)}° / ${deg(d.tmin)}°`, "high / low"));
  const prob = maxOf(rows, "prob");
  stats.append(stat("Precipitation", `${num(d.precip_mm, f1)} mm`, joined([
    d.snow_mm >= 0.1 ? `${num(d.snow_mm, f1)} mm as snow` : null, prob != null ? `chance up to ${num(prob, f0)} %` : null])));
  const windiest = rows.reduce((a, b) => (b.wind != null && (a == null || b.wind > a.wind) ? b : a), null);
  stats.append(stat("Wind", `${num(d.wind, f0)} m/s`, joined([
    d.gust != null ? `gusts ${num(d.gust, f0)} m/s` : null, windiest?.dir != null ? `from ${compass(windiest.dir)}` : null])));
  const hLo = minOf(rows, "humidity"), hHi = maxOf(rows, "humidity");
  if (hHi != null) stats.append(stat("Humidity", hLo === hHi ? `${num(hHi, f0)} %` : `${num(hLo, f0)}–${num(hHi, f0)} %`));
  const clouds = values(rows, "cloud");
  if (clouds.length) stats.append(stat("Cloud cover", `${num(clouds.reduce((a, b) => a + b, 0) / clouds.length, f0)} %`, "on average"));
  const ps = values(rows, "pressure");
  if (ps.length) {
    const change = ps[ps.length - 1] - ps[0];
    stats.append(stat("Air pressure", `${num(ps[0], f0)} hPa`,
      ps.length < 2 ? null : Math.abs(change) < 2 ? "steady" : `${change > 0 ? "rising" : "falling"} to ${num(ps[ps.length - 1], f0)}`));
  }
  const uv = maxOf(rows, "uv");
  if (uv != null && uv >= 1) stats.append(stat("UV index", num(uv, f0), "highest, clear sky"));
  const thunder = maxOf(rows, "thunder");
  if (thunder != null && thunder >= 5) stats.append(stat("Thunder", `${num(thunder, f0)} %`, "highest chance"));
  return stats;
}

function table(rows) {
  const t = el("table", "wxp-table");
  t.innerHTML = `<thead><tr><th>Time</th><th>Weather</th><th>Temp</th><th>Precip.</th><th>Wind (gust) m/s</th>` +
    `<th class="opt">Humidity</th><th class="opt">Cloud</th></tr></thead>`;
  const tbody = el("tbody");
  for (const b of rows) {
    const tr = el("tr"), [i, txt] = wx(b.symbol);
    const sky = el("td", "sky");
    sky.append(el("span", "i", i), el("span", "txt", txt));
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
  t.append(tbody);
  const scroll = el("div", "wxp-scroll");
  scroll.append(t);
  return scroll;
}

const dayView = {
  render() {
    const w = data;
    popup.head("Weather", w?.place || "");
    if (!w?.now) return popup.note("Loading the forecast…");
    const list = [w.today, ...(w.days || [])];
    if (!list[day]) day = Math.max(0, list.findIndex(Boolean));
    const d = list[day], mid = d.ts + 43200, today = day === 0;
    const rows = (w.hourly || []).filter((b) => isoDay(b.ts) === d.date);

    // the day at a glance; today from the weather now
    const [icon, text] = wx(today ? w.now.symbol : d.symbol);
    const head = el("div", "wxp-head"), what = el("div"), h3 = el("h3", null, today ? "Today" : day === 1 ? "Tomorrow" : weekday(mid, "long"));
    h3.append(el("small", null, ` ${today ? weekday(mid, "long") + " " : ""}${dayMonth(mid)}`));
    what.append(h3, el("p", null, today ? `${text} · ${deg(w.now.temp)} °C now` : text));
    head.append(el("span", "wx-icon", icon), what);

    // warnings in effect at some point of the day
    const warns = (w.warnings || []).filter((x) => (x.onset ?? 0) < d.ts + 86400 && x.expires > d.ts).map((x) => warnBox(x, true));

    const credit = el("p", "wx-credit");
    credit.innerHTML = `Forecast from <a href="https://www.yr.no/" target="_blank" rel="noopener">Yr</a>, data by MET Norway` +
      (w.updated_at ? ` · updated ${when(Date.parse(w.updated_at) / 1000)}` : "") +
      (rows.some((b) => b.len > 3600) ? " · 6-hour periods further ahead, as the forecast gets coarser" : "");

    const chart = el("div", "chart");
    popup.body(tabs(list), head, figures(d, rows, today), ...warns, el("h4", "wxp-sub", "Temperature"), chart,
      el("h4", "wxp-sub", rows.every((b) => b.len === 3600) ? (today ? "Hour by hour, rest of today" : "Hour by hour") : "Through the day"),
      table(rows), credit);

    // temperature over the day, drawn in place so it knows its width
    const pts = rows.filter((b) => b.temp != null).map((b) => ({ t: b.ts, y: b.temp, b }));
    Charts.line(chart, pts, {
      height: Math.round(Math.max(160, Math.min(220, chart.clientWidth * 0.24))),
      fit: true, minSpan: 4, cls: "temp", xFmt: hm, yFmt: (v) => f0.format(v) + "°",
      xTicks: pts.length ? hourTicks(pts[0].t, pts[pts.length - 1].t, chart.clientWidth < 560) : [],
      tip: (p) => `<div class="t">${span(p.b)}</div>` +
        `<div class="r"><span>${wx(p.b.symbol)[1] || "Temperature"}</span><b>${num(p.y, f1)} °C</b></div>` +
        (p.b.precip ? `<div class="r"><span>${p.b.snow ? "Snow" : "Rain"}</span><b>${num(p.b.precip, f1)} mm</b></div>` : "") +
        (p.b.wind != null ? `<div class="r"><span>Wind</span><b>${num(p.b.wind, f0)} m/s</b></div>` : ""),
      label: "Temperature over the day, °C",
      emptyText: "Not enough forecast left for a chart",
    });
  },
};

popup.opens($("wxNowBox"), () => openDay(0));
$("wxNext").addEventListener("click", () => openDay(0));      // the next hours: today's details
