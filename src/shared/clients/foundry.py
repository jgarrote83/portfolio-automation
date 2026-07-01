"""Azure AI Foundry — Claude via Anthropic Messages API surface.

Claude models hosted on Azure AI Foundry are NOT reachable via the Azure OpenAI
`/openai/deployments/*/chat/completions` path (returns 404 api_not_supported).
They must be called through the Anthropic-compatible endpoint:

    POST {FOUNDRY_ENDPOINT}
    headers:
        x-api-key: <FoundryApiKey>
        anthropic-version: 2023-06-01
        content-type: application/json
    body: { model, max_tokens, temperature, system, messages }

The verified endpoint shape is:
    https://<resource>.services.ai.azure.com/anthropic/v1/messages?api-version=2025-04-01-preview
"""
from __future__ import annotations

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
_TIMEOUT_SECONDS = 600  # Claude with large snapshots can take >3 min; Flex allows 40 min
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
        """Send a single user message and return the assistant text."""
        if not self.ready:
            raise RuntimeError(
                "FoundryClient not configured (need FoundryApiKey + FOUNDRY_ENDPOINT)"
            )

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [
                {"role": "user", "content": user_message},
            ],
        }

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    self.endpoint, headers=headers, json=body, timeout=_TIMEOUT_SECONDS
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(
                        f"Foundry transient {resp.status_code}: {resp.text[:300]}"
                    )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"Foundry {resp.status_code}: {resp.text[:500]}"
                    )
                data = resp.json()
                # Anthropic Messages response: { content: [{type:'text', text:'...'}], ... }
                parts = data.get("content", [])
                text_chunks = [p.get("text", "") for p in parts if p.get("type") == "text"]
                full = "".join(text_chunks).strip()
                if not full:
                    raise RuntimeError(f"Foundry empty response: {data}")
                logger.info(
                    "Foundry call ok: model=%s in_tokens=%s out_tokens=%s stop_reason=%s",
                    self.model,
                    data.get("usage", {}).get("input_tokens"),
                    data.get("usage", {}).get("output_tokens"),
                    data.get("stop_reason"),
                )
                if data.get("stop_reason") == "max_tokens":
                    logger.warning(
                        "Foundry response hit max_tokens cap (%s) \u2014 output likely truncated",
                        max_tokens,
                    )
                return full
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
