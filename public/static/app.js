"use strict";

const LABELS = { equivalent: "Share class / ETF", adr: "ADR", pairs: "Stat-arb", merger: "Merger" };
const state = { data: null, strategy: "", activeOnly: false, q: "", selected: null };
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d));
const signed = (v, d = 2) => (v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d));

// ---------- data ----------
function connect() {
  if (!window.EventSource) return poll();
  const es = new EventSource("/api/stream");
  es.addEventListener("snapshot", (e) => render(JSON.parse(e.data)));
  es.onerror = () => { es.close(); setTimeout(connect, 5000); poll(true); };
}
let pollMs = 30000;
async function poll(once = false) {
  try {
    const r = await fetch("/api/opportunities");
    if (r.ok) render(await r.json());
    else $("#stamp").textContent = (await r.json()).detail || "waiting for first scan…";
  } catch { $("#stamp").textContent = "server unreachable"; }
  if (!once) setTimeout(poll, pollMs);
}
async function status() {
  try {
    const s = await (await fetch("/api/status")).json();
    state.mode = s.mode;
    const m = $("#market");
    m.textContent = s.market_open ? "Market open" : "Market closed";
    m.classList.toggle("open", s.market_open);
    // Serverless deployments recalibrate in GitHub Actions, not from the page.
    $("#btn-scan").hidden = s.mode === "serverless";
    $("#btn-scan").disabled = s.scanning;
    $("#btn-scan").textContent = s.scanning ? "Scanning…" : "Run daily scan";
    if (s.last_error) $("#stamp").textContent = "⚠ " + s.last_error;
  } catch { /* ignore */ }
}

// ---------- rendering ----------
function render(data) {
  state.data = data;
  const live = data.live_at ? `live ${new Date(data.live_at).toLocaleTimeString()}` : "end-of-day";
  $("#stamp").textContent = `calibrated ${data.data_through || "—"} · ${live}`;
  renderTiles(data);
  renderTable();
  if (state.selected) {
    const o = data.opportunities.find((x) => x.id === state.selected);
    if (o) renderDetail(o);
  }
}

function renderTiles(data) {
  const by = data.summary?.by_strategy || {};
  const tiles = [
    { k: "Active signals", v: data.summary?.active ?? 0, s: `of ${data.summary?.total ?? 0} monitored` },
    ...Object.keys(LABELS).map((k) => ({ k: LABELS[k], v: by[k]?.active ?? 0, s: `/ ${by[k]?.candidates ?? 0}` })),
  ];
  $("#tiles").innerHTML = tiles.map((t) => `<div class="tile"><div class="k">${esc(t.k)}</div><div class="v">${t.v} <small>${esc(t.s)}</small></div></div>`).join("");
}

function rowsFiltered() {
  const q = state.q.trim().toUpperCase();
  return (state.data?.opportunities || []).filter((o) =>
    (!state.strategy || o.strategy === state.strategy) &&
    (!state.activeOnly || o.active) &&
    (!q || o.legs.some((l) => l.symbol.toUpperCase().includes(q))));
}

function signalBadge(o) {
  const cls = o.active ? "active" : /ABOVE|below cost/.test(o.signal) ? "warnb" : "watch";
  return `<span class="badge ${cls}">${esc(o.signal)}</span>`;
}

function mainStat(o) {
  if (o.strategy === "merger") return `${fmt(o.metrics.annualized_pct, 1)}%`;
  const z = o.zscore;
  return `<span class="${z > 0 ? "neg" : z < 0 ? "pos" : ""}">${signed(z)}</span>`;
}
function premium(o) {
  if (o.strategy === "merger") return `${signed(o.metrics.gross_spread_pct)}%`;
  return o.metrics.dev_pct === undefined ? "—" : `${signed(o.metrics.dev_pct)}%`;
}
function info(o) {
  if (o.strategy === "pairs") return `t½ ${fmt(o.metrics.half_life_days, 1)}d · p ${fmt(o.metrics.coint_pvalue, 3)}`;
  if (o.strategy === "merger") return `${o.metrics.days_to_close}d to close`;
  if (o.strategy === "adr") return `prem ${signed(o.metrics.premium_pct)}% · avg ${signed(o.metrics.mean_premium_pct)}%`;
  if (o.metrics.kind === "share_class") return `prem ${signed(o.metrics.premium_pct)}% · avg ${signed(o.metrics.mean_premium_pct)}%`;
  return "ETF twin";
}

