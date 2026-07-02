"""FoundryClient streaming — SSE accumulation, retries, and failure modes.

The client MUST stream (see foundry.py module docstring): Azure's ~4-min
outbound idle timeout kills non-streaming calls mid-generation. These tests
pin the SSE parsing contract so a refactor can't silently regress it.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from shared.clients.foundry import FoundryClient


def _sse(events: list[dict]) -> list[str]:
    """Render Anthropic Messages events as the lines iter_lines() yields."""
    lines: list[str] = []
    for ev in events:
        lines.append(f"event: {ev['type']}")
        lines.append(f"data: {json.dumps(ev)}")
        lines.append("")
    return lines


def _happy_events(text_parts: list[str], stop_reason: str = "end_turn") -> list[dict]:
    return [
        {"type": "message_start", "message": {"usage": {"input_tokens": 72000}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        *[
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": t}}
            for t in text_parts
        ],
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason},
         "usage": {"output_tokens": 13704}},
        {"type": "message_stop"},
    ]


class _FakeResponse:
    def __init__(self, status_code: int = 200, lines: list[str] | None = None,
                 text: str = ""):
        self.status_code = status_code
        self._lines = lines or []
        self.text = text

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _client() -> FoundryClient:
    return FoundryClient(api_key="k", endpoint="https://example.test/v1/messages")


def test_streams_and_concatenates_text_deltas():
    lines = _sse(_happy_events(["## Report\n", "Body ", "text."]))
    with mock.patch("requests.post", return_value=_FakeResponse(lines=lines)) as post:
        out = _client().complete("sys", "user")
    assert out == "## Report\nBody text."
    body = post.call_args.kwargs["json"]
    assert body["stream"] is True
    assert post.call_args.kwargs["stream"] is True
    # (connect, read) tuple — read is the inter-chunk gap, must stay < ~4 min
    connect, read = post.call_args.kwargs["timeout"]
    assert read < 240


def test_ignores_pings_and_non_text_deltas():
    events = _happy_events(["ok"])
    events.insert(1, {"type": "ping"})
    events.insert(2, {"type": "content_block_delta", "index": 0,
                      "delta": {"type": "thinking_delta", "thinking": "hmm"}})
    with mock.patch("requests.post", return_value=_FakeResponse(lines=_sse(events))):
        assert _client().complete("sys", "user") == "ok"


def test_truncated_stream_without_message_stop_raises_and_retries():
    events = _happy_events(["partial "])[:-2]  # cut before message_delta/stop
    resp = _FakeResponse(lines=_sse(events))
    with mock.patch("requests.post", return_value=resp), \
         mock.patch("time.sleep"):
        with pytest.raises(RuntimeError, match="without message_stop"):
            _client().complete("sys", "user", retries=1)


def test_error_event_mid_stream_raises():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {"type": "error",
         "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]
    with mock.patch("requests.post", return_value=_FakeResponse(lines=_sse(events))), \
         mock.patch("time.sleep"):
        with pytest.raises(RuntimeError, match="overloaded_error"):
            _client().complete("sys", "user", retries=0)


def test_empty_stream_with_message_stop_raises():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {"type": "message_stop"},
    ]
    with mock.patch("requests.post", return_value=_FakeResponse(lines=_sse(events))), \
         mock.patch("time.sleep"):
        with pytest.raises(RuntimeError, match="empty response"):
            _client().complete("sys", "user", retries=0)


def test_transient_429_then_success_retries():
    ok = _FakeResponse(lines=_sse(_happy_events(["recovered"])))
    throttled = _FakeResponse(status_code=429, text="slow down")
    with mock.patch("requests.post", side_effect=[throttled, ok]), \
         mock.patch("time.sleep"):
        assert _client().complete("sys", "user", retries=1) == "recovered"


def test_non_200_raises_with_body():
    bad = _FakeResponse(status_code=401, text="bad key")
    with mock.patch("requests.post", return_value=bad):
        with pytest.raises(RuntimeError, match="Foundry 401: bad key"):
            _client().complete("sys", "user", retries=0)


def test_not_ready_raises():
    with pytest.raises(RuntimeError, match="not configured"):
        FoundryClient(api_key=None, endpoint="").complete("s", "u")
