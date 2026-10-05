# backtest/ — offline ORB backtest package (never deployed)

Phase 1 of `docs/specs/ORB_Engine_v1.0.md`: the data layer. `deploy-code.yml` packages `src/` only;
nothing in `src/`, `infra/` or `web/` imports this package.

## `get_bars` — the one door (repo rule 6)

```python
from backtest.data import get_bars
df = get_bars(["SPY", "QQQ"], "2024-01-02", "2024-01-31", timeframe="1Day", feed="sip")
# columns: symbol, ts (US/Eastern), open, high, low, close, volume
opening = get_bars(symbols, "2024-03-08", "2024-03-11", "5Min", "iex", window=("09:30", "09:35"))
```

* Serves bars from `data/cache/{feed}/{timeframe}/{symbol}/{YYYY-MM}.parquet` and calls Alpaca only
  for what is missing. `data/cache/manifest.sqlite` records every (feed, timeframe, adjustment,
  window, symbol, day) fetched — **including days with no bars**, so they are never requested twice.
* Raw, unadjusted prices (`adjustment=raw`, part of the manifest key).
* **Holdout guard:** any date on/after 2026-01-01 raises `HoldoutError` unless `allow_holdout=True`
  (needs Jorge's approval; nothing in Phase 1 passes it).
* **Freshness:** today and the last 15 minutes are never cached (fetched into memory only).
* **Rate limit:** at most 180 requests/minute (token bucket + a strict rolling-window cap); 429/5xx
  retried with exponential backoff (honours `Retry-After`).
* **Offline:** `get_bars(..., offline=True)` / `--offline` raises `CacheMissError` on any miss.
* Writes are atomic (temp file → rename). Trading days come from SPY daily bars (no trading API).
* Read-only: only `GET https://data.alpaca.markets/v2/stocks/bars` and `GET /v2/assets` (active and
  inactive US equities; that endpoint is served by the Trading API host, paper) are ever called.

## Keys

Create a gitignored `.env` at the repo root (`backtest/env.example` is the template) with
`ALPACA_API_KEY` and `ALPACA_API_SECRET` for the **paper** account — type them in yourself, never
paste them into chat. The loader refuses to read `.env` unless `git check-ignore` confirms it is
ignored, and keys never appear in logs, `repr()` or error messages. Offline mode needs no keys.

## Commands (from the repo root, `PYTHONPATH=src` where the Core roster is needed)

```powershell
pip install -r backtest/requirements.txt
python -m backtest.data.cli bars --symbols SPY --start 2024-01-02 --end 2024-01-31 --timeframe 1Day
python -m backtest.data.cli bars --symbols SPY --start 2024-01-02 --end 2024-01-31 --timeframe 1Day --offline
python -m backtest.data.cli assets
python -m backtest.data.cli stats

# the IEX-vs-SIP report (the $99/month question) — hours, not minutes, on a cold cache:
$env:PYTHONPATH = "src"
python -m backtest.iex_vs_sip --start 2024-01-02 --end 2025-12-31 `
    --csv reports/orb/iex_vs_sip_2024_2025.csv --summary .review/phase1-iex-vs-sip.md
# smoke test first (NOT the report):  --limit-symbols 300
```

Tests need no keys and no network: `PYTHONPATH=src pytest -q tests/test_backtest_*.py`.

## Phase 2 — the ORB backtest engine

`src/orb/` holds the strategy as **pure Python** (`config.py`, `universe.py`, `signals.py`, `sizing.py`:
stdlib + `shared.*` only, no I/O, not registered in `function_app.py`); `backtest/` imports them, so the
logic that is tested is the logic that will trade (Phase 4). The rules, thresholds and the mechanical
go/no-go were written down **before any code or result**: `docs/specs/ORB_Phase2_Preregistration.md`
(locked at its first commit; a change needs a new pre-registration and a fresh test period).

| Module | What it does |
| --- | --- |
| `engine.py` | `simulate_day` (minute-by-minute, one variant) and `run_backtest` (all variants over the window). Refuses any date on/after 2026-01-01; **no override flag exists**. |
| `selection.py` | universe → Relative Volume → top 20 → direction, from cached SIP bars, for the ETFs-excluded and ETFs-included universes |
| `slippage.py`, `sessions.py`, `etf.py` | cost model; NYSE early closes (cross-checked against SPY's own bars on a real run); the Nasdaq Trader ETF list |
| `reports.py`, `provenance.py` | metrics, the go/no-go verdict, run output; code commit and pre-registration hash for every run header |
| `cli.py` | `python -m backtest.cli run` |

```powershell
# one-time, read-only: Nasdaq Trader's ETF flags into data/reference/ (gitignored)
New-Item -ItemType Directory -Force data/reference | Out-Null
foreach ($f in "nasdaqlisted","otherlisted") { Invoke-WebRequest "https://www.nasdaqtrader.com/dynamic/SymDir/$f.txt" -OutFile "data/reference/$f.txt" }

$env:PYTHONPATH = "src"
python -m backtest.cli run --review-file .review/phase2-results.md        # the pre-registered run (needs .env; hours on a cold cache)
python -m backtest.cli run --start 2024-01-02 --end 2024-03-29 --limit-symbols 300   # smoke test: labelled NOT A PRE-REGISTERED RUN
```

Only the exact pre-registered invocation (window 2024-01-02..2025-12-31, no `--limit-symbols`, default
config, all variants, pre-registration unchanged) produces a go/no-go verdict. Outputs go to
`reports/orb/phase2/<run-id>/` (gitignored): `header.json` (config, code commit, pre-registration
hash, data statistics), `summary.md`, and per variant `metrics.json` / `trades.csv` / `daily.csv`.
The CLI reports; it does not tune.
