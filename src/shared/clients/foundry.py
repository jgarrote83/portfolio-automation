"""Azure AI Foundry — Claude via Anthropic Messages API surface.

Claude models hosted on Azure AI Foundry are NOT reachable via the Azure OpenAI
`/openai/deployments/*/chat/completions` path (returns 404 api_not_supported).
They must be called through the Anthropic-compatible endpoint:

    POST {FOUNDRY_ENDPOINT}
    headers:
        x-api-key: <FoundryApiKey>
        anthropic-version: 2023-06-01
        content-type: application/json
    body: { model, max_tokens, temperature, system, messages, stream: true }

The verified endpoint shape is:
    https://<resource>.services.ai.azure.com/anthropic/v1/messages?api-version=2025-04-01-preview

Streaming is REQUIRED, not an optimization: Azure's outbound SNAT/LB idle
timeout silently drops any connection with no traffic for ~4 minutes. A
non-streaming call transfers zero bytes while the model generates, so any
generation longer than ~4 min was killed mid-flight (Foundry logged 499,
the client wasted the whole attempt — 2026-07-02 outage: 13/13 calls lost).
SSE keeps bytes flowing continuously, so the connection never idles.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"
# 24K output headroom: the Phase-4 report (Reference column, per-sleeve Current-vs-Reference
# gaps, override records) is verbose and was hitting the old 16K cap (stop_reason:max_tokens),
# risking truncation before the ===TRADES_JSON=== block. 24K is well within the model's 128K
# output max; output tokens are billed only as generated.
_DEFAULT_MAX_TOKENS = 24000
_DEFAULT_TEMPERATURE = 0.2
_CONNECT_TIMEOUT_SECONDS = 30
# Max gap BETWEEN stream chunks, not total call time. First-token latency on a
# ~72K-token prompt is the longest silent stretch; keep it under the ~4-min wall.
_READ_TIMEOUT_SECONDS = 180
_ANTHROPIC_VERSION = "2023-06-01"


class FoundryClient:
    def __init__(
        self,
        api_key: str | None,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint or os.environ.get("FOUNDRY_ENDPOINT", "")
        self.model = model or os.environ.get("FOUNDRY_MODEL", _DEFAULT_MODEL)

    @property
    def ready(self) -> bool:
        return bool(self.api_key) and bool(self.endpoint)

    def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = _DEFAULT_TEMPERATURE,
        retries: int = 2,
    ) -> str:
        """Send a single user message and return the assistant text (streamed)."""
        if not self.ready:
            raise RuntimeError(
                "FoundryClient not configured (need FoundryApiKey + FOUNDRY_ENDPOINT)"
            )

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [
                {"role": "user", "content": user_message},
            ],
            "stream": True,
        }

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with requests.post(
                    self.endpoint,
                    headers=headers,
                    json=body,
                    stream=True,
                    timeout=(_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise RuntimeError(
                            f"Foundry transient {resp.status_code}: {resp.text[:300]}"
                        )
                    if resp.status_code != 200:
                        raise RuntimeError(
                            f"Foundry {resp.status_code}: {resp.text[:500]}"
                        )
                    return self._consume_stream(resp, max_tokens)
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "Foundry call failed (attempt %d/%d): %s — retrying in %ds",
                        attempt + 1, retries + 1, e, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error("Foundry call failed permanently: %s", e)
        assert last_err is not None
        raise last_err

    def _consume_stream(self, resp: requests.Response, max_tokens: int) -> str:
        """Accumulate text from an Anthropic Messages SSE stream."""
        text_chunks: list[str] = []
        input_tokens: int | None = None
        output_tokens: int | None = None
        stop_reason: str | None = None
        saw_message_stop = False

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue  # skip event: lines, comments, blank keep-alives
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            event = json.loads(payload)
            etype = event.get("type")
            if etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text_chunks.append(delta.get("text", ""))
            elif etype == "message_start":
                input_tokens = (
                    event.get("message", {}).get("usage", {}).get("input_tokens")
                )
            elif etype == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
                output_tokens = event.get("usage", {}).get("output_tokens", output_tokens)
            elif etype == "message_stop":
                saw_message_stop = True
            elif etype == "error":
                err = event.get("error", {})
                raise RuntimeError(
                    f"Foundry stream error: {err.get('type')}: {err.get('message')}"
                )

        if not saw_message_stop:
            # Connection cut mid-generation (idle drop, worker recycle, …) —
            # partial text is untrustworthy; treat as a failed attempt.
            raise RuntimeError(
                f"Foundry stream ended without message_stop "
                f"(got {len(''.join(text_chunks))} chars)"
            )

        full = "".join(text_chunks).strip()
        if not full:
            raise RuntimeError("Foundry empty response (stream had no text deltas)")
        logger.info(
            "Foundry call ok: model=%s in_tokens=%s out_tokens=%s stop_reason=%s",
            self.model, input_tokens, output_tokens, stop_reason,
        )
        if stop_reason == "max_tokens":
            logger.warning(
                "Foundry response hit max_tokens cap (%s) — output likely truncated",
                max_tokens,
            )
        return full
