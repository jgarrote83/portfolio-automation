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

  let chart = null;
  let regimeKeys = [];  // per-point favored-bucket key, set before chart build

  async function load(windowKey) {
    const summaryEl = document.getElementById("perf-summary");
    summaryEl.textContent = "Loading…";
    try {
      const data = await window.pfauto.api(`/api/performance?window=${encodeURIComponent(windowKey)}`);
      renderChart(data);
      renderTable(data);
      renderSummary(data);
      renderQuadSummary(data);
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
    const rows = QUADS
      .map(q => ({ q, ret: last.quadrants[q] != null ? last.quadrants[q] - 100 : null }))
      .filter(r => r.ret != null);
    if (!rows.length) { el.innerHTML = ""; return; }
    const best = rows.reduce((a, b) => (b.ret > a.ret ? b : a)).q;
    el.innerHTML = rows.map(({ q, ret }) => {
      const alpha = spyRet != null ? ret - spyRet : null;
      return `<span class="chip${q === best ? " best" : ""}" title="${QLABEL[q]} equal-weight basket, this window">` +
        `<span class="swatch" style="background:${QCOLOR[q]}"></span>` +
        `${QLABEL[q]}${q === best ? " ★" : ""} ` +
        `<strong class="${cls(ret)}">${fmtPct(ret)}</strong>` +
        `<span class="muted"> (α ${fmtPct(alpha)})</span></span>`;
    }).join("") +
      `<span class="muted bandnote">Shaded bands = favored quadrant(s) that day; ★ = best in window.</span>`;
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
                const b = p && p.favored_bucket;
                return b && b.length ? `Favored: ${b.join("/")}` : "";
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
      plugins: [regimeBands],
    };

    const canvas = document.getElementById("perfChart");
    if (chart) chart.destroy();
    regimeKeys = series.map(p => (p.favored_bucket || []).join("+"));
    chart = new Chart(canvas, cfg);
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
