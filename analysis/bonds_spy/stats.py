"""Statistics for the bonds-vs-SPY analysis (pure numpy; no I/O).

* Pearson and Spearman correlation (average ranks for ties) with n.
* The pooled minute cross-correlation at lags -K..+K with two bands: the usual +/-1.96/sqrt(n) (optimistic, minute data is autocorrelated)
  and a day-block bootstrap (whole days resampled with replacement), which respects the within-day dependence.
* OLS with Newey-West (Bartlett kernel) HAC standard errors and a joint Wald test.

Lag convention: lag k pairs a bond return at minute t with the SPY return at minute t + k, so a POSITIVE lag means the bond moved FIRST.
"""
from __future__ import annotations

import math

import numpy as np


# ------------------------------------------------------------------------------------------------ correlations
def _clean(x, y):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    m = np.isfinite(x) & np.isfinite(y)
    return x[m], y[m]


def pearson(x, y) -> float | None:
    x, y = _clean(x, y)
    if len(x) < 3:
        return None
    sx, sy = x - x.mean(), y - y.mean()
    d = math.sqrt(float((sx * sx).sum()) * float((sy * sy).sum()))
    return float((sx * sy).sum() / d) if d > 0 else None


def average_ranks(x) -> np.ndarray:
    """1-based ranks, ties share the average rank."""
    x = np.asarray(x, dtype=float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(x, y) -> float | None:
    x, y = _clean(x, y)
    if len(x) < 3:
        return None
    return pearson(average_ranks(x), average_ranks(y))


def corr_row(x, y) -> dict:
    x, y = _clean(x, y)
    return {"n": int(len(x)), "pearson": pearson(x, y), "spearman": spearman(x, y)}


def quarter_label(d) -> str:
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


# ----------------------------------------------------------------------------------- minute cross-correlation
def minute_returns(prices: np.ndarray) -> np.ndarray:
    """Close-to-close returns of consecutive minute bars; NaN wherever either bar is missing (never filled)."""
    p = np.asarray(prices, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return p[1:] / p[:-1] - 1.0


def _pair_stats(x: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """(n, sum x, sum y, sum xx, sum yy, sum xy) over the finite pairs (x[t], y[t+k]) of ONE day."""
    m = len(x)
    if abs(k) >= m:
        return np.zeros(6)
    xs, ys = (x[: m - k], y[k:]) if k >= 0 else (x[-k:], y[: m + k])
    ok = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[ok], ys[ok]
    return np.array([len(xs), xs.sum(), ys.sum(), (xs * xs).sum(), (ys * ys).sum(), (xs * ys).sum()])


def _corr_from_stats(s: np.ndarray) -> np.ndarray:
    """Correlation from (..., 6) pooled sums; NaN when undefined."""
    n, sx, sy, sxx, syy, sxy = (s[..., i] for i in range(6))
    with np.errstate(invalid="ignore", divide="ignore"):
        num = n * sxy - sx * sy
        den = np.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy))
        return np.where((n > 2) & (den > 0), num / den, np.nan)


def cross_correlation(bond_days: list[np.ndarray], spy_days: list[np.ndarray], lags=range(-10, 11), boot: int = 2000, seed: int = 11) -> list[dict]:
    """Pooled cross-correlation of minute returns at each lag. `bond_days[i]` / `spy_days[i]` are the minute CLOSE prices of day i on a
    common grid (NaN = no bar). Pairs never cross a day boundary. Returns, per lag: n (pairs), corr, band_iid (+/-1.96/sqrt(n)),
    boot_lo / boot_hi (2.5th and 97.5th percentile of the correlation over `boot` resamples of whole days)."""
    lags = list(lags)
    D = len(bond_days)
    if D == 0 or D != len(spy_days):
        raise ValueError("need the same non-zero number of days for both series")
    br = [minute_returns(b) for b in bond_days]
    sr = [minute_returns(s) for s in spy_days]
    S = np.array([[_pair_stats(br[d], sr[d], k) for k in lags] for d in range(D)])        # (D, L, 6)
    pooled = S.sum(axis=0)                                                                 # (L, 6)
    corr = _corr_from_stats(pooled)
    rng = np.random.default_rng(seed)
    W = rng.multinomial(D, np.full(D, 1.0 / D), size=boot).astype(float)                    # (B, D) day counts
    totals = np.einsum("bd,dls->bls", W, S)
    bc = _corr_from_stats(totals)                                                          # (B, L)
    lo, hi = np.nanpercentile(bc, 2.5, axis=0), np.nanpercentile(bc, 97.5, axis=0)
    out = []
    for i, k in enumerate(lags):
        n = int(pooled[i, 0])
        out.append({"lag": k, "n": n, "corr": None if np.isnan(corr[i]) else float(corr[i]),
                    "band_iid": 1.96 / math.sqrt(n) if n > 0 else None, "boot_lo": float(lo[i]), "boot_hi": float(hi[i])})
    return out


# -------------------------------------------------------------------------------------------- OLS with HAC
def nw_lag(n: int) -> int:
    """The Newey-West (1994) rule of thumb for the Bartlett truncation lag: floor(4 (n/100)^(2/9))."""
    return int(4 * (n / 100.0) ** (2.0 / 9.0))


def normal_two_sided_p(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def ols_hac(y, X, names: list[str], lag: int | None = None) -> dict:
    """OLS of y on X (X already contains the constant column) with Newey-West HAC covariance, in time order.
    Returns coefficients, HAC standard errors, t, two-sided normal p, R^2, adjusted R^2, n, the lag used and the covariance matrix.
    No small-sample correction is applied (stated in the report)."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape
    if n <= k + 2:
        raise ValueError(f"too few observations ({n}) for {k} parameters")
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    L = nw_lag(n) if lag is None else lag
    xu = X * u[:, None]                                    # row t = x_t * u_t
    S = xu.T @ xu
    for j in range(1, L + 1):
        w = 1.0 - j / (L + 1.0)
        G = xu[j:].T @ xu[:-j]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.diag(V))
    t = beta / se
    ss_res, ss_tot = float((u * u).sum()), float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else None
    adj = 1.0 - (1.0 - r2) * (n - 1) / (n - k) if r2 is not None else None
    return {"names": names, "beta": beta, "se": se, "t": t, "p": np.array([normal_two_sided_p(v) for v in t]), "r2": r2, "adj_r2": adj,
            "n": n, "k": k, "lag": L, "cov": V}


def wald(fit: dict, idx: list[int]) -> dict:
    """Joint HAC Wald test that the coefficients at positions `idx` are all zero: W = b' V^-1 b ~ chi-square(len(idx))."""
    b = fit["beta"][idx]
    V = fit["cov"][np.ix_(idx, idx)]
    W = float(b @ np.linalg.solve(V, b))
    df = len(idx)
    if df == 1:
        p = math.erfc(math.sqrt(W / 2.0))
    elif df == 2:
        p = math.exp(-W / 2.0)
    else:
        raise ValueError("wald is implemented for 1 or 2 restrictions")
    return {"W": W, "df": df, "p": p}
