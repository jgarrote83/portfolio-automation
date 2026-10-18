"""Exploratory bonds-vs-SPY analysis (analysis/bonds_spy): return definitions, skip counting, statistics, lag alignment, the verifier.
Synthetic inputs only; no network, no cache."""
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.bonds_spy import analyse, data, returns, stats, verify  # noqa: E402

D1, D2, D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)


# --- return definitions ---------------------------------------------------------------------------------------------------

def test_day_returns_definitions():
    r = returns.day_returns(open_d=102.0, close_d=104.0, close_p=100.0, open10=103.0)
    assert r["gap"] == 102.0 / 100.0 - 1.0
    assert r["morning"] == 103.0 / 102.0 - 1.0
    assert r["rest"] == 104.0 / 103.0 - 1.0
    assert r["full"] == 104.0 / 100.0 - 1.0


def test_returns_compose_to_the_full_day():
    r = returns.day_returns(97.3, 101.9, 99.1, 98.4)
    assert math.isclose((1 + r["gap"]) * (1 + r["morning"]) * (1 + r["rest"]), 1 + r["full"], rel_tol=1e-12)


def _inputs():
    cal = [date(2023, 12, 29), D1, D2, D3]
    daily = {s: {d: (100.0 + i, 101.0 + i) for i, d in enumerate(cal)} for s in returns.SYMBOLS}
    open10 = {s: {d: 100.5 + i for i, d in enumerate(cal)} for s in returns.SYMBOLS}
    return cal, daily, open10


def test_day_table_uses_the_prior_trading_day_and_names_columns():
    cal, daily, open10 = _inputs()
    t, sk = returns.build_day_table(cal, daily, open10, D1, D3)
    assert list(t.index) == [D1, D2, D3] and sk["used"] == 3 and sk["days_in_window"] == 3
    # D1's prior trading day is 2023-12-29 (close 101.0); D1 open = 101.0
    assert t.loc[D1, "SPY_gap"] == 101.0 / 101.0 - 1.0
    assert set(t.columns) == {f"{s}_{m}" for s in returns.SYMBOLS for m in returns.MEASURES}


def test_missing_bars_skip_the_day_and_are_counted_never_filled():
    cal, daily, open10 = _inputs()
    del open10["TLT"][D2]                        # no 10:00 bar
    del daily["IEF"][D3]                         # no daily bar on D
    t, sk = returns.build_day_table(cal, daily, open10, D1, D3)
    assert list(t.index) == [D1]
    assert sk["skipped_days"] == {D2: ["missing_1000_bar:TLT"], D3: ["missing_daily_bar_D:IEF"]}
    assert sk["by_reason"] == {"missing_1000_bar:TLT": 1, "missing_daily_bar_D:IEF": 1}


def test_missing_prior_daily_bar_is_its_own_reason():
    cal, daily, open10 = _inputs()
    del daily["SPY"][D1]
    _, sk = returns.build_day_table(cal, daily, open10, D2, D2)
    assert sk["skipped_days"] == {D2: ["missing_daily_bar_prior:SPY"]}


def test_first_trading_day_of_month():
    cal = [date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 2), date(2024, 3, 1)]
    assert returns.first_trading_day_of_month(cal) == {date(2024, 2, 1), date(2024, 3, 1)}


# --- minute maps ------------------------------------------------------------------------------------------------------------

def test_minute_maps_grid_and_10am_open_with_gaps_left_as_nan():
    ts = pd.to_datetime(["2024-01-02 09:30", "2024-01-02 09:31", "2024-01-02 10:00", "2024-01-02 11:01"]).tz_localize("America/New_York")
    m = pd.DataFrame({"symbol": "SPY", "ts": ts, "open": [1.0, 2.0, 3.0, 4.0], "close": [1.5, 2.5, 3.5, 4.5]})
    closes, open10 = data.minute_maps(m)
    arr = closes["SPY"][D1]
    assert len(arr) == 91 and arr[0] == 1.5 and arr[1] == 2.5 and arr[30] == 3.5 and np.isnan(arr[2])
    assert open10["SPY"][D1] == 3.0                  # the 11:01 bar is off the grid and ignored


