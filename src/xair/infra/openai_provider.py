"""LlmProvider implementation — OpenAI official SDK.

Retry, backoff, and token counting handled by the SDK.
"""

from __future__ import annotations

import json
import os

from openai import OpenAI, APIError, APITimeoutError

from ..domain.exceptions import ConfigError, LlmError
from .constants import DEFAULT_TIMEOUT, OPENAI_MODEL
from ..log import logger


_REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _is_reasoning_model(model: str) -> bool:
    name = (model or "").lower()
    return any(name.startswith(prefix) for prefix in _REASONING_MODEL_PREFIXES)


class OpenAIProvider:
    """Calls OpenAI Chat Completions and returns parsed JSON."""

    def __init__(self, api_key: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise ConfigError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=key, timeout=timeout, max_retries=3)
        self._last_usage: dict = {}

    @property
    def last_usage(self) -> dict:
        return self._last_usage

    def call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        temperature: float,
        json_mode: bool = True,
    ) -> dict:
        resolved_model = model or OPENAI_MODEL
        kwargs: dict = {
            "model": resolved_model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Reasoning models (gpt-5.x, o1, o3, o4) only accept temperature=1 (default).
        # Sending any other value returns 400 "Unsupported value". Omit the param
        # for these families and only pass it for classic chat models (gpt-4.x, etc).
        if not _is_reasoning_model(resolved_model):
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except APITimeoutError as e:
            raise LlmError(f"OpenAI API timed out: {e}", status_code=0) from e
        except APIError as e:
            raise LlmError(
                f"OpenAI API error: {e.message}",
                status_code=getattr(e, "status_code", 0) or 0,
                body=str(e.body)[:500] if e.body else "",
            ) from e

        if resp.usage:
            self._last_usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
            }
            logger.debug(f"Tokens: {resp.usage.prompt_tokens} input / {resp.usage.completion_tokens} output")

        content = resp.choices[0].message.content or ""
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"_raw": content}
