// Heads-up page: dramatic weather in the next days and tomorrow's electricity, or a calm picture.
// The weather part comes from the Weather page's data (setWeather), tomorrow's prices from /api/outlook.
import { $, el, get, num, f0, f1, deg, hm, when, onDay, weekday } from "./core.js";
import { carousel } from "./carousel.js";

const hu = { weather: false, extremes: [], tomorrow: undefined };

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

function render() {
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
  carousel.badges();
}

// from the Weather page: whether weather is on, and what dramatic weather is ahead
export function setWeather(on, extremes) {
  hu.weather = on;
  hu.extremes = extremes;
  render();
}

export async function loadOutlook() {
  try { hu.tomorrow = (await get("/api/outlook")).tomorrow; } catch { return; }
  render();
}
