"""The code cannot drift from the pre-registration unnoticed (docs/specs/ORB_Phase2_Preregistration.md)."""
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))   # the repo root, for `backtest`

import pytest  # noqa: E402
from backtest import reports  # noqa: E402
from backtest.engine import ALL_VARIANTS, PRIMARY, SENSITIVITIES  # noqa: E402
from orb.config import SPEC_VERSION, OrbConfig  # noqa: E402

PREREG = Path(__file__).resolve().parents[1] / "docs" / "specs" / "ORB_Phase2_Preregistration.md"


@pytest.fixture(scope="module")
def doc() -> str:
    return PREREG.read_text(encoding="utf-8")


def _json_block(doc: str) -> dict:
    m = re.search(r"```json\s*(\{.*?\})\s*```", doc, re.S)
    assert m, "the pre-registration must carry its machine-readable parameter block"
    return json.loads(m.group(1))


def test_default_config_equals_the_preregistered_parameter_block(doc):
    block = _json_block(doc)
    actual = dataclasses.asdict(OrbConfig())
    assert set(block) == set(actual), "config fields and the pre-registered block must match exactly"
    for k, v in block.items():
        assert actual[k] == v, k
        assert isinstance(actual[k], bool) == isinstance(v, bool), k       # True is not 1
        assert isinstance(actual[k], str) == isinstance(v, str), k
    assert block["spec_version"] == SPEC_VERSION == "orb-1.0"


def test_the_primary_table_values_are_in_the_document_word_for_word(doc):
    for phrase in (
        "Open > $5, 14-day average volume ≥ 1M, 14-day ATR > $0.50 (averages from prior days only), Core roster excluded, **ETFs excluded**",
        "**Simple mean of the prior 14 true ranges**, the same definition Phase 1 uses, for both the filter and the stop",
        "Relative Volume (9:30–9:35 volume ÷ prior-14-day average of the same window) ≥ 100%, top 20",
        "First 5-min bar up → long only; down → short only; doji → no trade. **Shorts allowed**",
        "Stop at the 5-min high (long) or low (short), live from the 9:35 bar through 15:45; fill at the level, or at the bar's open if it gaps through",
        "10% of ATR from the **trigger price** (the live rule); fills at the level, or at the bar's open on a gap; entry and stop in the same minute bar ⇒ stop hit",
        "Close of the 15:59 bar if not stopped. Half-days use the calendar close",
        "Sleeve P&L (realized + marked) ≤ −3% of sleeve capital ⇒ cancel entries and flatten at that bar's close (mirrors live)",
        "Sleeve capital **$25,000** (25% of a $100k account), fixed and non-compounding. Shares = floor(min(1% of sleeve ÷ stop distance, sleeve ÷ 20 ÷ price)). Integer shares; never levered",
        "$0.0035/share commission plus slippage of **2¢ per share per side** for the go/no-go",
        "Daily sleeve return = day P&L ÷ $25,000; no-trade days count as 0. Sharpe = mean ÷ std × √252, risk-free 0",
        "GO only if, on the primary variant, net Sharpe ≥ **1.0** over 2024–2025 **and** net P&L is positive in **both** 2024 and 2025. Anything else is NO-GO.",
    ):
        assert phrase in doc, phrase


def test_the_go_no_go_constants_are_the_preregistered_ones():
    assert reports.GO_MIN_SHARPE == 1.0 and reports.GO_YEARS == (2024, 2025)
    assert reports.SLEEVE_SHARE_OF_EQUITY == 0.25


def test_the_variant_set_is_exactly_the_reported_sensitivities():
    # slippage 0/1/5c, long-only, ETFs included -- nothing else, each changing exactly one thing
    assert PRIMARY.overrides == ()
    assert [v.name for v in SENSITIVITIES] == ["slippage_0c", "slippage_1c", "slippage_5c",
                                               "long_only", "etfs_included"]
    assert all(len(v.overrides) == 1 for v in SENSITIVITIES)
    assert dict(SENSITIVITIES[0].overrides) == {"slippage_cents_per_side": 0.0}
    assert dict(SENSITIVITIES[1].overrides) == {"slippage_cents_per_side": 1.0}
    assert dict(SENSITIVITIES[2].overrides) == {"slippage_cents_per_side": 5.0}
    assert dict(SENSITIVITIES[3].overrides) == {"allow_shorts": False}
    assert dict(SENSITIVITIES[4].overrides) == {"exclude_etfs": False}
    assert ALL_VARIANTS[0] is PRIMARY


def test_the_document_records_the_etf_download(doc):
    assert "2026-10-04" in doc and "nasdaqlisted.txt" in doc and "otherlisted.txt" in doc
    assert len(re.findall(r"`[0-9a-f]{64}`", doc)) == 2          # one SHA-256 per file
