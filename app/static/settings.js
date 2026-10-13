(function () {
  const $ = (id) => document.getElementById(id);
  const form = $("form");
  const nf = (d) => new Intl.NumberFormat("fi-FI", { minimumFractionDigits: d, maximumFractionDigits: d });
  const f1 = nf(1), f2 = nf(2);
  const num = (v) => (v == null || Number.isNaN(v) ? "–" : f2.format(v));
  let saved = "";   // JSON of the last saved/loaded form, to detect unsaved changes

  for (const sel of form.querySelectorAll("select[name$='_hour']")) {
    for (let h = 0; h <= 24; h++) sel.add(new Option(String(h).padStart(2, "0") + ":00", h));
  }

  // ---------- form <-> settings object ----------
  function fill(values) {
    for (const el of form.elements) {
      if (!el.name || !el.name.includes(".")) continue;
      const [sec, key] = el.name.split(".");
      const v = values[sec]?.[key];
      if (el.type === "checkbox") el.checked = !!v;
      else if (el.type === "radio") el.checked = el.value === v;
      else el.value = v ?? "";
    }
    showMode();
    updateSubLink();
  }

  function updateSubLink() {
    const n = read().notifications;
    const ok = /^[A-Za-z0-9_-]{1,64}$/.test(n.topic || "");
    $("subLinkWrap").hidden = !ok;
    if (ok) $("subLink").href = `${(n.server || "https://ntfy.sh").replace(/\/$/, "")}/${n.topic}`;
  }

  function read() {
    const out = { tariff: {}, traffic_light: {}, notifications: {}, weather: {} };
    for (const el of form.elements) {
      if (!el.name || !el.name.includes(".")) continue;
      const [sec, key] = el.name.split(".");
      if (el.type === "checkbox") out[sec][key] = el.checked;
      else if (el.type === "radio") { if (el.checked) out[sec][key] = el.value; }
      else if (el.type === "number" || el.tagName === "SELECT" && key.endsWith("_hour")) {
        out[sec][key] = el.value === "" ? null : Number(el.value.replace(",", "."));
      } else out[sec][key] = el.value;
    }
    return out;
  }

  function showMode() {
    const mode = form.querySelector("[name='tariff.transfer_mode']:checked")?.value || "flat";
    form.querySelectorAll("[data-mode]").forEach((el) => { el.hidden = !el.dataset.mode.split(" ").includes(mode); });
    const seasonal = mode === "seasonal";
    form.querySelectorAll("[data-peak-label]").forEach((el) => { el.textContent = seasonal ? "Winter weekday price" : "Day price"; });
    form.querySelectorAll("[data-offpeak-label]").forEach((el) => { el.textContent = seasonal ? "Other time price" : "Night price"; });
  }

  // ---------- server ----------
  async function send(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }

  const PERIOD = { flat: "", peak: " (day rate)", offpeak: " (night rate)" };
  const LIGHT = { green: "green", yellow: "yellow", red: "red" };
  function renderPreview(b) {
    const mode = read().tariff.transfer_mode;
    const period = mode === "seasonal" ? { flat: "", peak: " (winter weekday)", offpeak: " (other time)" } : PERIOD;
    const tr = (k, v, cls = "") => `<tr class="${cls}"><th>${k}</th><td>${v}</td></tr>`;
    if (b.spot == null) {
      $("breakdown").innerHTML = tr("Spot price", "no price for this slot");
    } else {
      const t = read().tariff;
      let energyLabel = "Your spot part";
      const notes = [];
      if (t.negative_spot_as_zero && b.spot < 0) notes.push("negative → 0");
      if (b.discount_active) notes.push(`−${f2.format(t.spot_discount_percent)} %`);
      else if (t.spot_discount_percent) notes.push("discount ended");
      if (notes.length) energyLabel += ` <span class="muted">(${notes.join(", ")})</span>`;
      $("breakdown").innerHTML =
        tr("Spot price now <span class=\"muted\">(alv 0 %)</span>", num(b.spot) + " c") +
        tr(energyLabel, num(b.energy) + " c") +
        tr("+ Margin", num(b.margin) + " c") +
        tr("+ Transfer" + period[b.transfer_period], num(b.transfer) + " c") +
        tr("+ Electricity tax", num(b.tax) + " c") +
        tr(`+ VAT ${f1.format(b.vat_percent)} %`, num(b.vat) + " c") +
        tr("= You pay", num(b.total) + " c/kWh", "sum");
    }
    const fees = b.monthly_fixed_eur != null
      ? `Basic fees now: <b>${num(b.monthly_fixed_eur)} €/month</b> incl. VAT${b.intro_fee_active ? " (intro fee)" : ""}`
      : "";
    const light = b.light ? `Traffic light now: <span class="light-dot ${b.light}"></span><b>${LIGHT[b.light]}</b>` : "";
    $("previewNote").innerHTML = [light, fees].filter(Boolean).join("<br>") || "&nbsp;";
  }

  let pt, seq = 0;
  function preview() {
    clearTimeout(pt);
    pt = setTimeout(async () => {
      const my = ++seq;
      try {
        const b = await send("/api/settings/preview", read());
        if (my !== seq) return;
        renderPreview(b);
        setMsg(dirty() ? "Unsaved changes" : "", dirty() ? "warn" : "");
      } catch (e) {
        if (my === seq) setMsg(friendly(e.message), "bad");
      }
    }, 250);
  }

  const dirty = () => JSON.stringify(read()) !== saved;
  function setMsg(text, cls = "") { $("msg").textContent = text || " "; $("msg").className = "msg " + cls; }
  // "retailer_margin_c_kwh must be a number" → "Margin must be a number"
  function friendly(msg) {
    return msg.replace(/^[a-z_]+(?= )/, (key) => {
      const el = form.querySelector(`[name$='.${key}']`);
      const box = el && el.closest(".field, .check");
      const text = box && [...box.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
      return text ? text.textContent.trim() : key;
    });
  }

  async function load() {
    const r = await fetch("/api/settings", { cache: "no-store" });
    const d = await r.json();
    fill(d.values);
    // a freshly suggested topic counts as a change, so the page reminds you to save it
    saved = d.topic_suggested ? "" : JSON.stringify(read());
    renderPreview(d.now);
    showNotifier(d.notifier);
    if (dirty()) setMsg("Save to keep the suggested notification topic", "warn");
  }

  function showNotifier(n) {
    if (!n) return;
    const t = (s) => new Date(s * 1000).toLocaleString("fi-FI", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "numeric" });
    if (n.last_error) setTest(`Last notification failed: ${n.last_error}`, "bad");
    else if (n.last_sent) setTest(`Last sent ${t(n.last_sent.at)}: ${n.last_sent.title}`);
  }
  function setTest(text, cls = "") { $("testMsg").textContent = text || " "; $("testMsg").className = "msg " + cls; }

  $("useHere").onclick = () => {
    form.elements["notifications.click_url"].value = location.origin;
    preview();
  };
  $("testBtn").onclick = async () => {
    $("testBtn").disabled = true;
    setTest("Sending…");
    try {
      const d = await send("/api/settings/test-notification", read());
      setTest(`Sent to topic "${d.topic}" — check your phone.`, "ok");
    } catch (err) {
      setTest(friendly(err.message), "bad");
    } finally {
      $("testBtn").disabled = false;
    }
  };

  // ---------- weather location ----------
  function setWx(text, cls = "") { $("wxMsg").textContent = text; $("wxMsg").className = "msg " + cls; }
  function useLocation(lat, lon, name) {
    form.elements["weather.latitude"].value = lat.toFixed(4);
    form.elements["weather.longitude"].value = lon.toFixed(4);
    if (name != null) form.elements["weather.place"].value = name;
    $("wxResults").hidden = true;
    setWx("Location set. Save to use it on the front page.", "ok");
    preview();
  }
  async function searchPlace() {
    const q = $("wxQuery").value.trim();
    if (q.length < 2) { setWx("Type a town or an address first.", "warn"); return; }
    $("wxSearch").disabled = true;
    setWx("Searching…");
    try {
      const r = await fetch("/api/geocode?q=" + encodeURIComponent(q), { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "HTTP " + r.status);
      const list = $("wxResults");
      list.replaceChildren(...d.results.map((p) => {
        const li = document.createElement("li"), b = document.createElement("button"), sm = document.createElement("small");
        b.type = "button";
        b.textContent = p.name;
        sm.textContent = p.label;
        b.append(sm);
        b.onclick = () => useLocation(p.latitude, p.longitude, p.name);
        li.append(b);
        return li;
      }));
      list.hidden = !d.results.length;
      setWx(d.results.length ? "Pick the right one:" : "Nothing found. Try another spelling or a nearby town.",
        d.results.length ? "" : "warn");
    } catch (err) {
      setWx(err.message, "bad");
    } finally {
      $("wxSearch").disabled = false;
    }
  }
  $("wxSearch").onclick = searchPlace;
  $("wxQuery").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); searchPlace(); } });
  // browsers only share the location over https or on localhost
  if (navigator.geolocation && window.isSecureContext) {
    $("wxHere").hidden = false;
    $("wxHere").onclick = () => {
      setWx("Asking for this device's location…");
      navigator.geolocation.getCurrentPosition(
        (pos) => useLocation(pos.coords.latitude, pos.coords.longitude, null),
        (err) => setWx("Could not get the location: " + err.message, "bad"),
        { timeout: 15000 });
    };
  }

  form.addEventListener("input", (e) => {
    if (e.target.name === "tariff.transfer_mode") showMode();
    if (e.target.name?.startsWith("notifications.")) updateSubLink();
    preview();
  });
  form.addEventListener("change", (e) => { if (e.target.name === "tariff.transfer_mode") showMode(); preview(); });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    $("saveBtn").disabled = true;
    try {
      const d = await send("/api/settings", read());
      fill(d.values);
      saved = JSON.stringify(read());
      renderPreview(d.now);
      setMsg("Saved ✓", "ok");
    } catch (err) {
      setMsg(friendly(err.message), "bad");
    } finally {
      $("saveBtn").disabled = false;
    }
  });

  $("resetBtn").onclick = () => { $("confirmReset").hidden = false; $("resetBtn").hidden = true; };
  $("resetNo").onclick = () => { $("confirmReset").hidden = true; $("resetBtn").hidden = false; };
  $("resetYes").onclick = async () => {
    $("resetNo").onclick();
    try {
      const d = await send("/api/settings/reset");
      fill(d.values);
      saved = JSON.stringify(read());
      renderPreview(d.now);
      setMsg("Reset to config.toml values", "ok");
    } catch (err) { setMsg(err.message, "bad"); }
  };

  window.addEventListener("beforeunload", (e) => { if (dirty()) e.preventDefault(); });

  load().catch(() => setMsg("App not reachable", "bad"));
})();