def test_load_frames_refuses_2026(monkeypatch):
    monkeypatch.setattr(data, "LAST_DAY", date(2026, 1, 2))
    with pytest.raises(ValueError, match="2026"):
        data.load_frames(layer=object())


# --- correlations ---------------------------------------------------------------------------------------------------------

def test_spearman_handles_ties_with_average_ranks():
    assert list(stats.average_ranks([3, 1, 3, 2])) == [3.5, 1.0, 3.5, 2.0]
    x, y = [1, 2, 2, 3, 4], [10, 20, 20, 30, 40]
    assert math.isclose(stats.spearman(x, y), 1.0)


def test_pearson_matches_numpy_and_drops_nans_pairwise():
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=50), rng.normal(size=50)
    assert math.isclose(stats.pearson(x, y), float(np.corrcoef(x, y)[0, 1]), rel_tol=1e-12)
    x2 = np.append(x, np.nan)
    y2 = np.append(y, 5.0)
    assert stats.corr_row(x2, y2)["n"] == 50


def test_quarter_label_grouping():
    assert [stats.quarter_label(date(2024, m, 1)) for m in (1, 3, 4, 7, 12)] == ["2024Q1", "2024Q1", "2024Q2", "2024Q3", "2024Q4"]


# --- cross-correlation lag alignment --------------------------------------------------------------------------------------

def _leader_follower(shift: int, days: int = 30, seed: int = 3):
    """Bond minute returns are noise; SPY's return at minute t+shift equals the bond's return at t (bond leads by `shift`)."""
    rng = np.random.default_rng(seed)
    bonds, spys = [], []
    for _ in range(days):
        b = rng.normal(0, 1e-3, 90)
        s = rng.normal(0, 1e-4, 90)
        if shift >= 0:
            s[shift:] += b[: 90 - shift]
        else:
            s[: 90 + shift] += b[-shift:]
        bonds.append(100 * np.cumprod(np.r_[1.0, 1 + b]))
        spys.append(100 * np.cumprod(np.r_[1.0, 1 + s]))
    return bonds, spys


def test_bond_leading_by_three_minutes_peaks_at_plus_three():
    b, s = _leader_follower(3)
    cc = stats.cross_correlation(b, s, boot=50)
    best = max(cc, key=lambda r: abs(r["corr"]))
    assert best["lag"] == 3 and best["corr"] > 0.9


def test_spy_leading_peaks_at_a_negative_lag():
    b, s = _leader_follower(-2)
    cc = stats.cross_correlation(b, s, boot=50)
    assert max(cc, key=lambda r: abs(r["corr"]))["lag"] == -2


def test_pairs_never_cross_a_day_boundary():
    # 2 days of 4 closes -> 3 returns each; at lag +1 there are exactly 2 pairs per day (4), never a day-1 -> day-2 pair
    b = [np.array([1.0, 1.1, 1.2, 1.3]), np.array([2.0, 2.1, 2.2, 2.3])]
    cc = {r["lag"]: r for r in stats.cross_correlation(b, b, lags=[0, 1, 2, 3], boot=5)}
    assert cc[0]["n"] == 6 and cc[1]["n"] == 4 and cc[2]["n"] == 2 and cc[3]["n"] == 0


def test_missing_minute_is_dropped_not_filled():
    p = np.array([1.0, 1.01, np.nan, 1.02, 1.03])
    r = stats.minute_returns(p)
    assert np.isnan(r[1]) and np.isnan(r[2]) and np.isfinite(r[0]) and np.isfinite(r[3])


# --- OLS with HAC ---------------------------------------------------------------------------------------------------------

def test_ols_hac_matches_a_direct_matrix_computation():
    rng = np.random.default_rng(7)
    n = 200
    X = np.column_stack([np.ones(n), rng.normal(size=n), rng.normal(size=n)])
    y = X @ np.array([0.1, 0.5, -0.2]) + rng.normal(size=n)
    fit = stats.ols_hac(y, X, ["c", "a", "b"], lag=3)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    u = y - X @ beta
    S = np.zeros((3, 3))
    for t in range(n):
        for s in range(n):
            if abs(t - s) <= 3:
                S += (1 - abs(t - s) / 4) * np.outer(X[t] * u[t], X[s] * u[s])
    Xi = np.linalg.inv(X.T @ X)
    V = Xi @ S @ Xi
    assert np.allclose(fit["beta"], beta) and np.allclose(fit["cov"], V)
    assert np.allclose(fit["se"], np.sqrt(np.diag(V)))


