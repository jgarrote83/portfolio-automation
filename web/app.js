// Shared client-side helpers for Portfolio Automation SWA.

/** GET JSON from the SWA managed API. */
async function api(path, init) {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) throw new Error(`${path} → ${res.status} ${res.statusText}`);
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

/** POST JSON */
async function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Fill the top-right user label from SWA's built-in /.auth/me. */
async function renderUser() {
  const el = document.getElementById("user");
  if (!el) return;
  try {
    const r = await fetch("/.auth/me");
    const data = await r.json();
    const p = data && data.clientPrincipal;
    if (p) {
      el.innerHTML = `${p.userDetails} &middot; <a href="/logout">sign out</a>`;
    } else {
      el.innerHTML = `<a href="/login">sign in</a>`;
    }
  } catch {
    el.textContent = "";
  }
}

/** Learning tab nav link — only rendered when the API reports phase >= 2
 * (FOLLOWUPS #13/#32). Phase 1 ships dry-run only; there is no tab yet. */
async function renderLearningNav() {
  const nav = document.querySelector(".topnav nav");
  if (!nav || nav.querySelector("[data-learning-nav]")) return;
  try {
    const r = await fetch("/api/learning/proposals");
    if (!r.ok) return;
    const data = await r.json();
    if ((data.phase || 0) < 2) return;
    const a = document.createElement("a");
    a.href = "/learning.html";
    a.textContent = "Learning";
    a.dataset.learningNav = "1";
    if (location.pathname.endsWith("/learning.html")) a.className = "active";
    nav.appendChild(a);
  } catch {
    // Never let a Learning-tab check break navigation on any other page.
  }
}

document.addEventListener("DOMContentLoaded", renderUser);
document.addEventListener("DOMContentLoaded", renderLearningNav);

window.pfauto = { api, postJson };
