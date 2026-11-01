// Calendar page: today on the left, the next days on the right, from the iCal addresses in Settings.
import { $, el, get, hm, isoDay, weekday, dayMonth } from "./core.js";
import { carousel } from "./carousel.js";

const NEXT_MAX = 8;           // keeps the page within a tablet screen
const noonOf = (iso) => Date.parse(iso + "T12:00:00Z") / 1000;
const dayName = (iso) => {
  if (iso === isoDay(Date.now() / 1000 + 86400)) return "Tomorrow";
  return weekday(noonOf(iso), "long") + " " + dayMonth(noonOf(iso));
};

function row(e, today) {
  const li = el("li");
  li.dataset.cal = e.calendar % 6;
  const now = Date.now() / 1000;
  const time = el("div", "ct");
  if (e.all_day) {
    time.textContent = "All day";
    if (e.last_date !== e.date) time.append(el("small", null, `until ${weekday(noonOf(e.last_date))}`));
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

export async function loadCalendar() {
  let c;
  try { c = await get("/api/calendar"); } catch { return; }
  carousel.show("calendar", !!c.enabled);
  if (!c.enabled) return;
  const today = c.today;
  $("calTodayDate").textContent = dayMonth(noonOf(today));
  // the server only sends events still going today or later, so "started on or before today" = today's
  const todays = c.events.filter((e) => e.date <= today && (!e.all_day || e.last_date >= today));
  $("calToday").replaceChildren(...(todays.length ? todays.map((e) => row(e, true))
    : [el("li", "empty", "Nothing in the calendar today")]));

  const upcoming = c.events.filter((e) => e.date > today);
  const groups = [];
  for (const e of upcoming.slice(0, NEXT_MAX)) {
    if (groups.at(-1)?.date !== e.date) groups.push({ date: e.date, items: [] });
    groups.at(-1).items.push(e);
  }
  const nodes = groups.map((g) => {
    const box = el("section", "cal-day");
    const list = el("ol", "cal-list");
    list.append(...g.items.map((e) => row(e, false)));
    box.append(el("h3", null, dayName(g.date)), list);
    return box;
  });
  if (!groups.length) nodes.push(el("p", "empty", `Nothing else in the next ${c.days - 1} days`));
  if (upcoming.length > NEXT_MAX) nodes.push(el("p", "more", `+ ${upcoming.length - NEXT_MAX} more later`));
  $("calNext").replaceChildren(...nodes);

  const errors = c.calendars.filter((k) => k.error).map((k) => `${k.name}: ${k.error}`);
  $("calFoot").hidden = !errors.length;
  $("calFoot").textContent = errors.length ? "Couldn't update " + errors.join(" · ") : "";
}
