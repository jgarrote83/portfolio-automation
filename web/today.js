// today.html — load latest report + trade recommendations.

(async () => {
  const dateEl = document.getElementById("date");
  const reportEl = document.getElementById("report");
  const tbody = document.querySelector("#trades tbody");
  const selectAll = document.getElementById("select-all");
  const approveBtn = document.getElementById("approve-selected");
  const rejectBtn = document.getElementById("reject-selected");
  const statusEl = document.getElementById("trade-status");

  let currentDate = null;

  function selectedIds() {
    return Array.from(tbody.querySelectorAll("input[type=checkbox]:checked"))
      .map(cb => cb.dataset.id);
  }

  function updateButtons() {
    const n = selectedIds().length;
    approveBtn.disabled = n === 0;
    rejectBtn.disabled = n === 0;
    approveBtn.textContent = n ? `Approve ${n} trade${n > 1 ? "s" : ""}` : "Approve selected";
  }

  tbody.addEventListener("change", updateButtons);
  selectAll.addEventListener("change", () => {
    tbody.querySelectorAll("input[type=checkbox]").forEach(cb => (cb.checked = selectAll.checked));
    updateButtons();
  });

  async function decide(verb) {
    const ids = selectedIds();
    if (!ids.length) return;
    statusEl.textContent = `${verb}ing ${ids.length}…`;
    try {
      await window.pfauto.postJson(`/api/trades/${currentDate}/${verb}`, { ids });
      statusEl.textContent = `${verb}d ${ids.length} trade(s).`;
      await loadTrades();
    } catch (e) {
      statusEl.textContent = `Error: ${e.message}`;
    }
  }
  approveBtn.addEventListener("click", () => decide("approve"));
  rejectBtn.addEventListener("click", () => decide("reject"));

  async function loadTrades() {
    const trades = await window.pfauto.api(`/api/trades/${currentDate}`);
    if (!trades.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="muted">No trade recommendations.</td></tr>`;
      return;
    }
    tbody.innerHTML = trades.map(t => `
      <tr>
        <td><input type="checkbox" data-id="${t.id}" ${t.status === "pending" ? "" : "disabled"} /></td>
        <td class="action-${(t.action || "").toLowerCase()}">${t.action}</td>
        <td>${t.ticker}</td>
        <td>${t.quantity}</td>
        <td>${t.limit_price ?? "mkt"}</td>
        <td>${(t.confidence ?? 0).toFixed(2)}</td>
        <td>${t.rationale ?? ""}</td>
      </tr>`).join("");
    updateButtons();
  }

  try {
    const dates = await window.pfauto.api("/api/dates");
    currentDate = dates[0];
    dateEl.textContent = currentDate ? `(${currentDate})` : "(no data yet)";
    if (!currentDate) {
      reportEl.textContent = "No reports yet — run the collector.";
      tbody.innerHTML = `<tr><td colspan="7" class="muted">No data.</td></tr>`;
      return;
    }
    const md = await window.pfauto.api(`/api/report/${currentDate}`);
    reportEl.innerHTML = window.marked ? window.marked.parse(md) : `<pre>${md}</pre>`;
    await loadTrades();
  } catch (e) {
    reportEl.textContent = `Error loading report: ${e.message}`;
  }
})();
