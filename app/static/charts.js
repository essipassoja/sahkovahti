/* Minimal dependency-free SVG charts: bars with hover tooltips, and a line with crosshair.
   One y-axis per chart, recessive grid, thin marks, rounded data ends. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const tip = () => document.getElementById("tip");

  function el(name, attrs, parent) {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function niceTicks(min, max, count = 4) {
    if (max === min) max = min + 1;
    const raw = (max - min) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
    return { lo, hi, ticks };
  }

  function showTip(html, evt) {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    const pad = 14, w = t.offsetWidth, h = t.offsetHeight;
    let x = evt.clientX + pad, y = evt.clientY - h - pad;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
    if (y < 8) y = evt.clientY + pad;
    t.style.left = x + "px";
    t.style.top = y + "px";
  }
  function hideTip() { tip().hidden = true; }

  // Rounded only at the data end (top for positive, bottom for negative), square at the baseline.
  function barPath(x, y0, y1, w, r) {
    const up = y1 < y0;
    const hgt = Math.abs(y1 - y0);
    r = Math.min(r, w / 2, hgt);
    if (hgt < 0.5) return "";
    if (up) {
      return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z`;
    }
    return `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 - r}V${y0}Z`;
  }

  /**
   * items: [{y, cls, future, tip: html}]  (y may be null = no data)
   * opts:  {height, yFmt, xLabel(i) -> string|null, nowIndex, nowLabel, ref:{value,label}, onClick(i), emptyText}
   */
  function bars(container, items, opts = {}) {
    const W = Math.max(280, container.clientWidth);
    const H = opts.height || 180;
    const m = { l: 40, r: 8, t: 14, b: 22 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    container.innerHTML = "";
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": opts.label || "" }, container);

    const vals = items.map((d) => d.y).filter((v) => v != null);
    if (!vals.length) {
      el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "empty" }, svg).textContent = opts.emptyText || "No data";
      return;
    }
    let yMin = Math.min(0, ...vals), yMax = Math.max(0, ...vals);
    if (opts.ref && opts.ref.value != null) yMax = Math.max(yMax, opts.ref.value);
    const { lo, hi, ticks } = niceTicks(yMin, yMax, opts.ticks || 4);
    const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;
    const step = iw / items.length;
    const gap = step >= 6 ? 2 : step >= 3 ? 1 : 0;   // surface gap between adjacent bars
    const bw = Math.max(1, step - gap);

    const g = el("g", { class: "grid" }, svg);
    const ax = el("g", { class: "axis" }, svg);
    for (const t of ticks) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }, g);
      el("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" }, ax).textContent = (opts.yFmt || String)(t);
    }
    if (opts.xLabel) {
      items.forEach((_, i) => {
        const lab = opts.xLabel(i);
        if (lab != null) el("text", { x: m.l + i * step, y: H - 6, "text-anchor": "start" }, ax).textContent = lab;
      });
    }

    const hl = el("rect", { class: "hl", x: 0, y: m.t, width: step, height: ih, visibility: "hidden" }, svg);
    const marks = el("g", {}, svg);
    items.forEach((d, i) => {
      if (d.y == null) return;
      const p = barPath(m.l + i * step + gap / 2, y(0), y(d.y), bw, 3);
      if (p) el("path", { d: p, class: (d.cls || "") + (d.future ? " future" : "") }, marks);
    });
    el("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }, svg);

    if (opts.ref && opts.ref.value != null) {
      el("line", { class: "ref", x1: m.l, x2: W - m.r, y1: y(opts.ref.value), y2: y(opts.ref.value) }, svg);
      el("text", { class: "reflabel", x: W - m.r, y: y(opts.ref.value) - 4, "text-anchor": "end" }, svg).textContent = opts.ref.label;
    }
    if (opts.nowIndex != null && opts.nowIndex >= 0 && opts.nowIndex < items.length) {
      const x = m.l + opts.nowIndex * step + step / 2;
      el("line", { class: "nowline", x1: x, x2: x, y1: m.t - 4, y2: m.t + ih }, svg);
      if (opts.nowLabel) el("text", { class: "nowlabel", x: x + 4, y: m.t + 2 }, svg).textContent = opts.nowLabel;
    }

    // hit targets: full column height, wider than the mark
    const hits = el("g", {}, svg);
    items.forEach((d, i) => {
      const r = el("rect", { class: "hit", x: m.l + i * step, y: m.t, width: step, height: ih }, hits);
      r.addEventListener("pointermove", (e) => {
        hl.setAttribute("x", m.l + i * step); hl.setAttribute("visibility", "visible");
        if (d.tip) showTip(d.tip, e);
      });
      r.addEventListener("pointerleave", () => { hl.setAttribute("visibility", "hidden"); hideTip(); });
      if (opts.onClick) r.addEventListener("click", () => opts.onClick(i));
    });
  }

  /** points: [{t, y}] sorted by t;  opts: {height, yFmt, xFmt(t), xTicks:[t], tip(point) -> html} */
  function line(container, points, opts = {}) {
    const W = Math.max(280, container.clientWidth);
    const H = opts.height || 200;
    const m = { l: 44, r: 10, t: 12, b: 22 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    container.innerHTML = "";
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": opts.label || "" }, container);
    if (points.length < 2) {
      el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "empty" }, svg).textContent = opts.emptyText || "Waiting for data…";
      return;
    }
    const t0 = opts.tMin ?? points[0].t, t1 = opts.tMax ?? points[points.length - 1].t;
    const { lo, hi, ticks } = niceTicks(0, Math.max(...points.map((p) => p.y)), 4);
    const x = (t) => m.l + ((t - t0) / (t1 - t0 || 1)) * iw;
    const y = (v) => m.t + ih - ((v - lo) / (hi - lo)) * ih;

    const g = el("g", { class: "grid" }, svg), ax = el("g", { class: "axis" }, svg);
    for (const t of ticks) {
      el("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }, g);
      el("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end" }, ax).textContent = (opts.yFmt || String)(t);
    }
    for (const t of opts.xTicks || []) {
      el("text", { x: x(t), y: H - 6, "text-anchor": "middle" }, ax).textContent = opts.xFmt(t);
    }
    let d = "";
    points.forEach((p, i) => { d += (i ? "L" : "M") + x(p.t).toFixed(1) + "," + y(p.y).toFixed(1); });
    el("path", { class: "area", d: d + `L${x(points[points.length - 1].t)},${y(0)}L${x(points[0].t)},${y(0)}Z` }, svg);
    el("path", { class: "line", d }, svg);
    el("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: y(0), y2: y(0) }, svg);

    const cross = el("line", { class: "nowline", y1: m.t, y2: m.t + ih, visibility: "hidden" }, svg);
    const dot = el("circle", { r: 4, fill: "var(--use)", stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" }, svg);
    const hit = el("rect", { class: "hit", x: m.l, y: m.t, width: iw, height: ih }, svg);
    hit.addEventListener("pointermove", (e) => {
      const r = svg.getBoundingClientRect();
      const tx = t0 + ((e.clientX - r.left) * (W / r.width) - m.l) / iw * (t1 - t0);
      let best = points[0];
      for (const p of points) if (Math.abs(p.t - tx) < Math.abs(best.t - tx)) best = p;
      cross.setAttribute("x1", x(best.t)); cross.setAttribute("x2", x(best.t)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(best.t)); dot.setAttribute("cy", y(best.y)); dot.setAttribute("visibility", "visible");
      if (opts.tip) showTip(opts.tip(best), e);
    });
    hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); hideTip(); });
  }

  function spark(svg, points) {
    svg.innerHTML = "";
    if (points.length < 2) return;
    const W = 300, H = 42;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const t0 = points[0][0], t1 = points[points.length - 1][0];
    const max = Math.max(...points.map((p) => p[1])) * 1.1 || 1;
    let d = "";
    points.forEach((p, i) => {
      d += (i ? "L" : "M") + (((p[0] - t0) / (t1 - t0 || 1)) * W).toFixed(1) + "," + (H - (p[1] / max) * (H - 4)).toFixed(1);
    });
    el("path", { class: "area", d: d + `L${W},${H}L0,${H}Z` }, svg);
    el("path", { class: "line", d }, svg);
  }

  window.Charts = { bars, line, spark, showTip, hideTip };
})();
