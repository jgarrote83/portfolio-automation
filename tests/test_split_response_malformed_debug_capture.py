"""A0 (2026-08-14 audit) — malformed trades-JSON must persist a debug-raw copy,
mirroring the pre-existing "marker missing" branch.

Discovered while investigating the 2026-08-12 six-orders-to-one-order
incident: `_split_response`'s "marker missing" branch already calls
`write_debug_raw` for forensics, but the sibling "trades block malformed"
branch (a JSON parse failure / truncation) silently defaulted to an empty
trades list with only a log line. App Insights retention had already expired
by the time this incident was investigated, so there was no way to tell
"the model never emitted the trades" from "the model emitted them and the
JSON got truncated." This closes that blind spot.

Run: PYTHONPATH=src pytest tests/test_split_response_malformed_debug_capture.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analyzer.handler import _split_response  # noqa: E402

_MARKER = "===TRADES_JSON==="


def test_malformed_json_persists_debug_raw():
    raw = "# Report\n\nsome text\n" + _MARKER + "\n{not valid json at all"
    with patch("shared.storage.write_debug_raw") as mock_write:
        _, trades_obj = _split_response(raw, "2026-08-12")
    assert trades_obj == {
        "trades": [],
        "generated_at": trades_obj["generated_at"],
        "date": "2026-08-12",
    }
    mock_write.assert_called_once_with("2026-08-12", raw)


def test_trades_key_missing_persists_debug_raw():
    raw = "# Report\n\nsome text\n" + _MARKER + "\n{\"not_trades\": []}"
    with patch("shared.storage.write_debug_raw") as mock_write:
        _, trades_obj = _split_response(raw, "2026-08-12")
    assert trades_obj["trades"] == []
    mock_write.assert_called_once_with("2026-08-12", raw)


def test_debug_write_failure_is_non_fatal():
    raw = "# Report\n\nsome text\n" + _MARKER + "\n{not valid json"
    with patch("shared.storage.write_debug_raw", side_effect=RuntimeError("blob down")):
        _, trades_obj = _split_response(raw, "2026-08-12")
    assert trades_obj["trades"] == []
