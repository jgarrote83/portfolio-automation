// sleeve_trades.js — the Flex catalyst sleeve's per-ticker view (performance.html).
//
// Pure functions only (no DOM, no Chart.js, no fetch) so the logic is unit-testable under Node
// (tests/test_sleeve_trades_js.py). performance.js owns the drawing; this file owns the numbers:
//   * buildMarkers  — which ticker was chosen / closed, and where on the sleeve line that happened
//   * byTickerHtml  — trades and total gain % per ticker, since inception
//   * tradeLogHtml  — every trade, open and closed
//
// Doctrine (the panel's standing rule): NO win rate, Sharpe or other skill statistic, at any sample size.
// "Gain %" is total realised P&L / total cost of the shares sold — never an average of trade returns,
// never annualised. A trade whose P&L the engine could not determine is counted but contributes nothing.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SleeveTrades = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const groupThousands = (s) => s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const isNum = (v) => typeof v === "number" && isFinite(v);

  // Rounded to the shown precision FIRST, so a -0.004 never renders as "-$0.00".
  function fmtUsd(v, signed) {
    if (!isNum(v)) return "—";
    const r = Math.round(v * 100) / 100;
    if (r === 0) return "$0.00";
    const body = groupThousands(Math.abs(r).toFixed(2));
    return `${r < 0 ? "-" : (signed === false ? "" : "+")}$${body}`;
  }
  function fmtPct(v, digits) {
    if (!isNum(v)) return "—";
    const d = digits == null ? 2 : digits;
    const f = 10 ** d;
    const r = Math.round(v * f) / f;
    if (r === 0) return `${(0).toFixed(d)}%`;
    return `${r > 0 ? "+" : "-"}${Math.abs(r).toFixed(d)}%`;
  }
  function fmtPp(v) {
    if (!isNum(v)) return "—";
    const r = Math.round(v * 1000) / 1000;
    if (r === 0) return "0.000pp";
    return `${r > 0 ? "+" : "-"}${Math.abs(r).toFixed(3)}pp`;
  }
  const fmtPrice = (v) => (isNum(v) ? `$${v.toFixed(2)}` : "—");
  const fmtQty = (v) => (isNum(v) ? String(Number(v.toFixed(4))) : "—");

  const EXIT_LABELS = {
    stop_fill: "Stop hit",
    time_stop: "Time stop",
    take_profit: "Take profit",
    scale_out: "Scale-out",
    conviction_released: "Conviction released",
  };
  function exitLabel(reason) {
    if (!reason) return "—";
    if (EXIT_LABELS[reason]) return EXIT_LABELS[reason];
    const t = String(reason).replace(/_/g, " ");
    return t.charAt(0).toUpperCase() + t.slice(1);
  }

  // "gain" | "loss" | "flat" | "unknown" — unknown is a trade whose P&L the engine could not determine.
  function outcomeOf(pnl) {
    if (!isNum(pnl)) return "unknown";
    return pnl > 0 ? "gain" : (pnl < 0 ? "loss" : "flat");
  }
  const colorClass = (v) => (!isNum(v) || v === 0 ? "" : (v > 0 ? "action-buy" : "action-sell"));

  // ------------------------------------------------------------------------------------------ chart markers
  // Where the chart can place a date: the first chart label on or after it. A date before the window's first
  // label, or after its last, is NOT drawn (the tables still list the trade) — never clamped to an edge, which
  // would put a marker on a day the trade did not happen.
  function indexFor(labels, date) {
    if (!date || !labels.length || date < labels[0] || date > labels[labels.length - 1]) return -1;
    for (let i = 0; i < labels.length; i++) if (labels[i] >= date) return i;
    return -1;
  }
  // The sleeve line's value at index i, or the nearest earlier (then later) non-null value. null = no line there at all.
  function lineValueNear(values, i) {
    if (isNum(values[i])) return values[i];
    for (let j = i - 1; j >= 0; j--) if (isNum(values[j])) return values[j];
    for (let j = i + 1; j < values.length; j++) if (isNum(values[j])) return values[j];
    return null;
  }
  function mergeInto(map, kind, index, y, item) {
    const key = String(index);
    let m = map.get(key);
    if (!m) { m = { kind, index, y, items: [] }; map.set(key, m); }
    m.items.push(item);
  }
  function finishMarker(m) {
    m.symbols = m.items.map((it) => it.symbol);
    if (m.kind === "exit") {
      const known = m.items.filter((it) => isNum(it.pnl_usd));
      const total = known.reduce((a, it) => a + it.pnl_usd, 0);
      m.outcome = known.length ? outcomeOf(total) : "unknown";
      m.label = m.items.map((it) => (isNum(it.return_pct) ? `${it.symbol} ${fmtPct(it.return_pct, 1)}` : it.symbol)).join(" · ");
    } else {
      m.outcome = "entry";
      m.label = m.symbols.join(" · ");
    }
    return m;
  }

  // series: the /api/performance series (date + sleeve_contribution_pp). st: payload.sleeve_trades.
  // Returns {entries, exits}: one marker per chart index (same-day trades merge into one marker with a joined label).
  // Entry = the ticker chosen (every closed trade's entry plus every position still open); exit = a trade closed.
  function buildMarkers(series, st) {
    const out = { entries: [], exits: [] };
    if (!st || !st.available || !Array.isArray(series) || !series.length) return out;
    const labels = series.map((p) => p.date);
    const values = series.map((p) => (p.sleeve_contribution_pp == null ? null : p.sleeve_contribution_pp));
    const entries = new Map();
    const exits = new Map();
    const place = (map, kind, date, item) => {
      const i = indexFor(labels, date);
      if (i < 0) return;
      const y = lineValueNear(values, i);
      if (y == null) return;
      mergeInto(map, kind, i, y, item);
    };
    for (const t of st.trades || []) {
      place(entries, "entry", t.entry_date, { symbol: t.symbol, price: t.entry_price, date: t.entry_date, open: false });
      place(exits, "exit", t.closed_date, {
        symbol: t.symbol, date: t.closed_date, pnl_usd: t.pnl_usd, return_pct: t.return_pct, exit_reason: t.exit_reason,
        exit_price: t.exit_price,
      });
    }
    for (const o of st.open || []) {
      place(entries, "entry", o.entry_date, { symbol: o.symbol, price: o.entry_price, date: o.entry_date, open: true });
    }
    out.entries = [...entries.values()].map(finishMarker).sort((a, b) => a.index - b.index);
    out.exits = [...exits.values()].map(finishMarker).sort((a, b) => a.index - b.index);
    return out;
  }

  // Tooltip lines for one marker.
  function tooltipLines(m) {
    if (m.kind === "entry") {
      return m.items.map((it) => `${it.open ? "Holding" : "Entered"} ${it.symbol}${isNum(it.price) ? ` @ ${fmtPrice(it.price)}` : ""} (${it.date})`);
    }
    return m.items.map((it) => {
      const bits = [`Closed ${it.symbol}`];
      if (isNum(it.pnl_usd)) bits.push(`${fmtUsd(it.pnl_usd)}${isNum(it.return_pct) ? ` (${fmtPct(it.return_pct)})` : ""}`);
      else bits.push("P&L unavailable");
      bits.push(exitLabel(it.exit_reason));
      return bits.join(" — ");
    });
  }

  // Label collision avoidance (used by the canvas plugin): the first vertical offset whose box overlaps nothing already placed.
  function overlaps(a, b) { return a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0; }
  function pickLabelOffset(box, placed, offsets) {
    for (const dy of offsets) {
      const moved = { x0: box.x0, x1: box.x1, y0: box.y0 + dy, y1: box.y1 + dy };
      if (!placed.some((p) => overlaps(moved, p))) return dy;
    }
    return offsets[0];
  }

  // ------------------------------------------------------------------------------------------------- tables
  const td = (v, cls, title) => `<td${cls ? ` class="${cls}"` : ""}${title ? ` title="${esc(title)}"` : ""}>${v}</td>`;

  function byTickerHtml(st) {
    if (!st || !st.available || !(st.by_symbol || []).length) return `<p class="muted">No flex trades recorded yet.</p>`;
    const rows = st.by_symbol.map((b) => {
      const unk = b.pnl_unavailable > 0 ? `${b.pnl_unavailable} closed trade${b.pnl_unavailable === 1 ? "" : "s"} with unknown P&L left out of this row's P&L and gain %` : "";
      const status = b.open ? "open position" : "flat";
      return `<tr>${td(`<strong>${esc(b.symbol)}</strong>`)}${td(b.closed_trades, "num", unk)}${td(esc(status))}` +
        `${td(fmtUsd(b.capital_usd, false), "num")}${td(fmtUsd(b.pnl_usd), `num ${colorClass(b.pnl_usd)}`, unk)}` +
        `${td(fmtPct(b.gain_pct), `num ${colorClass(b.gain_pct)}`, unk)}${td(fmtPp(b.contribution_pp), `num ${colorClass(b.contribution_pp)}`)}</tr>`;
    }).join("");
    const t = st.totals || {};
    const total = `<tr class="total">${td("<strong>All tickers</strong>")}${td(t.closed_trades == null ? "—" : t.closed_trades, "num")}` +
      `${td(t.open_positions ? `${t.open_positions} open` : "—")}${td(fmtUsd(t.capital_usd, false), "num")}` +
      `${td(fmtUsd(t.pnl_usd), `num ${colorClass(t.pnl_usd)}`)}${td(fmtPct(t.gain_pct), `num ${colorClass(t.gain_pct)}`)}` +
      `${td(fmtPp(t.contribution_pp), `num ${colorClass(t.contribution_pp)}`)}</tr>`;
    return `<div class="table-scroll"><table class="sleeve-table"><thead><tr><th>Ticker</th><th class="num">Closed trades</th><th>Status</th>` +
      `<th class="num">Capital deployed</th><th class="num">Realised P&amp;L</th><th class="num">Gain %</th><th class="num">Contribution</th></tr></thead>` +
      `<tbody>${rows}${total}</tbody></table></div>`;
  }

  function tradeLogHtml(st) {
    if (!st || !st.available) return `<p class="muted">No flex trades recorded yet.</p>`;
    const open = [...(st.open || [])].sort((a, b) => String(b.entry_date || "").localeCompare(String(a.entry_date || "")));
    const closed = [...(st.trades || [])].sort((a, b) =>
      String(b.closed_date || "").localeCompare(String(a.closed_date || "")) || String(b.entry_date || "").localeCompare(String(a.entry_date || "")));
    if (!open.length && !closed.length) return `<p class="muted">No flex trades recorded yet.</p>`;
    const openRows = open.map((o) =>
      `<tr>${td(`<strong>${esc(o.symbol)}</strong>`)}${td(esc(o.entry_date || "—"))}${td("open", "muted")}${td("", "num")}` +
      `${td(fmtPrice(o.entry_price), "num")}${td("—", "num")}${td(fmtQty(o.qty), "num")}` +
      `${td(o.scaled_out ? "Open (partly scaled out)" : "Open")}${td("—", "num")}${td("—", "num")}${td("—", "num")}</tr>`);
    const closedRows = closed.map((t) => {
      const why = isNum(t.pnl_usd) ? "" : `P&L unavailable: ${String(t.pnl_unavailable_reason || "unknown").replace(/_/g, " ")}`;
      return `<tr>${td(`<strong>${esc(t.symbol)}</strong>`)}${td(esc(t.entry_date || "—"))}${td(esc(t.closed_date || "—"))}` +
        `${td(t.holding_days == null ? "—" : esc(t.holding_days), "num")}${td(fmtPrice(t.entry_price), "num")}${td(fmtPrice(t.exit_price), "num")}` +
        `${td(fmtQty(t.qty), "num")}${td(esc(exitLabel(t.exit_reason)))}` +
        `${td(fmtUsd(t.pnl_usd), `num ${colorClass(t.pnl_usd)}`, why)}${td(fmtPct(t.return_pct), `num ${colorClass(t.return_pct)}`, why)}` +
        `${td(fmtPp(t.contribution_pp), `num ${colorClass(t.contribution_pp)}`)}</tr>`;
    });
    return `<div class="table-scroll"><table class="sleeve-table"><thead><tr><th>Ticker</th><th>Entered</th><th>Closed</th><th class="num">Days</th>` +
      `<th class="num">Entry</th><th class="num">Exit</th><th class="num">Qty sold</th><th>Exit reason</th><th class="num">P&amp;L</th>` +
      `<th class="num">Return</th><th class="num">Contribution</th></tr></thead><tbody>${openRows.concat(closedRows).join("")}</tbody></table></div>`;
  }

  // One-line headline for the summary under the panel title.
  function headline(st) {
    if (!st || !st.available) return "";
    const t = st.totals || {};
    if (!isNum(t.pnl_usd) || !isNum(t.capital_usd)) return "";
    return `Realised ${fmtUsd(t.pnl_usd)} on ${fmtUsd(t.capital_usd, false)} deployed = ${fmtPct(t.gain_pct)} (not annualised).`;
  }

  return { esc, fmtUsd, fmtPct, fmtPp, fmtPrice, fmtQty, exitLabel, outcomeOf, indexFor, lineValueNear, buildMarkers, tooltipLines, overlaps,
           pickLabelOffset, byTickerHtml, tradeLogHtml, headline };
});
