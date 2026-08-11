"""Task B (2026-08-10, catalyst-sleeve-funnel, G2 fix) — news for flex
candidates, not just holdings.

Two things are verified here by actually RUNNING code (not by reading it):
1. `FMPClient.get_stock_news` does not truncate a large symbol list client-side
   before sending the request — mocks the HTTP layer and inspects the params
   the client actually built and sent. (Whether FMP's SERVER silently truncates
   the response for a very long symbol list cannot be verified without a live
   API key — see scripts/probe_fmp_tier.py and the PR body.)
2. `_CATALYST_TONE_KEYWORDS` (the news_tone component's keyword sets, mirroring
   `_SHOCK_KEYWORDS`'s shape) actually classifies sample headlines the way the
   composite scorer expects.

Run: PYTHONPATH=src pytest tests/test_catalyst_news.py
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from collector.catalyst_screen import keyword_hits, news_tone_score  # noqa: E402
from collector.handler import _CATALYST_TONE_KEYWORDS, _STOCK_NEWS_LIMIT  # noqa: E402
from shared.clients.fmp import FMPClient  # noqa: E402


def test_get_stock_news_sends_full_symbol_list_unmodified():
    """Empirical client-side check: build a symbol list far larger than the
    OLD held-only fetch would ever produce and confirm every symbol reaches
    the request params, in order, comma-joined, with no client-side slicing."""
    client = FMPClient(api_key="test-key")
    symbols = [f"SYM{i}" for i in range(60)]  # larger than held+flex ever was

    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status.return_value = None

    with patch.object(client.session, "get", return_value=mock_response) as mock_get:
        client.get_stock_news(symbols, limit=_STOCK_NEWS_LIMIT)

    assert mock_get.called
    _, kwargs = mock_get.call_args
    sent_params = kwargs["params"]
    assert sent_params["symbols"] == ",".join(symbols)
    assert sent_params["limit"] == _STOCK_NEWS_LIMIT
    # Every symbol individually present — no silent client-side cap.
    for sym in symbols:
        assert sym in sent_params["symbols"].split(",")


def test_get_stock_news_empty_list_short_circuits_without_a_call():
    client = FMPClient(api_key="test-key")
    with patch.object(client.session, "get") as mock_get:
        result = client.get_stock_news([], limit=30)
    assert result == []
    assert not mock_get.called


def test_catalyst_tone_keywords_classify_sample_headlines():
    positive_item = [{"headline": "Company beats estimates and raises guidance for FY27"}]
    negative_item = [{"headline": "Firm cuts guidance after major lawsuit filed"}]
    neutral_item = [{"headline": "Company appoints new board member"}]

    pos_hits = keyword_hits(positive_item, _CATALYST_TONE_KEYWORDS)
    neg_hits = keyword_hits(negative_item, _CATALYST_TONE_KEYWORDS)
    neu_hits = keyword_hits(neutral_item, _CATALYST_TONE_KEYWORDS)

    assert pos_hits["positive"] == 1 and pos_hits["negative"] == 0
    assert neg_hits["negative"] == 1 and neg_hits["positive"] == 0
    assert neu_hits == {"positive": 0, "negative": 0}

    assert news_tone_score(True, pos_hits["positive"], pos_hits["negative"]) == 1.0
    assert news_tone_score(True, neg_hits["positive"], neg_hits["negative"]) == 0.0
    assert news_tone_score(True, neu_hits["positive"], neu_hits["negative"]) == 0.5


def test_catalyst_tone_keywords_have_no_overlap():
    # A headline must never hit both categories on the same keyword.
    pos = set(_CATALYST_TONE_KEYWORDS["positive"])
    neg = set(_CATALYST_TONE_KEYWORDS["negative"])
    assert pos.isdisjoint(neg)