function renderTable() {
  const rows = rowsFiltered();
  $("#empty").hidden = rows.length > 0;
  $("#grid tbody").innerHTML = rows.map((o) => {
    const stale = o.stale_legs?.length ? `<span class="stale" title="No live print for ${esc(o.stale_legs.join(", "))}; using last close">●</span>` : "";
    return `<tr data-id="${esc(o.id)}" class="${o.active ? "" : "dim"} ${o.id === state.selected ? "sel" : ""}">
      <td class="strat hide-sm">${esc(LABELS[o.strategy])}</td>
      <td>${esc(o.name)}${stale}</td>
      <td>${signalBadge(o)}</td>
      <td class="num">${mainStat(o)}</td>
      <td class="num">${premium(o)}</td>
      <td class="num ${o.net_edge_bps > 0 ? "pos" : ""}">${fmt(o.net_edge_bps, 0)} bps</td>
      <td class="num hide-sm muted">${info(o)}</td></tr>`;
  }).join("");
}

function renderDetail(o) {
  state.selected = o.id;
  $("#detail").hidden = false;
  $(".layout").classList.add("with-detail");
  $("#d-strategy").textContent = LABELS[o.strategy];
  $("#d-name").textContent = o.name;
  $("#d-signal").innerHTML = `${signalBadge(o)} <span class="muted small">gross edge ${fmt(o.edge_bps, 0)} bps · net ${fmt(o.net_edge_bps, 0)} bps</span>`;

  const hist = o.history || { dates: [], values: [] };
  let title, bands = [], unit = "";
  if (o.strategy === "merger") { title = "Spread to deal value (%)"; bands = [0]; unit = "%"; }
  else if (o.strategy === "adr") {
    title = "ADR premium to home line (%), ±2σ of trailing mean";
    const m = o.params.mean * 100, s = o.params.std * 100;
    bands = [m - 2 * s, m, m + 2 * s]; unit = "%";
  } else { title = "Spread z-score, entry at ±2"; bands = [-2, 0, 2]; }
  $("#d-chart-title").textContent = title;
  $("#d-chart").innerHTML = lineChart(hist.dates, hist.values, bands, unit);
  bindChart(hist.dates, hist.values, unit);

  $("#d-legs").innerHTML = o.legs.map((l) => `<tr><td>${esc(l.symbol)}</td>
    <td class="${l.side === "BUY" ? "pos" : l.side === "SELL" ? "neg" : "muted"}">${esc(l.side || "—")}</td>
    <td class="num">${l.side === "FX" ? "hedge" : fmt(l.weight, 4)}</td>
    <td class="num">${fmt(o.prices?.[l.symbol], 2)}</td></tr>`).join("");

  const skip = new Set(["source"]);
  const m = Object.entries(o.metrics || {}).filter(([k]) => !skip.has(k));
  $("#d-metrics").innerHTML = m.map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(typeof v === "number" ? +v.toFixed(4) : v)}</dd>`).join("")
    + (o.metrics?.source ? `<dt>source</dt><dd><a href="${esc(o.metrics.source)}" target="_blank" rel="noopener">filing / news</a></dd>` : "");
  $("#d-notes").textContent = o.notes || "";
  $("#d-warn").innerHTML = (o.warnings || []).map((w) => `<li>${esc(w)}</li>`).join("");
  renderTable();
}

// ---------- chart (single series, recessive grid, dashed threshold bands) ----------
const W = 400, H = 190, PAD = { l: 38, r: 10, t: 10, b: 22 };
let scaleX, scaleY;
function lineChart(dates, vals, bands, unit) {
  if (!vals.length) return `<p class="muted small">No history.</p>`;
  const all = vals.concat(bands);
  let lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  scaleX = (i) => PAD.l + (i / Math.max(vals.length - 1, 1)) * (W - PAD.l - PAD.r);
  scaleY = (v) => PAD.t + (1 - (v - lo) / (hi - lo)) * (H - PAD.t - PAD.b);
  const ticks = niceTicks(lo, hi, 4);
  const grid = ticks.map((t) => `<line class="grid" x1="${PAD.l}" x2="${W - PAD.r}" y1="${scaleY(t)}" y2="${scaleY(t)}"/>
    <text class="axis" x="${PAD.l - 4}" y="${scaleY(t) + 3}" text-anchor="end">${+t.toFixed(2)}${unit}</text>`).join("");
  const bandLines = bands.map((b) => `<line class="band" x1="${PAD.l}" x2="${W - PAD.r}" y1="${scaleY(b)}" y2="${scaleY(b)}"/>`).join("");
  const path = vals.map((v, i) => `${i ? "L" : "M"}${scaleX(i).toFixed(1)},${scaleY(v).toFixed(1)}`).join("");
  const xl = [0, Math.floor((vals.length - 1) / 2), vals.length - 1].map((i) =>
    `<text class="axis" x="${scaleX(i)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === vals.length - 1 ? "end" : "middle"}">${dates[i].slice(5)}</text>`).join("");
  const last = vals.length - 1;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="history chart">${grid}${bandLines}
    <path class="line" d="${path}"/><circle class="dot" r="4" cx="${scaleX(last)}" cy="${scaleY(vals[last])}"/>${xl}
    <line class="cross" id="cross" y1="${PAD.t}" y2="${H - PAD.b}" visibility="hidden"/>
    <rect id="hit" x="${PAD.l}" y="0" width="${W - PAD.l - PAD.r}" height="${H}" fill="transparent"/></svg>`;
}
function niceTicks(lo, hi, n) {
  const step0 = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) out.push(t);
  return out;
}
function bindChart(dates, vals, unit) {
  const svg = $("#d-chart svg"); if (!svg) return;
  const hit = svg.querySelector("#hit"), cross = svg.querySelector("#cross"), tip = $("#tip");
  hit.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(vals.length - 1, Math.round(((x - PAD.l) / (W - PAD.l - PAD.r)) * (vals.length - 1))));
    cross.setAttribute("x1", scaleX(i)); cross.setAttribute("x2", scaleX(i)); cross.setAttribute("visibility", "visible");
    tip.hidden = false; tip.textContent = `${dates[i]}  ${fmt(vals[i], 2)}${unit}`;
    tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY - 28 + "px";
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hidden = true; });
}

// ---------- events ----------
$("#tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab"); if (!b) return;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t === b));
  state.strategy = b.dataset.s; renderTable();
});
$("#active-only").addEventListener("change", (e) => { state.activeOnly = e.target.checked; renderTable(); });
$("#q").addEventListener("input", (e) => { state.q = e.target.value; renderTable(); });
$("#grid tbody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr"); if (!tr) return;
  const o = state.data.opportunities.find((x) => x.id === tr.dataset.id);
  if (o) renderDetail(o);
});
$("#d-close").addEventListener("click", () => {
  state.selected = null; $("#detail").hidden = true; $(".layout").classList.remove("with-detail"); renderTable();
});
$("#btn-refresh").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try {
    const body = await (await fetch("/api/refresh", { method: "POST" })).json();
    if (body.opportunities) render(body);  // serverless: no stream, so render the reply
  } finally { e.target.disabled = false; }
});
$("#btn-scan").addEventListener("click", async () => { await fetch("/api/scan", { method: "POST" }); status(); });

(async () => {
  await status();
  // Self-hosted server pushes updates over SSE; serverless (Vercel) is polled.
  if (state.mode === "serverless") { pollMs = 60000; poll(); } else connect();
})();
setInterval(status, 15000);
