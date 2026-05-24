// portfolio.html — current holdings from latest snapshot.
(async () => {
  const tbody = document.querySelector("#holdings tbody");
  try {
    const dates = await window.pfauto.api("/api/dates");
    if (!dates.length) { tbody.innerHTML = `<tr><td colspan="6" class="muted">No data.</td></tr>`; return; }
    const snap = await window.pfauto.api(`/api/snapshot/${dates[0]}`);
    const positions = (snap.portfolio && snap.portfolio.positions) || [];
    if (!positions.length) { tbody.innerHTML = `<tr><td colspan="6" class="muted">No positions.</td></tr>`; return; }
    tbody.innerHTML = positions.map(p => `
      <tr>
        <td>${p.ticker}</td>
        <td>${p.quantity}</td>
        <td>${p.weight != null ? (p.weight * 100).toFixed(1) + "%" : ""}</td>
        <td>${p.last_price ?? ""}</td>
        <td>${p.day_pl ?? ""}</td>
        <td>${p.unrealised ?? ""}</td>
      </tr>`).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="6" class="muted">Error: ${e.message}</td></tr>`;
  }
})();
