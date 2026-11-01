// Shared by the dashboard pages: DOM helpers, fetching, polling and number/date formatting.

export const $ = (id) => document.getElementById(id);

export const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

export async function get(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(url + " → " + r.status);
  return r.json();
}

// Run fn every ms, but not while the page is hidden (screen off, other tab): nothing to show,
// so no requests. Coming back runs whatever fell due in the meantime straight away.
const tasks = [];
export function every(ms, fn) {
  const task = { ms, fn, last: Date.now() };
  tasks.push(task);
  setInterval(() => {
    if (!document.hidden) { task.last = Date.now(); fn(); }
  }, ms);
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  for (const t of tasks) if (Date.now() - t.last >= t.ms) { t.last = Date.now(); t.fn(); }
});

// ---------- numbers ----------
const nf = (d) => new Intl.NumberFormat("fi-FI", { minimumFractionDigits: d, maximumFractionDigits: d });
export const f0 = nf(0), f1 = nf(1), f2 = nf(2);
export const num = (v, f = f2) => (v == null || Number.isNaN(v) ? "–" : f.format(v));
export const deg = (v) => (v == null ? "–" : f0.format(Math.round(v) || 0));
export const watts = (w, f = f2) => (w >= 1000 ? `${num(w / 1000, f)} kW` : `${num(w, f0)} W`);

// ---------- dates, in the app's time zone ----------
// Formatters are costly to create and charts format hundreds of times, so make them once per zone.
let fmt;
function zone(tz) {
  const f = (locale, o) => new Intl.DateTimeFormat(locale, { timeZone: tz, ...o });
  fmt = {
    tz,
    hm: f("fi-FI", { hour: "2-digit", minute: "2-digit" }),
    iso: f("en-CA", {}),
    short: f("en-GB", { weekday: "short" }),
    long: f("en-GB", { weekday: "long" }),
    dm: f("fi-FI", { day: "numeric", month: "numeric" }),
  };
}
zone("Europe/Helsinki");
export const setTimeZone = (tz) => { if (tz && tz !== fmt.tz) zone(tz); };

export const hm = (t) => fmt.hm.format(t * 1000);                 // "06.00"
export const isoDay = (t) => fmt.iso.format(t * 1000);            // "2026-11-01"
export const weekday = (t, style = "short") => fmt[style].format(t * 1000);
export const dayMonth = (t) => fmt.dm.format(t * 1000);           // "1.11."
const now = () => Date.now() / 1000;

// a time with its day when not today: "06.00", "06.00 tomorrow", "06.00 Sat"
export const when = (t) => {
  const d = isoDay(t);
  if (d === isoDay(now())) return hm(t);
  if (d === isoDay(now() + 86400)) return hm(t) + " tomorrow";
  return hm(t) + " " + weekday(t);
};
// a day relative to today: "today", "tomorrow" or "on Saturday"
export const onDay = (t) => {
  const d = isoDay(t);
  if (d === isoDay(now())) return "today";
  if (d === isoDay(now() + 86400)) return "tomorrow";
  return "on " + weekday(t + 43200, "long");
};

// x-axis ticks on full local hours: every 3 h, or every 6 h when the chart is narrow
export function hourTicks(t0, t1, narrow) {
  const out = [];
  for (let t = Math.ceil(t0 / 3600) * 3600; t <= t1; t += 3600) {
    if (+hm(t).slice(0, 2) % (narrow ? 6 : 3) === 0) out.push(t);
  }
  return out;
}

// one figure in a popup's row of figures
export const stat = (label, value, sub) => {
  const box = el("div");
  box.append(el("span", null, label), el("b", null, value));
  if (sub) box.append(el("small", null, sub));
  return box;
};
