// easygrid.js — the EasyGrid (C5 paper account) tab, built to docs/design/easygrid-tab-mockup.html.
// The render functions are pure (data in, HTML out) and exported for Node tests (tests/test_easygrid.py); the DOM wiring at the bottom
// runs only in a browser. Every value comes computed from /api/easygrid/*; this file only FORMATS (the page never recomputes anything).
//
// Colours. The mockup's EasyGrid #4ea1ff and Core #f2a65a FAIL the dataviz palette validator on the #161a22 panel (OKLCH lightness 0.70
// and 0.79, above the dark band 0.48-0.67), so the same hues are used one step darker: #3b8ae3 and #c97f2e PASS every check (band,
// chroma, CVD dE 25.5, normal-vision dE 28.6, contrast >= 3:1). SPY keeps the mockup's neutral #e6e8ee as a REFERENCE line, drawn
// dashed (a neutral by definition fails the chroma floor; it is not a categorical slot; its separation from both series passes, dE 20).
// Status colours #7fd69a / #ff9a8a always come with an icon and a word, never colour alone.
(function (root, factory) {
  const mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  else root.EasyGrid = mod;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const COLOR = { easygrid: "#3b8ae3", core: "#c97f2e", spy: "#e6e8ee", ok: "#7fd69a", bad: "#ff9a8a", muted: "#9aa3b5" };
  const PREVIEW_LABEL = "If the month ended today (preview, not a decision).";
  const MINUS = "−";
  const ICON_OK = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3 3 7-7"></path></svg>';
  const ICON_BAD = '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"></path></svg>';
  const STATE_WORD = { active: "Active", paused: "Paused", paused_to_tbills: "Paused to T-bills" };

  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const signed = (x, d) => (x >= 0 ? "+" : MINUS) + Math.abs(x).toFixed(d);
  const pct = (v, d = 1) => (isNum(v) ? `${signed(v * 100, d)}%` : "—");
  const pts = (v, d = 1) => (isNum(v) ? Math.abs(v).toFixed(d) : "—");
  const num = (v, d = 2) => (isNum(v) ? v.toFixed(d) : "—");
  // a distance below the peak is never positive: 0 reads "0.0%", anything else carries a true minus sign
  const below = (v) => (!isNum(v) ? "—" : Math.abs(v) < 0.0005 ? "0.0%" : `${MINUS}${Math.abs(v * 100).toFixed(1)}%`);
  const usd0 = (v) => (isNum(v) ? `$${Math.round(v).toLocaleString("en-US")}` : "—");
  const usdSigned = (v) => (isNum(v) ? `${v >= 0 ? "+" : MINUS}$${Math.abs(v).toFixed(2)}` : "—");
  function day(d, withDow) {
    if (!d) return "—";
    const [y, m, dd] = String(d).slice(0, 10).split("-").map(Number);
    const dt = new Date(Date.UTC(y, m - 1, dd));
    const opts = withDow ? { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" };
    return dt.toLocaleDateString("en-US", opts);
  }
  const status = (ok) => (ok === true ? `<span class="eg-status ok">${ICON_OK}Healthy</span>` : ok === false ? `<span class="eg-status bad">${ICON_BAD}Not healthy</span>` : `<span class="eg-status">—</span>`);
  const tick = (ok) => (ok === true ? "✓" : ok === false ? "✗" : "—");

  function engineChip(s) {
    return `Engine: ${s && s.enabled ? "Enabled" : "Disabled"}`;
  }

  function headlineHtml(s) {
    if (!s) return `<p class="eg-kicker">No data.</p>`;
    if (!s.started) {
      const dr = s.dry_run && s.dry_run.decision;
      const what = dr
        ? `Dry run for the ${esc(day(dr.date))} month-end: trend ${tick(dr.trend && dr.trend.ok)} · credit ${tick(dr.credit && dr.credit.ok)} → would hold <strong>${esc((dr.target === "SSO" ? "SSO · 2x S&P 500" : dr.target === "SPY" ? "SPY · 1x" : "T-bills (BIL)"))}</strong>.`
        : "No dry-run decision yet.";
      return `<p class="eg-kicker">Not started</p><p class="eg-sentence"><strong>Not started.</strong> ${what}</p>` +
        `<p class="eg-muted eg-small" style="margin:0">Next decision: ${esc(day(s.next_decision_date, true))}.</p>`;
    }
    const h = s.headline || {};
    const ahead = h.ahead === true ? "ahead of" : h.ahead === false ? "behind" : "level with";
    return `<p class="eg-kicker">Since the first trade on ${esc(day(s.first_fill_date))}</p>` +
      `<div class="eg-big-row">` +
      `<div class="eg-big"><span class="lab">EasyGrid</span><span class="val" style="color:${COLOR.easygrid}">${pct(h.easygrid)}</span></div>` +
      `<div class="eg-big"><span class="lab">SPY</span><span class="val" style="color:${COLOR.spy}">${pct(h.spy)}</span></div>` +
      `<div class="eg-big"><span class="lab">Core</span><span class="val" style="color:${COLOR.core}">${pct(h.core)}</span></div></div>` +
      `<p class="eg-sentence">EasyGrid is <strong>${ahead}</strong> SPY by <strong>${pts(h.vs_spy_points)} points</strong>.</p>`;
  }

  function keyNumbersHtml(s) {
    s = s || {};
    const started = !!s.started;
    const bp = s.below_peak || {};
    return `<div class="eg-key"><span class="lab">Account value</span><span class="num">${started ? usd0(s.account_value) : "—"}</span>` +
      `<span class="sub">Started at ${started ? usd0(s.started_at) : "$100,000"}</span></div>` +
      `<div class="eg-key"><span class="lab">Holding now</span><span class="word">${started ? esc(s.holding_name || "—") : "Not started"}</span>` +
      `<span class="sub">${started ? `Since ${esc(day(s.holding_since))}` : "No position yet"}</span></div>` +
      `<div class="eg-key"><span class="lab">Next decision</span><span class="word">${esc(day(s.next_decision_date, true))}</span>` +
      `<span class="sub">After the close; trades shortly before the next day's close</span></div>` +
      `<div class="eg-key"><span class="lab">Below its own peak</span><span class="num">${started ? below(bp.easygrid) : "—"}</span>` +
      `<span class="sub">SPY: ${started ? below(bp.spy) : "—"}</span></div>`;
  }

  function card(n, title, c, kind) {
    const isTrend = kind === "trend";
    const x = (c && c[kind]) || {};
    const pos = isNum(x.bar_pos) ? x.bar_pos : null;
    const color = x.ok ? COLOR.easygrid : COLOR.bad;
    const values = isTrend
      ? `SPY month-end close <span class="mono" style="color:#e6e8ee">${num(x.spy_close)}</span> vs its 10-month average <span class="mono" style="color:#e6e8ee">${num(x.sma10)}</span>`
      : `Corporate credit spread (prior day) <span class="mono" style="color:#e6e8ee">${num(x.baa10y)}%</span> vs its 126-day average <span class="mono" style="color:#e6e8ee">${num(x.avg126)}%</span>`;
    const note = isTrend
      ? `${isNum(x.distance_pct) ? `${signed(x.distance_pct, 1)}% ${x.distance_pct >= 0 ? "above" : "below"} the average` : "—"} · healthy while above`
      : `${isNum(x.diff) ? `${signed(x.diff, 2)} ${x.diff > 0 ? "above" : "below"} the average` : "—"} · healthy at or below (lenders calm)`;
    return `<div class="eg-card"><div class="eg-card-head"><span style="font-weight:600">${n} · ${title}</span>${status(x.ok)}</div>` +
      `<p>${values}</p>` +
      `<div class="eg-bar" aria-hidden="true"><div class="mid"></div>${pos == null ? "" : `<div class="pos" style="left:${(pos * 100).toFixed(1)}%;background:${color}"></div>`}</div>` +
      `<p class="note">${note}</p></div>`;
  }

  function whyHtml(cr) {
    cr = cr || {};
    const last = cr.last_decision, pv = cr.preview;
    const label = cr.preview_label || PREVIEW_LABEL;
    const sub = last
      ? `${cr.last_decision_source === "dry_run" ? "Latest month-end (dry run)" : "Decided"} ${esc(day(last.date))}${pv ? ` · checked again ${esc(day(pv.date))}` : ""}`
      : "No decision yet";
    const rule = (cr.rule || []).map((r, i) => {
      const t = r.today ? " today" + (i >= 0 ? " first" : "") : "";
      return `<div class="${t.trim()}">${esc(r.when)}${r.today ? "  ← today" : ""}</div><div class="hold${r.today ? " today" : ""}">${esc(r.holding_name)}</div>`;
    }).join("");
    const prev = pv
      ? `Trend ${tick(pv.trend && pv.trend.ok)} · Credit ${tick(pv.credit && pv.credit.ok)} → would hold <strong>${esc(pv.target_name)}</strong>`
      : "Not available until the next close.";
    return `<div><h2>Why it holds what it holds</h2><p class="eg-muted eg-small" style="margin:4px 0 0">${sub}</p></div>` +
      card(1, "Trend", last, "trend") + card(2, "Credit", last, "credit") +
      `<div style="display:flex;flex-direction:column;gap:6px"><span class="eg-small eg-muted">The rule</span><div class="eg-rule">${rule}</div></div>` +
      `<div class="eg-preview"><span class="kick">If the month ended today · preview, not a decision</span><span style="font-size:14px">${prev}</span>` +
      `<span class="eg-small eg-muted">${esc(label)}</span></div>`;
  }

  function mtdHtml(s) {
    const m = (s && s.month_to_date) || {};
    return `<h2>This month so far</h2><div class="eg-mtd">` +
      `<div><span class="eg-muted eg-small">EasyGrid</span><span class="v" style="color:${COLOR.easygrid}">${pct(m.easygrid)}</span></div>` +
      `<div><span class="eg-muted eg-small">SPY</span><span class="v">${pct(m.spy)}</span></div>` +
      `<div><span class="eg-muted eg-small">Core</span><span class="v" style="color:${COLOR.core}">${pct(m.core)}</span></div></div>` +
      `<p class="eg-muted eg-small" style="margin:0">Resets at each monthly decision${m.since ? ` (since ${esc(day(m.since))})` : ""}.</p>`;
  }

  function decisionsHtml(rows) {
    if (!rows || !rows.length) return `<p class="eg-muted">No decisions yet.</p>`;
    const body = rows.map((r) => {
      const trend = r.trend_ok == null ? "—" : `${tick(r.trend_ok)} ${isNum(r.trend_distance_pct) ? signed(r.trend_distance_pct, 1) + "%" : ""}`;
      const credit = r.credit_ok == null ? "—" : `${tick(r.credit_ok)} ${isNum(r.credit_diff) ? signed(r.credit_diff, 2) : ""}`;
      const st = r.status && r.status !== "executed" ? ` <span class="eg-muted eg-small">(${esc(r.status)})</span>` : "";
      return `<tr><td>${esc(r.month || "—")}</td><td>${trend}</td><td>${credit}</td><td style="font-weight:600">${esc(r.decision || "—")}${st}</td>` +
        `<td class="mono">${usdSigned(r.fill_vs_close)}</td><td class="mono r">${pct(r.easygrid_return)}</td><td class="mono r">${pct(r.spy_return)}</td></tr>`;
    }).join("");
    return `<table class="eg-table"><thead><tr><th scope="col">Month</th><th scope="col">Trend</th><th scope="col">Credit</th><th scope="col">Decision</th>` +
      `<th scope="col">Fill vs close</th><th scope="col" class="r">EasyGrid</th><th scope="col" class="r">SPY</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function healthHtml(h) {
    h = h || {};
    let match;
    if (h.holding_mismatch) {
      const actual = Object.entries(h.holding_mismatch.actual || {}).map(([k, v]) => `${esc(k)} ${num(v, 0)}`).join(", ") || "nothing";
      match = `<span class="eg-alert">${ICON_BAD} Holding mismatch</span> expected ${esc(h.holding_mismatch.expected)}, account holds ${actual}`;
    } else if (h.positions_match === false) {
      match = `<span class="eg-alert">${ICON_BAD} No</span>`;
    } else {
      match = h.positions_match === true ? "Yes" : "—";
    }
    const d = h.data;
    const data = d ? `SPY ${tick(d.spy)} · credit spread ${tick(d.credit)}` : "—";
    return `<span><span class="k">Last engine run</span> ${esc(h.last_run ? h.last_run.replace("T", " ").slice(0, 16) + " ET" : "never")}</span>` +
      `<span><span class="k">C5 switch</span> ${h.enabled ? "Enabled" : "Disabled"}</span>` +
      `<span><span class="k">Kill switch</span> ${esc(STATE_WORD[h.control] && h.control !== "active" ? STATE_WORD[h.control] : "Off")}</span>` +
      `<span><span class="k">Positions match ledger</span> ${match}</span>` +
      `<span><span class="k">Data</span> ${data}</span>` +
      (h.pause ? `<span>${esc(h.pause)}</span>` : "") +
      (h.error ?`<span class="eg-alert">${ICON_BAD} ${esc(h.error)}</span>` : "");
  }

  function controlsHtml(c) {
    c = c || {};
    const st = c.state || "active";
    const icon = st === "active" ? `<span class="eg-status ok">${ICON_OK}${STATE_WORD[st]}</span>` : `<span class="eg-status bad">${ICON_BAD}${esc(STATE_WORD[st] || st)}</span>`;
    const since = c.changed_at ? ` <span class="eg-muted eg-small">since ${esc(String(c.changed_at).replace("T", " ").slice(0, 16))}${c.reason ? ` · “${esc(c.reason)}”` : ""}</span>` : "";
    const btn = (action, label) => `<button type="button" class="eg-btn" data-action="${action}" title="${esc(ACTION_TEXT[action].what)}">${esc(label)}</button>`;
    const buttons = c.is_admin
      ? `<div class="eg-controls-actions">` +
        (st !== "paused" ? btn("pause", "Pause") : "") +
        (st !== "paused_to_tbills" ? btn("pause_to_tbills", "Pause to T-bills") : "") +
        (st !== "active" ? btn("resume", "Resume") : "") +
        (st === "active" ? btn("realign", "Re-align at next close") : "") +
        `</div>`
      : "";
    const log = (c.log || []).map((e) => `<li><span class="mono eg-muted">${esc(String(e.changed_at || e.logged_at || "").replace("T", " ").slice(0, 16))}</span> ` +
      `${esc(e.kind === "state" ? `state → ${STATE_WORD[e.state] || e.state}` : e.kind === "realign_request" ? "re-align requested" : (e.action || e.kind))}` +
      `${e.changed_by ? ` · ${esc(e.changed_by)}` : ""}${e.reason ? ` · “${esc(e.reason)}”` : ""}</li>`).join("");
    return `<div class="eg-toolbar"><h2>Controls</h2>${icon}</div>` +
      `<p class="eg-muted eg-small" style="margin:0">Engine state${since}${c.realign_pending ? " · a re-align is pending for the next close" : ""}.</p>` +
      buttons +
      `<div><h3 style="margin-bottom:6px">Recent changes</h3>${log ? `<ul class="eg-log">${log}</ul>` : `<p class="eg-muted eg-small" style="margin:0">No changes yet.</p>`}</div>`;
  }

  const ACTION_TEXT = {
    pause: { title: "Pause", what: "Place no orders and keep the current holding.", phrase: "PAUSE" },
    pause_to_tbills: { title: "Pause to T-bills", what: "Sell to T-bills (BIL) at the next close, then place no further orders.", phrase: "PAUSE TO T-BILLS" },
    resume: { title: "Resume", what: "Back to active: at the next close, align to the latest month-end decision.", phrase: "RESUME" },
    realign: { title: "Re-align at next close", what: "At the next close, align the account to the latest month-end decision.", phrase: "REALIGN" },
  };

  return { COLOR, PREVIEW_LABEL, ACTION_TEXT, engineChip, headlineHtml, keyNumbersHtml, whyHtml, mtdHtml, decisionsHtml, healthHtml, controlsHtml, esc, pct };
});

(function () {
  if (typeof document === "undefined" || !window.pfauto) return;
  const EG = window.EasyGrid;
  let growth = null, drawdown = null, range = "since_start";

  const grid = { color: "#262c38" }, ticks = { color: "#9aa3b5" };

  function empty(wrapId, text) {
    const w = document.getElementById(wrapId);
    w.innerHTML = `<div class="eg-empty">${EG.esc(text)}</div>`;
  }

  function renderGrowth(s) {
    if (growth) { growth.destroy(); growth = null; }
    if (!s || !s.started || !s.points.length) { empty("eg-growth-wrap", "Three lines indexed to 100 at the first fill. Not started yet."); empty("eg-dd-wrap", "Starts at the first fill."); return; }
    const w = document.getElementById("eg-growth-wrap");
    if (!w.querySelector("canvas")) w.innerHTML = '<canvas id="egGrowth"></canvas>';
    const labels = s.points.map((p) => p.date);
    const idx = new Map(labels.map((d, i) => [d, i]));
    const marks = Array(labels.length).fill(null);
    const byDate = new Map();
    (s.markers || []).forEach((m) => { const i = idx.get(m.date); if (i != null) { marks[i] = s.points[i].easygrid; byDate.set(m.date, m); } });
    growth = new Chart(document.getElementById("egGrowth"), {
      type: "line",
      data: { labels, datasets: [
        { label: "EasyGrid", data: s.points.map((p) => p.easygrid), borderColor: EG.COLOR.easygrid, borderWidth: 2, pointRadius: 0, tension: 0.1 },
        { label: "Core", data: s.points.map((p) => p.core), borderColor: EG.COLOR.core, borderWidth: 2, pointRadius: 0, tension: 0.1 },
        { label: "SPY", data: s.points.map((p) => p.spy), borderColor: EG.COLOR.spy, borderDash: [6, 4], borderWidth: 2, pointRadius: 0, tension: 0.1 },
        { label: "Holding changed", data: marks, showLine: false, pointRadius: 5, pointHoverRadius: 8, pointBackgroundColor: EG.COLOR.easygrid,
          pointBorderColor: "#161a22", pointBorderWidth: 2 },
      ] },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: false }, tooltip: { filter: (i) => i.raw != null, callbacks: { label: (ctx) => {
          if (ctx.datasetIndex === 3) { const m = byDate.get(labels[ctx.dataIndex]); return m ? `Holding changed → ${m.holding_name} (decided ${m.decision_date || m.kind})` : ""; }
          return `${ctx.dataset.label}: ${ctx.parsed.y == null ? "—" : ctx.parsed.y.toFixed(1)}`;
        } } } },
        scales: { x: { ticks: { ...ticks, maxTicksLimit: 8 }, grid }, y: { ticks, grid } } },
    });
    renderDrawdown(s.drawdown || []);
  }

  function renderDrawdown(dd) {
    if (drawdown) { drawdown.destroy(); drawdown = null; }
    if (!dd.length) { empty("eg-dd-wrap", "Starts at the first fill."); return; }
    const w = document.getElementById("eg-dd-wrap");
    if (!w.querySelector("canvas")) w.innerHTML = '<canvas id="egDrawdown"></canvas>';
    drawdown = new Chart(document.getElementById("egDrawdown"), {
      type: "line",
      data: { labels: dd.map((p) => p.date), datasets: [
        { label: "EasyGrid", data: dd.map((p) => p.easygrid == null ? null : p.easygrid * 100), borderColor: EG.COLOR.easygrid, borderWidth: 2, pointRadius: 0 },
        { label: "SPY", data: dd.map((p) => p.spy == null ? null : p.spy * 100), borderColor: EG.COLOR.spy, borderDash: [6, 4], borderWidth: 2, pointRadius: 0 },
      ] },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#c6ccd8", boxWidth: 22, boxHeight: 3 } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y == null ? "—" : ctx.parsed.y.toFixed(1) + "%"}` } } },
        scales: { x: { ticks: { ...ticks, maxTicksLimit: 8 }, grid }, y: { ticks: { ...ticks, callback: (v) => `${Number(v).toFixed(1)}%` }, grid, max: 0 } } },
    });
  }

  async function loadSeries() {
    const s = await window.pfauto.api(`/api/easygrid/series?range=${encodeURIComponent(range)}`).catch(() => null);
    renderGrowth(s);
  }

  async function loadControls() {
    const c = await window.pfauto.api("/api/easygrid/controls").catch(() => null);
    document.getElementById("eg-controls").innerHTML = EG.controlsHtml(c || {});
    document.querySelectorAll("#eg-controls [data-action]").forEach((b) => b.addEventListener("click", () => openDialog(b.dataset.action)));
  }

  function openDialog(action) {
    const t = EG.ACTION_TEXT[action];
    const dlg = document.getElementById("eg-dialog");
    document.getElementById("eg-dialog-title").textContent = t.title;
    document.getElementById("eg-dialog-what").textContent = t.what;
    document.getElementById("eg-confirm-label").textContent = `Type ${t.phrase} to confirm`;
    document.getElementById("eg-reason").value = "";
    document.getElementById("eg-confirm").value = "";
    document.getElementById("eg-dialog-error").textContent = "";
    dlg.dataset.action = action;
    dlg.showModal();
  }

  async function submitDialog(ev) {
    ev.preventDefault();
    const dlg = document.getElementById("eg-dialog");
    const action = dlg.dataset.action;
    const body = { action, reason: document.getElementById("eg-reason").value, confirm: document.getElementById("eg-confirm").value };
    const err = document.getElementById("eg-dialog-error");
    try {
      const r = await fetch("/api/easygrid/controls", { method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json", "X-EasyGrid-Control": "1" }, body: JSON.stringify(body) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { err.textContent = j.error || `${r.status} ${r.statusText}`; return; }
      dlg.close();
      await Promise.all([loadControls(), loadSummary()]);
    } catch (e) {
      err.textContent = e.message;
    }
  }

  async function loadSummary() {
    const sum = await window.pfauto.api("/api/easygrid/summary").catch((e) => ({ __error: e.message }));
    if (sum.__error) { document.getElementById("eg-headline").innerHTML = `<p class="eg-alert">Error: ${EG.esc(sum.__error)}</p>`; return; }
    document.getElementById("eg-chip-engine").textContent = EG.engineChip(sum);
    document.getElementById("eg-headline").innerHTML = EG.headlineHtml(sum);
    document.getElementById("eg-keys").innerHTML = EG.keyNumbersHtml(sum);
    document.getElementById("eg-mtd").innerHTML = EG.mtdHtml(sum);
    document.getElementById("eg-health").innerHTML = EG.healthHtml(sum.health);
  }

  async function load() {
    const [cri, dec] = await Promise.all(["criteria", "decisions"].map((p) => window.pfauto.api(`/api/easygrid/${p}`).catch(() => null)));
    document.getElementById("eg-why").innerHTML = EG.whyHtml(cri || {});
    document.getElementById("eg-decisions").innerHTML = EG.decisionsHtml(dec || []);
    await Promise.all([loadSummary(), loadSeries(), loadControls()]);
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#eg-range [data-range]").forEach((b) => b.addEventListener("click", () => {
      range = b.dataset.range;
      document.querySelectorAll("#eg-range [data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      loadSeries();
    }));
    document.getElementById("eg-dialog-form").addEventListener("submit", submitDialog);
    document.getElementById("eg-dialog-cancel").addEventListener("click", () => document.getElementById("eg-dialog").close());
    load();
  });
})();
