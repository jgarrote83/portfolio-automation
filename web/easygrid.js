// easygrid.js — the EasyGrid (C5 paper account) tab. The render functions are pure (strings in, strings out) and exported for
// Node tests (tests/test_easygrid_js.py); the DOM wiring at the bottom runs only in a browser. Values come verbatim from
// /api/easygrid/*; nothing is recomputed here except formatting.
(function (root, factory) {
  const mod = factory();
  if (typeof module === "object" && module.exports) module.exports = mod;
  else root.EasyGrid = mod;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Colours from the portal's validated palette (performance.js): Core blue, SPY red, and the sleeve magenta for EasyGrid.
  const COLOR = { easygrid: "#b8348f", core: "#4f8cff", spy: "#e0524d" };
  const PREVIEW_LABEL = "If the month ended today (preview, not a decision).";
  const HOLDING_NAME = { SSO: "SSO (2x S&P)", SPY: "SPY (1x)", BIL: "T-bills (BIL)" };

  const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const pct = (v, d = 2) => (isNum(v) ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%` : "—");
  const num = (v, d = 2) => (isNum(v) ? v.toFixed(d) : "—");
  const usd = (v) => (isNum(v) ? `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—");
  const tick = (ok) => (ok === true ? "✓" : ok === false ? "✗" : "—");
  const holdingName = (s) => esc(HOLDING_NAME[s] || s || "—");

  function summaryHtml(s) {
    if (!s) return `<p class="muted">No data.</p>`;
    if (!s.started) {
      const dr = s.dry_run && s.dry_run.decision;
      const what = dr
        ? `Dry run for the ${esc(dr.date)} month-end: <strong>${holdingName(dr.target)}</strong> (trend ${tick(dr.trend && dr.trend.ok)}, credit ${tick(dr.credit && dr.credit.ok)}).`
        : "No dry-run decision yet.";
      return `<p><strong>Not started.</strong> ${what} Next decision: ${esc(s.next_decision_date || "—")}.</p>`;
    }
    return `<p>Equity <strong>${usd(s.equity)}</strong> (${esc(s.as_of || "")}) · ` +
      `return since the first fill (${esc(s.first_fill_date)}) <strong>${pct(s.return_since_first_fill)}</strong> · ` +
      `holding <strong>${esc(s.holding || "—")}</strong> since ${esc(s.holding_since || "—")} · next decision ${esc(s.next_decision_date || "—")}</p>`;
  }

  function criteriaBlock(c, title) {
    if (!c) return `<div class="eg-crit"><h3>${esc(title)}</h3><p class="muted">Not available yet.</p></div>`;
    const t = c.trend || {}, k = c.credit || {};
    return `<div class="eg-crit"><h3>${esc(title)}</h3>` +
      `<p><strong>Trend</strong> ${tick(t.ok)}: SPY ${num(t.spy_close)} vs its 10-month average ${num(t.sma10)} (${pct(isNum(t.distance_pct) ? t.distance_pct / 100 : null)})</p>` +
      `<p><strong>Credit</strong> ${tick(k.ok)}: BAA10Y ${num(k.baa10y)} (dated ${esc(k.baa10y_date || "—")}) vs its 126-day average ${num(k.avg126, 3)}</p>` +
      `<p><strong>Holding</strong>: ${holdingName(c.target)}${c.date ? ` <span class="muted">(${esc(c.date)})</span>` : ""}</p></div>`;
  }

  function criteriaHtml(cr) {
    cr = cr || {};
    const src = cr.last_decision_source === "dry_run" ? "Latest month-end (dry run)" : "Last decision";
    const label = cr.preview_label || PREVIEW_LABEL;
    return `<p class="muted">${esc(cr.rule || "Both ✓ → SSO; one ✓ → SPY; neither → T-bills.")}</p>` +
      `<div class="eg-crit-grid">${criteriaBlock(cr.last_decision, src)}${criteriaBlock(cr.preview, label)}</div>` +
      `<p class="muted eg-preview-label">${esc(label)}</p>`;
  }

  function decisionsHtml(rows) {
    if (!rows || !rows.length) return `<p class="muted">No decisions yet.</p>`;
    const body = rows.map((r) => {
      const fills = (r.fills || []).map((f) => `${esc(f.side)} ${num(f.filled_qty, 0)} ${esc(f.symbol)} @ ${num(f.avg_price)}` +
        (isNum(f.slippage_bps_vs_close) ? ` (${f.slippage_bps_vs_close >= 0 ? "+" : ""}${f.slippage_bps_vs_close.toFixed(1)} bps vs close)` : "")).join("<br>");
      const orders = (r.orders || []).map((o) => `${esc(o.side)} ${esc(o.qty)} ${esc(o.symbol)}`).join(", ");
      const p = r.period || {};
      return `<tr><td>${esc(r.decision_date)}</td><td>${esc(r.execution_date)}</td><td>${tick(r.trend && r.trend.ok)}</td><td>${tick(r.credit && r.credit.ok)}</td>` +
        `<td><strong>${holdingName(r.target)}</strong></td><td>${esc(r.status)}${r.first_enable ? " (first enable)" : ""}${r.late ? " (late)" : ""}</td>` +
        `<td>${orders || "—"}</td><td>${fills || "—"}</td><td class="num">${pct(p.return)}</td><td class="num">${pct(p.spy_return)}</td></tr>`;
    }).join("");
    return `<table class="sleeve-table"><thead><tr><th>Decided</th><th>Executed</th><th>Trend</th><th>Credit</th><th>Holding</th><th>Status</th>` +
      `<th>Orders</th><th>Fills</th><th class="num">Period return</th><th class="num">SPY same period</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function healthHtml(h) {
    h = h || {};
    const rec = h.reconcile === "ok" ? "ok" : (h.reconcile && h.reconcile.mismatches ? `<strong class="action-sell">mismatch</strong>` : esc(h.reconcile || "—"));
    return `<span>Last run ${esc(h.last_run || "never")}</span> · <span>C5_ENABLED <strong>${h.enabled === true ? "true" : "false"}</strong></span> · ` +
      `<span>Kill switch ${h.kill_switch ? `<strong class="action-sell">TRIPPED</strong>` : "off"}</span> · <span>Reconcile ${rec}</span>` +
      (h.error ? ` · <span class="action-sell">Error: ${esc(h.error)}</span>` : "");
  }

  return { COLOR, PREVIEW_LABEL, summaryHtml, criteriaHtml, decisionsHtml, healthHtml, esc, pct };
});

(function () {
  if (typeof document === "undefined" || !window.pfauto) return;
  const EG = window.EasyGrid;
  let chart = null;

  function renderChart(s) {
    const canvas = document.getElementById("egChart");
    const note = document.getElementById("eg-chart-note");
    if (chart) { chart.destroy(); chart = null; }
    if (!s || !s.started || !s.points.length) {
      note.textContent = "No fills yet: the chart starts at EasyGrid's first fill.";
      return;
    }
    const labels = s.points.map((p) => p.date);
    const at = new Map(s.points.map((p, i) => [p.date, i]));
    const marks = Array(labels.length).fill(null);
    (s.markers || []).forEach((m) => { const i = at.get(m.date); if (i != null) marks[i] = s.points[i].easygrid; });
    const markerBy = new Map((s.markers || []).map((m) => [m.date, m]));
    chart = new Chart(canvas, {
      type: "line",
      data: { labels, datasets: [
        { label: "EasyGrid", data: s.points.map((p) => p.easygrid), borderColor: EG.COLOR.easygrid, backgroundColor: "transparent", borderWidth: 2, pointRadius: 0, tension: 0.1 },
        { label: "Core", data: s.points.map((p) => p.core), borderColor: EG.COLOR.core, backgroundColor: "transparent", borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
        { label: "S&P 500 (SPY)", data: s.points.map((p) => p.spy), borderColor: EG.COLOR.spy, borderDash: [5, 4], backgroundColor: "transparent", borderWidth: 1.5, pointRadius: 0, tension: 0.1 },
        { label: "Holding changed", data: marks, showLine: false, pointStyle: "rectRot", pointRadius: 6, pointBorderWidth: 2, pointBackgroundColor: "#161a22",
          borderColor: "#e6e8ee", pointBorderColor: "#e6e8ee" },
      ] },
      options: {
        responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#e6e8ee", usePointStyle: true } },
          tooltip: { filter: (i) => i.raw != null, callbacks: { label: (ctx) => {
            if (ctx.datasetIndex === 3) { const m = markerBy.get(labels[ctx.dataIndex]); return m ? `→ ${m.holding} (decided ${m.decision_date})` : ""; }
            return `${ctx.dataset.label}: ${ctx.parsed.y == null ? "—" : ctx.parsed.y.toFixed(2)}`;
          } } },
        },
        scales: { x: { ticks: { color: "#8a93a6", maxTicksLimit: 10 }, grid: { color: "#262c38" } },
                  y: { ticks: { color: "#8a93a6" }, grid: { color: "#262c38" }, title: { display: true, text: "index, 100 = first fill", color: "#8a93a6" } } },
      },
    });
    note.textContent = `Indexed to 100 at the ${s.base_date} close.`;
  }

  async function load() {
    const [sum, ser, dec, cri] = await Promise.all(["summary", "series", "decisions", "criteria"].map((p) =>
      window.pfauto.api(`/api/easygrid/${p}`).catch((e) => ({ __error: e.message }))));
    document.getElementById("eg-summary").innerHTML = sum.__error ? `<p class="action-sell">Error: ${EG.esc(sum.__error)}</p>` : EG.summaryHtml(sum);
    document.getElementById("eg-health").innerHTML = EG.healthHtml(sum && sum.health);
    document.getElementById("eg-criteria").innerHTML = EG.criteriaHtml(cri.__error ? {} : cri);
    document.getElementById("eg-decisions").innerHTML = EG.decisionsHtml(dec.__error ? [] : dec);
    renderChart(ser.__error ? null : ser);
  }
  document.addEventListener("DOMContentLoaded", load);
})();
