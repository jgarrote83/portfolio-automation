// history.html — list past report dates.
(async () => {
  const ul = document.getElementById("dates");
  try {
    const dates = await window.pfauto.api("/api/dates");
    if (!dates.length) { ul.innerHTML = `<li class="muted">No reports yet.</li>`; return; }
    ul.innerHTML = dates.map(d => `<li><a href="/today.html?date=${d}">${d}</a></li>`).join("");
  } catch (e) {
    ul.innerHTML = `<li class="muted">Error: ${e.message}</li>`;
  }
})();
