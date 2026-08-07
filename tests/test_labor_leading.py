"""Unit tests for the ADP leading-labor sub-signal (2026-08-06 audit O3).

08-05's ADP miss (+44K vs +70K consensus) — the session's most important
forward labor signal — was caught only because the analyzer happened to
parse it out of the forex-news feed. This makes it a deterministic field.

Run: PYTHONPATH=src pytest tests/test_labor_leading.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.handler import _build_labor_leading, _build_labor_signals  # noqa: E402


def _npttl_rows(latest_k: float, deltas_k: list[float]) -> list[dict]:
    """Newest-first NPPTTL rows: vals[0]=latest, vals[i+1] = vals[i] - deltas_k[i]."""
    vals = [latest_k]
    for d in deltas_k:
        vals.append(vals[-1] - d)
    return [{"date": f"2026-{(8 - i):02d}-01", "value": str(v)} for i, v in enumerate(vals)]


def test_available_false_when_series_absent():
    result = _build_labor_leading({})
    assert result["available"] is False


def test_forward_softening_flag_on_a_weak_print():
    """08-05-shaped miss: latest +20K well below the ~+67K trailing 3m pace
    (default forward_softening_gap_k = 20.0) -> flagged."""
    macro = {"NPPTTL": _npttl_rows(1000.0, [20.0, 90.0, 90.0])}
    result = _build_labor_leading(macro)
    assert result["available"] is True
    assert result["delta_1m_k"] == 20.0
    assert result["delta_3m_avg_k"] == 66.7
    assert result["forward_softening_flag"] is True


def test_no_flag_when_print_matches_trailing_pace():
    macro = {"NPPTTL": _npttl_rows(1000.0, [68.0, 70.0, 70.0, 70.0])}
    result = _build_labor_leading(macro)
    assert result["forward_softening_flag"] is False


def test_no_flag_when_insufficient_history_for_3m_avg():
    """Only 2 observations -> delta_1m_k computes, delta_3m_avg_k stays None,
    and the flag can never fire off missing data."""
    macro = {"NPPTTL": _npttl_rows(1000.0, [44.0])}
    result = _build_labor_leading(macro)
    assert result["available"] is True
    assert result["delta_1m_k"] == 44.0
    assert result["delta_3m_avg_k"] is None
    assert result["forward_softening_flag"] is False


def test_labor_signals_carries_leading_without_touching_payems_scorecard():
    """PAYEMS scorecard (the binding read) must be byte-for-byte unaffected by
    the leading ADP signal being present or absent."""
    payems_rows = [{"date": f"2026-{(8 - i):02d}-01", "value": str(150000 - i * 180)} for i in range(24)]
    macro_with_adp = {"PAYEMS": payems_rows, "NPPTTL": _npttl_rows(1000.0, [44.0, 70.0, 70.0])}
    macro_without_adp = {"PAYEMS": payems_rows}
    with_adp = _build_labor_signals(macro_with_adp)
    without_adp = _build_labor_signals(macro_without_adp)
    assert with_adp["payrolls"] == without_adp["payrolls"]
    assert with_adp["scorecard"] == without_adp["scorecard"]
    assert with_adp["leading"]["available"] is True
    assert without_adp["leading"]["available"] is False
