// performance.html — portfolio vs SPY chart + holdings valuation table.
(() => {
  const fmtMoney = (v) => v == null || isNaN(v) ? "—"
    : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  const fmtPct = (v) => v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
  const fmtQty = (v) => v == null ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 });
  const cls = (v) => v == null || v === 0 ? "" : (v > 0 ? "action-buy" : "action-sell");

  let chart = null;

  async function load(windowKey) {
    const summaryEl = document.getElementById("perf-summary");
    summaryEl.textContent = "Loading…";
    try {
      const data = await window.pfauto.api(`/api/performance?window=${encodeURIComponent(windowKey)}`);
      renderChart(data);
      renderTable(data);
      renderSummary(data);
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

  function renderChart(data) {
    const series = data.series || [];
    const labels = series.map(p => p.date);
    // Convert normalized (start=100) into percent change from start.
    const pfData  = series.map(p => p.portfolio_norm != null ? p.portfolio_norm - 100 : null);
    const spyData = series.map(p => p.spy_norm       != null ? p.spy_norm       - 100 : null);

    const cfg = {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Portfolio", data: pfData, borderColor: "#4f8cff", backgroundColor: "transparent",
            borderWidth: 2, tension: 0.15, pointRadius: 2, pointHoverRadius: 5 },
          { label: "S&P 500 (SPY)", data: spyData, borderColor: "#e0524d", backgroundColor: "transparent",
            borderWidth: 2, tension: 0.15, pointRadius: 2, pointHoverRadius: 5, borderDash: [5, 4] },
        ],
      },
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
    };

    const canvas = document.getElementById("perfChart");
    if (chart) chart.destroy();
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
