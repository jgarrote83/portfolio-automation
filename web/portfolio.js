// portfolio.html — current holdings from latest snapshot.
(async () => {
  const tbody = document.querySelector("#holdings tbody");
  const summaryEl = document.getElementById("summary");

  const fmtMoney = (v) => v == null || isNaN(v) ? "—"
    : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  const fmtPct = (v) => v == null || isNaN(v) ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const fmtQty = (v) => v == null ? "" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 });
  const cls = (v) => v == null || v === 0 ? "" : (v > 0 ? "action-buy" : "action-sell");

  try {
    const dates = await window.pfauto.api("/api/dates");
    if (!dates.length) { tbody.innerHTML = `<tr><td colspan="8" class="muted">No data.</td></tr>`; return; }
    const snap = await window.pfauto.api(`/api/snapshot/${dates[0]}`);
    const positions = (snap.portfolio && snap.portfolio.positions) || [];
    const balances  = (snap.portfolio && snap.portfolio.balances)  || {};
    const prices    = snap.prices || {};
    const totalMv = balances.netMv || positions.reduce((s, p) => s + (p.market_value || 0), 0);

    if (summaryEl) {
      summaryEl.innerHTML = `
        <div><span class="muted">As of</span> <strong>${snap.date || dates[0]}</strong></div>
        <div><span class="muted">Total Account</span> <strong>${fmtMoney(balances.totalAccountValue)}</strong></div>
        <div><span class="muted">Net Market Value</span> <strong>${fmtMoney(balances.netMv)}</strong></div>
        <div><span class="muted">Cash</span> <strong>${fmtMoney(balances.cashAvailableForInvestment)}</strong></div>
        <div><span class="muted">Day P/L</span> <strong class="${cls(balances.dayGainDollar)}">${fmtMoney(balances.dayGainDollar)} (${fmtPct(balances.dayGainPct)})</strong></div>
        <div><span class="muted">Total Gain</span> <strong class="${cls(balances.totalGainDollar)}">${fmtMoney(balances.totalGainDollar)} (${fmtPct(balances.totalGainPct)})</strong></div>`;
    }

    if (!positions.length) { tbody.innerHTML = `<tr><td colspan="8" class="muted">No positions.</td></tr>`; return; }

    positions.sort((a, b) => (b.market_value || 0) - (a.market_value || 0));

    tbody.innerHTML = positions.map(p => {
      const last = prices[p.ticker]?.c;
      const weight = totalMv ? (p.market_value / totalMv) * 100 : null;
      const totalGainPct = p.cost_basis ? (p.total_gain / p.cost_basis) * 100 : null;
      const dayGainPct = (p.market_value && p.day_gain != null)
        ? (p.day_gain / (p.market_value - p.day_gain)) * 100 : null;
      return `
        <tr>
          <td><strong>${p.ticker}</strong></td>
          <td>${fmtQty(p.quantity)}</td>
          <td>${weight != null ? weight.toFixed(2) + "%" : "—"}</td>
          <td>${last != null ? fmtMoney(last) : "—"}</td>
          <td>${fmtMoney(p.market_value)}</td>
          <td>${fmtMoney(p.cost_basis)}</td>
          <td class="${cls(p.day_gain)}">${fmtMoney(p.day_gain)} <span class="muted">(${fmtPct(dayGainPct)})</span></td>
          <td class="${cls(p.total_gain)}">${fmtMoney(p.total_gain)} <span class="muted">(${fmtPct(totalGainPct)})</span></td>
        </tr>`;
    }).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="muted">Error: ${e.message}</td></tr>`;
  }
})();
