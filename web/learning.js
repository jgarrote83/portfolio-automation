// learning.html — Learning Loop tab: pending proposals, run view, history.
// Phase 1 = no tab nav entry at all (see app.js renderLearningNav). Phase 2 =
// read-only (decision buttons hidden, banner shown). Phase 3 = full loop.

(async () => {
  const pendingBody = document.querySelector("#pending tbody");
  const historyBody = document.querySelector("#history tbody");
  const narrativeEl = document.getElementById("run-narrative");
  const statsEl = document.getElementById("run-stats");
  const runBtn = document.getElementById("run-now");
  const runStatusEl = document.getElementById("run-status");
  const readonlyBanner = document.getElementById("readonly-banner");

  const TYPE_BADGE = { 0: ["Note", "note"], 1: ["Small change", "small"], 2: ["Small change", "small"], 3: ["Structural", "structural"] };

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function badge(cls) {
    const [label, css] = TYPE_BADGE[cls] || TYPE_BADGE[0];
    return `<span class="badge ${css}">${label}</span>`;
  }

  function parseEvidence(row) {
    try {
      const v = JSON.parse(row.evidence || "[]");
      return Array.isArray(v) ? v : [];
    } catch {
      return [];
    }
  }

  function detailRow(p, phase) {
    const evidence = parseEvidence(p);
    const evidenceHtml = evidence.length
      ? `<ul>${evidence.map(e => `<li>${esc(e)}</li>`).join("")}</ul>`
      : `<p class="muted">No evidence listed.</p>`;

    let artifactHtml;
    if (p.class === 3) {
      artifactHtml = `
        <p><a href="#" data-copy-brief="${esc(p.RowKey)}">Copy implementation brief</a></p>
        <pre class="markdown">${esc(p.spec_draft || "")}</pre>`;
    } else if (p.diff) {
      artifactHtml = `<pre class="markdown">${esc(p.diff)}</pre>`;
    } else {
      artifactHtml = `<p class="muted">No diff.</p>`;
    }

    const decisionHtml = (phase >= 3 && p.status === "pending") ? `
      <div class="decision-row">
        <button class="btn primary" data-approve="${esc(p.RowKey)}">Approve</button>
        <button class="btn" data-reject="${esc(p.RowKey)}">Reject</button>
        <input type="text" placeholder="Reason (required to reject)" data-reason="${esc(p.RowKey)}" />
      </div>` : "";

    return `
      <tr class="detail-row" style="display:none">
        <td colspan="5">
          ${evidenceHtml}
          <p><strong>Expected effect:</strong> ${esc(p.expected_effect || "")}</p>
          <p><strong>Falsifier:</strong> ${esc(p.falsifier || "")}</p>
          <p><strong>Review by:</strong> ${esc(p.review_by || "")}</p>
          ${artifactHtml}
          ${decisionHtml}
        </td>
      </tr>`;
  }

  function renderPending(rows, phase) {
    if (!rows.length) {
      pendingBody.innerHTML = `<tr><td colspan="5" class="muted">No pending proposals.</td></tr>`;
      return;
    }
    pendingBody.innerHTML = rows.map((p, i) => `
      <tr class="pending-row" data-idx="${i}">
        <td>${i + 1}</td>
        <td>${esc(p.change_summary)}</td>
        <td>${badge(p.class)}</td>
        <td>${esc(p.data_summary)}</td>
        <td>${phase >= 3 ? "" : `<span class="muted">read-only</span>`}</td>
      </tr>
      ${detailRow(p, phase)}
    `).join("");

    pendingBody.querySelectorAll(".pending-row").forEach(r => {
      r.addEventListener("click", () => {
        const detail = r.nextElementSibling;
        detail.style.display = detail.style.display === "none" ? "table-row" : "none";
      });
    });
    pendingBody.querySelectorAll("[data-approve]").forEach(btn =>
      btn.addEventListener("click", e => { e.stopPropagation(); decide(btn.dataset.approve, "approve"); }));
    pendingBody.querySelectorAll("[data-reject]").forEach(btn =>
      btn.addEventListener("click", e => {
        e.stopPropagation();
        const id = btn.dataset.reject;
        const input = pendingBody.querySelector(`[data-reason="${CSS.escape(id)}"]`);
        decide(id, "reject", input ? input.value : "");
      }));
    pendingBody.querySelectorAll("[data-copy-brief]").forEach(a =>
      a.addEventListener("click", e => {
        e.preventDefault();
        e.stopPropagation();
        const row = rows.find(p => p.RowKey === a.dataset.copyBrief);
        if (row && navigator.clipboard) navigator.clipboard.writeText(row.implementation_brief || "");
      }));
  }

  async function decide(id, decision, reason) {
    if (decision === "reject" && !(reason && reason.trim())) {
      alert("A reason is required to reject.");
      return;
    }
    try {
      await window.pfauto.postJson("/api/learning/decision", { id, decision, reason });
      await load();
    } catch (e) {
      alert(`Error: ${e.message}`);
    }
  }

  function renderHistory(rows) {
    if (!rows.length) {
      historyBody.innerHTML = `<tr><td colspan="7" class="muted">No history yet.</td></tr>`;
      return;
    }
    historyBody.innerHTML = rows.map(p => `
      <tr>
        <td>${esc(p.cycle)}</td>
        <td>${esc(p.change_summary)}</td>
        <td>${badge(p.class)}</td>
        <td>${esc(p.status)}</td>
        <td>${esc(p.decision_reason || "")}</td>
        <td>${p.pr_url ? `<a href="${esc(p.pr_url)}" target="_blank" rel="noopener">PR</a>` : ""}</td>
        <td>${p.resolved_correct === true ? "✅" : p.resolved_correct === false ? "❌" : "—"}</td>
      </tr>`).join("");
  }

  function renderRun(data) {
    const last = data.last_cycle;
    if (!last) {
      narrativeEl.textContent = "No cycles have run yet.";
      statsEl.textContent = "";
      return;
    }
    if (last.status === "failed_validation") {
      narrativeEl.innerHTML = `<span class="action-sell">Cycle ${esc(last.RowKey)} FAILED validation:</span> ${esc(last.error || "")}`;
    } else {
      narrativeEl.textContent = last.narrative || "";
    }
    statsEl.textContent = `Cycle ${last.RowKey || ""} · ${last.trigger || ""} · model ${last.model || ""} · ${last.proposal_count ?? 0} proposal(s) · mode ${last.mode || ""}`;
  }

  async function load() {
    const data = await window.pfauto.api("/api/learning/proposals");
    const phase = data.phase || 1;
    readonlyBanner.style.display = phase === 2 ? "block" : "none";
    runBtn.style.display = phase >= 2 ? "inline-block" : "none";
    renderPending(data.pending || [], phase);
    renderHistory(data.history || []);
    renderRun(data);
  }

  runBtn.addEventListener("click", async () => {
    runStatusEl.textContent = "Running…";
    try {
      const result = await window.pfauto.postJson("/api/learning/run", {});
      runStatusEl.textContent = result.status === "started"
        ? "Started — running in the background, check back shortly."
        : `Done: ${result.status}`;
      await load();
    } catch (e) {
      runStatusEl.textContent = `Error: ${e.message}`;
    }
  });

  try {
    await load();
  } catch (e) {
    narrativeEl.textContent = `Error loading Learning tab: ${e.message}`;
  }
})();
