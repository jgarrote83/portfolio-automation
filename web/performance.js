// performance.html — portfolio + Dalio-quadrant baskets vs SPY chart, regime
// shading by favored bucket, and holdings valuation table.
(() => {
  const fmtMoney = (v) => v == null || isNaN(v) ? "—"
    : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  const fmtPct = (v) => v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
  const fmtQty = (v) => v == null ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 });
  const cls = (v) => v == null || v === 0 ? "" : (v > 0 ? "action-buy" : "action-sell");

  // Quadrant identity colors — fixed per entity, validated as a set with
  // portfolio blue + SPY red against the panel surface (all ≥3:1, CVD ΔE ≥ 13).
  const QUADS = ["Q1", "Q2", "Q3", "Q4"];
  const QCOLOR = { Q1: "#199e70", Q2: "#d95926", Q3: "#c98500", Q4: "#9085e9" };
  const QTINT  = { Q1: "rgba(25,158,112,.10)", Q2: "rgba(217,89,38,.10)",
                   Q3: "rgba(201,133,0,.10)",  Q4: "rgba(144,133,233,.10)" };
  const QLABEL = { Q1: "Q1 Goldilocks", Q2: "Q2 Reflation",
                   Q3: "Q3 Stagflation", Q4: "Q4 Deflation" };

  // Flex sleeve panel (session 2026-08-10, Flex Sleeve Performance Ledger
  // Task E). #b8348f validated with scripts/validate_palette.js (dataviz
  // skill) against the panel surface (#161a22, dark mode) and every existing
  // series color in this file: contrast >=3:1 and CVD ΔE >=8 (a clean PASS,
  // not the 6-8 WARN band) on every pair, normal-vision ΔE >=15 on every
  // pair (well above the hard-fail floor); worst simulated-CVD pair is vs Q1
  // green at ΔE 9.5 — full details in the PR body. A muted grey substitutes
  // for it below the N=30 sample floor (never a skill/win-rate stat here).
  const SLEEVE_COLOR = "#b8348f";
  const SLEEVE_THIN_COLOR = "#5a6070";
  const SLEEVE_MIN_N = 30;

  let chart = null;
  let sleeveChart = null;
  let regimeKeys = [];  // per-point favored-bucket key, set before chart build
  let leanKeys = [];    // per-point transition-lean key, set before chart build

  async function load(windowKey) {
    const summaryEl = document.getElementById("perf-summary");
    summaryEl.textContent = "Loading…";
    try {
      const data = await window.pfauto.api(`/api/performance?window=${encodeURIComponent(windowKey)}`);
      renderChart(data);
      renderSleeveChart(data);  // after renderChart -- shares its regimeKeys
      renderTable(data);
      renderSummary(data);
      renderQuadSummary(data);
      renderQuadAccountability(data);
      renderSleeveSummary(data);
      renderLeanLegend(data);
    } catch (e) {
      summaryEl.textContent = `Error: ${e.message}`;
    }
  }

  function renderSummary(data) {
    const series = data.series || [];
    document.getElementById("as-of").textContent = data.as_of || "—";
    if (!series.length) {
      document.getElementById("perf-summary").textContent = "No data in this window.";
      return;
    }
    const first = series[0], last = series[series.length - 1];
    const pfRet  = first.portfolio_value ? ((last.portfolio_value / first.portfolio_value) - 1) * 100 : null;
    const spyRet = first.spy_close ? ((last.spy_close / first.spy_close) - 1) * 100 : null;
    const alpha  = (pfRet != null && spyRet != null) ? pfRet - spyRet : null;
    document.getElementById("perf-summary").innerHTML =
      `Portfolio <strong class="${cls(pfRet)}">${fmtPct(pfRet)}</strong> &nbsp;|&nbsp; ` +
      `S&P 500 <strong class="${cls(spyRet)}">${fmtPct(spyRet)}</strong> &nbsp;|&nbsp; ` +
      `Alpha <strong class="${cls(alpha)}">${fmtPct(alpha)}</strong> ` +
      `<span class="muted">(${series.length} obs)</span>`;
  }

  // Per-quadrant window return + alpha vs SPY chips; best quadrant highlighted.
  function renderQuadSummary(data) {
    const el = document.getElementById("quad-summary");
    if (!el) return;
    const series = (data.series || []).filter(p => p.quadrants);
    if (!series.length) { el.innerHTML = ""; return; }
    const last = series[series.length - 1];
    const spyRet = last.spy_norm != null ? last.spy_norm - 100 : null;
    const meta = data.quadrant_index_meta || null;
    const rows = QUADS
      .map(q => ({ q, ret: last.quadrants[q] != null ? last.quadrants[q] - 100 : null }))
      .filter(r => r.ret != null);
    if (!rows.length) { el.innerHTML = ""; return; }
    const best = rows.reduce((a, b) => (b.ret > a.ret ? b : a)).q;
    el.innerHTML = rows.map(({ q, ret }) => {
      const alpha = spyRet != null ? ret - spyRet : null;
      // 2026-08-21 measurement-integrity cycle, Task A: disclose a dropped
      // member (never hide it) directly in the chip's own tooltip.
      const qMeta = meta && meta[q];
      const dropped = qMeta && qMeta.members_dropped && qMeta.members_dropped.length
        ? ` -- excludes ${qMeta.members_dropped.join("/")} (no price at window start)`
        : "";
      return `<span class="chip${q === best ? " best" : ""}" title="${QLABEL[q]} equal-weight basket, this window${dropped}">` +
        `<span class="swatch" style="background:${QCOLOR[q]}"></span>` +
        `${QLABEL[q]}${q === best ? " ★" : ""} ` +
        `<strong class="${cls(ret)}">${fmtPct(ret)}</strong>` +
        `<span class="muted"> (α ${fmtPct(alpha)})</span></span>`;
    }).join("") +
      `<span class="muted bandnote">Shaded bands = favored quadrant(s) that day; ★ = best in window. ` +
      `These baskets carry no cash, flex, or international sleeve -- not an achievable alternative to the book.</span>` +
      // 2026-08-21 measurement-integrity cycle, Task A2: disclose the
      // look-ahead when the window's basket composition could not be
      // anchored to its OWN first day's recorded membership (predates the
      // quadrant_map stamping feature) -- never silently apply today's
      // roster to history without saying so.
      (meta && meta.membership_basis === "current_map_applied_retroactively"
        ? `<span class="muted bandnote" style="display:block">⚠ Basket composition for this window uses TODAY's current roster ` +
          `applied retroactively (no per-day membership recorded yet for the window start) -- a look-ahead toward whichever ` +
          `incumbent is currently selected.</span>`
        : "");
  }

  // Regime-call accountability scorecard (2026-08-21 SWA lean-visibility
  // cycle, Task A) — deliberately a SEPARATE panel from renderQuadSummary
  // above. That one answers "how did the basket do?"; this one answers "how
  // did OUR PICKING do?" (excess earned only on sessions the quadrant was
  // favored — a basket can return strongly while our picking of it still
  // lost, if it was favored at the wrong times). Echoes
  // `data.quadrant_accountability` verbatim -- never recomputes an excess or
  // a suspect flag client-side.
  function suspectTitle(path, n) {
    if (path === "both") {
      return `Lagged SPY on a long consecutive run of sessions AND over the trailing ${n} sessions — both triggers fired.`;
    }
    if (path === "streak") {
      return "Lagged SPY every session checked, consecutively, long enough to flag (legacy streak rule).";
    }
    if (path === "rolling") {
      return `Favored on enough of the trailing ${n} sessions with negative excess over that window (rolling rule) — not necessarily one unbroken losing streak.`;
    }
    return "";
  }

  function renderQuadAccountability(data) {
    const el = document.getElementById("quad-accountability");
    if (!el) return;
    const qa = data.quadrant_accountability;
    if (!qa || !qa.available) { el.innerHTML = ""; return; }
    const n = qa.trailing_window_sessions;
    const chips = QUADS.filter(q => qa.buckets[q]).map(q => {
      const b = qa.buckets[q];
      const cum = b.cumulative_favored_excess_pp;
      const trail = b.trailing_excess_pp;
      const fs = b.favored_sessions;
      const favText = (fs != null && n != null) ? `${fs}/${n} sessions favored` : "—";
      const badge = b.suspect
        ? `<span class="badge suspect" title="${suspectTitle(b.suspect_path, n)}">⚠ suspect</span>`
        : "";
      return `<span class="chip accountability" title="${QLABEL[q]} — excess vs SPY earned only on sessions this quadrant was favored">` +
        `<span class="swatch" style="background:${QCOLOR[q]}"></span>` +
        `${QLABEL[q]} <strong class="${cls(cum)}">${fmtPct(cum)}</strong>` +
        `<span class="muted"> (trail ${fmtPct(trail)}, ${favText})</span>` +
        badge +
        `</span>`;
    });
    if (!chips.length) { el.innerHTML = ""; return; }
    el.innerHTML =
      `<div class="accountability-label">Regime-call scorecard — excess vs SPY earned only on sessions this quadrant was favored (did picking it add value?)</div>` +
      `<div class="accountability-row">${chips.join("")}</div>`;
  }

  // Background shading by the day's favored quadrant bucket. Contiguous days
  // with the same bucket become one band; a two-quadrant (borderline) bucket
  // splits the band into stacked half-height tints. Drawn before gridlines so
  // the chart chrome stays on top.
  const regimeBands = {
    id: "regimeBands",
    beforeDraw(c) {
      const meta = regimeKeys;
      const area = c.chartArea;
      const x = c.scales && c.scales.x;
      if (!meta.length || !area || !x) return;
      const ctx = c.ctx;
      const step = meta.length > 1
        ? x.getPixelForValue(1) - x.getPixelForValue(0) : 0;
      let i = 0;
      while (i < meta.length) {
        const key = meta[i];
        let j = i;
        while (j + 1 < meta.length && meta[j + 1] === key) j++;
        if (key) {
          const quads = key.split("+").filter(q => QTINT[q]);
          if (quads.length) {
            const left  = Math.max(area.left,  x.getPixelForValue(i) - step / 2);
            const right = Math.min(area.right, x.getPixelForValue(j) + step / 2);
            const h = (area.bottom - area.top) / quads.length;
            quads.forEach((q, k) => {
              ctx.fillStyle = QTINT[q];
              ctx.fillRect(left, area.top + k * h, right - left, h);
            });
            if (right - left > 36) {
              ctx.fillStyle = "#8a93a6";
              ctx.font = "10px -apple-system, 'Segoe UI', sans-serif";
              ctx.textAlign = "center";
              ctx.fillText(key.replace("+", "/"), (left + right) / 2, area.top + 11);
            }
          }
        }
        i = j + 1;
      }
    },
  };

  // Transition-lean rail (2026-08-21 SWA lean-visibility cycle, Task B) —
  // the D-3 observation surface: once decision D-3 resolves and a joint lean
  // stages toward a projected quadrant, this is where it becomes visible
  // rather than inferred from JSON. A SEPARATE plugin (not folded into
  // regimeBands) so it is added ONLY to the main chart, never the sleeve
  // panel below, and so the realized band's own logic stays untouched.
  //
  // Deliberately a THIN RAIL along the chart-area bottom, not a second
  // full-height tint layer -- regimeBands already splits the full height
  // into stacked strips for a two-quadrant favored bucket, and a second
  // full-height overlay on top of that would make the realized band
  // unreadable. The rail is drawn on its OWN visual track so a
  // realized-Q4 / projected-Q2 session reads unambiguously as two different
  // quadrants, never as one blended color.
  //
  // Color note (B3a): reuses the EXISTING QCOLOR/QTINT hues already
  // validated for this panel (see the file-header comment) at a different
  // opacity/treatment -- no new hue is introduced, so no new palette
  // validation is required. `scripts/validate_palette.js` referenced there
  // does not exist in this repo (FOLLOWUPS -- tracked separately); reusing
  // existing hues sidesteps needing it here.
  const LEAN_RAIL_HEIGHT = 9;
  const LEAN_RAIL_ALPHA = 0.55;          // active: higher than QTINT's 0.10 band tint
  const LEAN_RAIL_UNKNOWN_ALPHA = 0.20;  // unknown: faint -- closer to "not asserted" than to "deployed"

  function _leanRailFill(q, alpha) {
    // QCOLOR is a hex "#rrggbb" -- reuse it at a different alpha rather than
    // introducing a new color (B3a).
    const hex = QCOLOR[q];
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  const leanRail = {
    id: "leanRail",
    beforeDraw(c) {
      const meta = leanKeys;
      const area = c.chartArea;
      const x = c.scales && c.scales.x;
      if (!meta.length || !area || !x) return;
      const ctx = c.ctx;
      const step = meta.length > 1 ? x.getPixelForValue(1) - x.getPixelForValue(0) : 0;
      const y = area.bottom - LEAN_RAIL_HEIGHT;
      let i = 0;
      while (i < meta.length) {
        const key = meta[i];  // "Q2|active" / "Q1|inert" / "Q1|unknown" / null (no lean, or unknown pre-#17 history)
        let j = i;
        while (j + 1 < meta.length && meta[j + 1] === key) j++;
        if (key) {
          const [q, state] = key.split("|");
          if (QCOLOR[q]) {
            const left  = Math.max(area.left,  x.getPixelForValue(i) - step / 2);
            const right = Math.min(area.right, x.getPixelForValue(j) + step / 2);
            if (state === "inert") {
              // Hatched/outlined, never solid -- reference weight pointed at
              // gate-blocked names is not deployed exposure.
              ctx.save();
              ctx.strokeStyle = QCOLOR[q];
              ctx.lineWidth = 1.5;
              ctx.setLineDash([3, 2]);
              ctx.strokeRect(left, y, right - left, LEAN_RAIL_HEIGHT);
              ctx.restore();
            } else if (state === "unknown") {
              // 2026-08-21 PR #43 review, Finding M1: a real projected
              // quadrant whose DEPLOYABILITY was never recorded (the
              // FOLLOWUPS #17 -> PR #42 Task F window). The quadrant hue is
              // kept -- the projection itself is known -- but at a faint
              // fill with no outline, so it reads as neither "deployed"
              // (active's bold solid) nor "known blocked" (inert's dashed
              // outline): a third, deliberately less assertive treatment.
              ctx.fillStyle = _leanRailFill(q, LEAN_RAIL_UNKNOWN_ALPHA);
              ctx.fillRect(left, y, right - left, LEAN_RAIL_HEIGHT);
            } else {
              ctx.fillStyle = _leanRailFill(q, LEAN_RAIL_ALPHA);
              ctx.fillRect(left, y, right - left, LEAN_RAIL_HEIGHT);
            }
          }
        }
        i = j + 1;
      }
    },
  };

  function renderChart(data) {
    const series = data.series || [];
    const labels = series.map(p => p.date);
    // Convert normalized (start=100) into percent change from start.
    const pfData  = series.map(p => p.portfolio_norm != null ? p.portfolio_norm - 100 : null);
    const spyData = series.map(p => p.spy_norm       != null ? p.spy_norm       - 100 : null);

    const datasets = [
      { label: "Portfolio", data: pfData, borderColor: "#4f8cff", backgroundColor: "transparent",
        borderWidth: 2, tension: 0.15, pointRadius: 2, pointHoverRadius: 5 },
      { label: "S&P 500 (SPY)", data: spyData, borderColor: "#e0524d", backgroundColor: "transparent",
        borderWidth: 2, tension: 0.15, pointRadius: 2, pointHoverRadius: 5, borderDash: [5, 4] },
    ];
    // Quadrant basket lines — context, so thinner and point-free.
    const hasQuads = series.some(p => p.quadrants);
    if (hasQuads) {
      for (const q of QUADS) {
        datasets.push({
          label: QLABEL[q],
          data: series.map(p => p.quadrants && p.quadrants[q] != null ? p.quadrants[q] - 100 : null),
          borderColor: QCOLOR[q], backgroundColor: "transparent",
          borderWidth: 1.5, tension: 0.15, pointRadius: 0, pointHoverRadius: 4,
          spanGaps: true,
        });
      }
    }

    const cfg = {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#e6e8ee" } },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const v = ctx.parsed.y;
                return v == null ? `${ctx.dataset.label}: —`
                  : `${ctx.dataset.label}: ${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
              },
              footer: (items) => {
                const p = series[items[0] && items[0].dataIndex];
                const lines = [];
                const b = p && p.favored_bucket;
                if (b && b.length) lines.push(`Favored: ${b.join("/")}`);
                const lean = p && p.lean;
                if (lean && lean.projected_quadrant) {
                  const pct = `${Math.round((lean.staged_fraction || 0) * 100)}%`;
                  const base = `Lean: ${lean.projected_quadrant} (${lean.direction}, ${pct})`;
                  // Finding M1 (PR #43 review): a THIRD wording for the
                  // deployability-unknown epoch -- never reuse "gate-blocked"
                  // for a session that was never evaluated; that asserts a
                  // fact this data cannot support, the same class of error
                  // as asserting "deployed."
                  if (lean.inert == null) {
                    lines.push(`${base} — deployability unknown (predates lean diagnostics)`);
                  } else if (lean.inert) {
                    lines.push(`${base} — gate-blocked, not buyable`);
                  } else {
                    lines.push(base);
                  }
                }
                return lines;
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: "#8a93a6", maxTicksLimit: 10 }, grid: { color: "#262c38" } },
          y: {
            ticks: {
              color: "#8a93a6",
              callback: (v) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`,
            },
            grid: { color: "#262c38" },
            title: { display: true, text: "% change from window start", color: "#8a93a6" },
          },
        },
      },
      plugins: [regimeBands, leanRail],
    };

    const canvas = document.getElementById("perfChart");
    if (chart) chart.destroy();
    regimeKeys = series.map(p => (p.favored_bucket || []).join("+"));
    // "Q2|active" / "Q1|inert" / "Q1|unknown" per point; null for no active
    // lean at all (known-no-lean or unknown pre-#17 history alike -- both
    // render as blank rail, see leanRail above). `inert` is a THREE-way
    // value (Finding M1, PR #43 review) -- `lean.inert == null` catches both
    // `undefined` (field never sent) and an explicit `null` (the #17-to-#42
    // window, deployability never recorded), deliberately NOT a bare falsy
    // check, which would wrongly collapse "unknown" into "active" exactly
    // like the pre-fix `lean.inert ? "inert" : "active"` did.
    leanKeys = series.map(p => {
      const lean = p.lean;
      if (!lean || !lean.projected_quadrant) return null;
      const state = lean.inert == null ? "unknown" : (lean.inert ? "inert" : "active");
      return `${lean.projected_quadrant}|${state}`;
    });
    chart = new Chart(canvas, cfg);
  }

  // Sample-size honesty (required, not optional — Task E): the closed-trade
  // count is always shown, and no Sharpe/win-rate/skill statistic is ever
  // computed or displayed on this panel at any N.
  function renderSleeveSummary(data) {
    const el = document.getElementById("sleeve-summary");
    if (!el) return;
    if (!data.sleeve_available) {
      el.textContent = "No sleeve activity recorded yet.";
      return;
    }
    const n = data.sleeve_closed_trade_count_total || 0;
    el.innerHTML = n < SLEEVE_MIN_N
      ? `<strong>${n} closed trade${n === 1 ? "" : "s"}</strong> since inception — ` +
        `below ${SLEEVE_MIN_N}, not yet a meaningful sample (line shown dashed/greyed).`
      : `<strong>${n} closed trades</strong> since inception.`;
  }

  // Separate panel, separate y-axis unit (percentage points of equity
  // contributed, not a buy-and-hold index) — shares x-axis labels, the
  // window selector, and the regimeBands plugin/regimeKeys with the main
  // chart above (renderChart must run first each load() to set regimeKeys).
  function renderSleeveChart(data) {
    const canvas = document.getElementById("sleeveChart");
    if (sleeveChart) { sleeveChart.destroy(); sleeveChart = null; }
    if (!data.sleeve_available) return;

    const series = data.series || [];
    const labels = series.map(p => p.date);
    const n = data.sleeve_closed_trade_count_total || 0;
    const thin = n < SLEEVE_MIN_N;
    const sleeveData = series.map(p => p.sleeve_contribution_pp != null ? p.sleeve_contribution_pp : null);

    const cfg = {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Flex sleeve contribution",
          data: sleeveData,
          borderColor: thin ? SLEEVE_THIN_COLOR : SLEEVE_COLOR,
          backgroundColor: "transparent",
          borderWidth: 2,
          borderDash: thin ? [5, 4] : [],
          tension: 0.15, pointRadius: 0, pointHoverRadius: 4, spanGaps: true,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: thin ? SLEEVE_THIN_COLOR : "#e6e8ee" } },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const v = ctx.parsed.y;
                const pt = series[ctx.dataIndex];
                const tc = pt && pt.sleeve_trade_count != null ? pt.sleeve_trade_count : null;
                const pct = v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(3)}pp`;
                return tc != null ? `${ctx.dataset.label}: ${pct} (${tc} trades)` : `${ctx.dataset.label}: ${pct}`;
              },
            },
          },
        },
        scales: {
          x: { ticks: { color: "#8a93a6", maxTicksLimit: 10 }, grid: { color: "#262c38" } },
          y: {
            ticks: { color: "#8a93a6", callback: (v) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}pp` },
            grid: { color: "#262c38" },
            title: { display: true, text: "percentage points of equity contributed", color: "#8a93a6" },
          },
        },
      },
      plugins: [regimeBands],
    };
    sleeveChart = new Chart(canvas, cfg);
  }

  // Static legend for the leanRail track (Task B4) — shown whenever the
  // series carries the `lean` field at all (even if every value is null in
  // the current window), same convention as the always-shown bandnote caption
  // above the main chips row.
  function renderLeanLegend(data) {
    const el = document.getElementById("lean-legend");
    if (!el) return;
    const series = data.series || [];
    const hasLeanData = series.some(p => "lean" in p);
    if (!hasLeanData) { el.innerHTML = ""; return; }
    el.innerHTML =
      `<span>Bottom rail: transition lean (projected quadrant), colored by quadrant at higher opacity than the band above</span>` +
      `<span><span class="swatch-rail inert"></span> inert — gate-blocked, not buyable</span>` +
      `<span><span class="swatch-rail unknown"></span> lean — deployability unknown (pre-2026-08-21 history)</span>`;
  }

  function renderTable(data) {
    const tbody = document.querySelector("#valuations tbody");
    const rows = data.holdings || [];
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="muted">No holdings.</td></tr>`;
      return;
    }
    tbody.innerHTML = rows.map(h => `
      <tr>
        <td><strong>${h.ticker}</strong></td>
        <td>${fmtQty(h.quantity)}</td>
        <td>${h.weight_pct != null ? h.weight_pct.toFixed(2) + "%" : "—"}</td>
        <td>${fmtMoney(h.cost_basis)}</td>
        <td>${fmtMoney(h.market_value)}</td>
        <td class="${cls(h.total_gain)}">${fmtMoney(h.total_gain)} <span class="muted">(${fmtPct(h.total_gain_pct)})</span></td>
        <td class="muted">${h.dividends_gain == null ? "—" : fmtMoney(h.dividends_gain)}</td>
      </tr>`).join("");
  }

  // Wire window-tab buttons.
  document.getElementById("window-tabs").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-window]");
    if (!btn) return;
    document.querySelectorAll("#window-tabs .btn").forEach(b => b.classList.remove("primary"));
    btn.classList.add("primary");
    load(btn.dataset.window);
  });

  // Initial load = 1Y.
  load("1Y");
})();
