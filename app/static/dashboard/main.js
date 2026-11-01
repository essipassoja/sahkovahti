// The dashboard: a carousel of pages, each in its own module. This starts them and keeps them fresh.
import { $, get, every, setTimeZone } from "./core.js";
import { carousel } from "./carousel.js";
import { loadLive, tickPrices } from "./electricity.js";
import { loadWeather } from "./weather.js";
import { loadCalendar } from "./calendar.js";
import { loadOutlook } from "./headsup.js";

// the meter badge in the header
async function loadStatus() {
  try {
    const s = await get("/api/status");
    setTimeZone(s.timezone);
    const m = s.meter;
    $("status").className = "status " + (m.simulated ? "sim" : m.connected ? "ok" : "bad");
    $("statusText").textContent = m.simulated ? "Simulator" : m.connected ? "Meter connected" : "Meter offline";
    $("simNote").hidden = !m.simulated;
  } catch {
    $("status").className = "status bad";
    $("statusText").textContent = "App not reachable";
  }
}

carousel.refresh();
await loadStatus();                 // the time zone, before anything shows a time
loadLive();
tickPrices();
loadWeather();
loadOutlook();
loadCalendar();

every(5000, loadLive);
every(60000, tickPrices);           // moves "now" along; fetches when there can be new prices
every(15 * 60000, loadWeather);     // MET updates about hourly; the server caches until then
every(5 * 60000, loadOutlook);
every(60000, loadCalendar);         // the server re-reads calendars every 15 min; this keeps "Now" current
every(120000, loadStatus);