def test_nw_lag_rule():
    assert stats.nw_lag(502) == 5 and stats.nw_lag(250) == 4 and stats.nw_lag(100) == 4


def test_wald_one_restriction_equals_the_squared_t():
    rng = np.random.default_rng(2)
    X = np.column_stack([np.ones(100), rng.normal(size=100)])
    y = 0.3 * X[:, 1] + rng.normal(size=100)
    fit = stats.ols_hac(y, X, ["c", "x"])
    w = stats.wald(fit, [1])
    assert math.isclose(w["W"], fit["t"][1] ** 2, rel_tol=1e-9) and math.isclose(w["p"], fit["p"][1], rel_tol=1e-9)


def test_wald_two_restrictions_chi2_p():
    fit = {"beta": np.array([0.0, 1.0, 1.0]), "cov": np.eye(3)}
    w = stats.wald(fit, [1, 2])
    assert w["W"] == 2.0 and math.isclose(w["p"], math.exp(-1.0))


# --- Q4 selection ---------------------------------------------------------------------------------------------------------

def test_top_ten_percent_by_absolute_gap_with_date_tiebreak():
    idx = [date(2024, 1, d) for d in range(2, 22)]                     # 20 days -> k = 2
    gaps = [0.001] * 20
    gaps[5], gaps[9], gaps[12] = -0.05, 0.05, 0.03                     # a tie in |gap| at 0.05
    t = pd.DataFrame({"TLT_gap": gaps}, index=idx)
    assert analyse.top_abs_days(t) == [idx[5], idx[9]]


# --- verifier -------------------------------------------------------------------------------------------------------------

def _write_cache(root, sym, daily_rows, minute_rows):
    for tf, rows in (("1Day", daily_rows), ("1Min", minute_rows)):
        df = pd.DataFrame(rows, columns=["ts", "open", "close"])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
        for (y, m), g in df.groupby([df["ts"].dt.tz_convert("America/New_York").dt.year, df["ts"].dt.tz_convert("America/New_York").dt.month]):
            p = root / tf / sym / f"{y:04d}-{m:02d}.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            g.reset_index(drop=True).to_parquet(p)


def test_verifier_matches_and_catches_a_tampered_value(tmp_path, monkeypatch):
    cache = tmp_path / "sip"
    for i, sym in enumerate(verify.SYMS):
        k = i + 1.0
        _write_cache(cache, sym, [("2024-01-02 00:00", 100 * k, 101 * k), ("2024-01-03 00:00", 102 * k, 103 * k)],
                     [("2024-01-03 10:00", 102.5 * k, 102.6 * k)])
    # the verifier lists the trading calendar from SPY month files 2023-12 .. 2025-12; give it empty months for the rest
    empty = pd.read_parquet(cache / "1Day" / "SPY" / "2024-01.parquet").iloc[0:0]
    for ym in pd.period_range("2023-12", "2025-12", freq="M"):
        p = cache / "1Day" / "SPY" / f"{ym.year:04d}-{ym.month:02d}.parquet"
        if not p.exists():
            empty.to_parquet(p)
    row = {"date": "2024-01-03"}
    for i, sym in enumerate(verify.SYMS):
        k = i + 1.0
        row.update({f"{sym}_gap": 102 * k / (101 * k) - 1, f"{sym}_morning": 102.5 * k / (102 * k) - 1,
                    f"{sym}_rest": 103 * k / (102.5 * k) - 1, f"{sym}_full": 103 * k / (101 * k) - 1})
    csv = tmp_path / "t.csv"
    pd.DataFrame([row]).to_csv(csv, index=False)
    ok = verify.check(csv, n=1, cache=cache)
    assert ok["pass"] and ok["matched"] == 12
    row["IEF_rest"] += 1e-12
    pd.DataFrame([row]).to_csv(csv, index=False)
    bad = verify.check(csv, n=1, cache=cache)
    assert not bad["pass"] and [(m["symbol"], m["measure"]) for m in bad["mismatches"]] == [("IEF", "rest")]
