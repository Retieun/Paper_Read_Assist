"""A thin wrapper around the Anthropic SDK.

* structured JSON answers via ``output_config.format``;
* the paper text as a cached system-prompt prefix (prompt caching);
* streaming for long outputs so requests do not time out;
* explicit handling of ``refusal`` and ``max_tokens`` stop reasons.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import anthropic


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, model: str = "claude-opus-5", effort: Optional[str] = None):
        self.model = model
        self.effort = effort
        self.client = anthropic.Anthropic()
        self.last_usage: dict = {}

    # -- building blocks -------------------------------------------------------
    @staticmethod
    def system_blocks(instructions: str, cached_text: Optional[str] = None) -> list[dict]:
        blocks: list[dict] = [{"type": "text", "text": instructions}]
        if cached_text:
            blocks.append({"type": "text", "text": cached_text, "cache_control": {"type": "ephemeral", "ttl": "1h"}})
        return blocks

    def _record_usage(self, msg: Any) -> None:
        u = getattr(msg, "usage", None)
        if u is None:
            return
        self.last_usage = {
            "input_tokens": getattr(u, "input_tokens", 0), "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }

    @staticmethod
    def _check_stop(msg: Any) -> None:
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise LLMError("The model declined this request" + (f" ({details.category})" if details and getattr(details, "category", None) else "") + ".")
        if msg.stop_reason == "max_tokens":
            raise LLMError("The model's answer was cut off (max_tokens); try a smaller request.")

    @staticmethod
    def _text(msg: Any) -> str:
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    # -- calls ---------------------------------------------------------------------
    def complete_json(self, system: list[dict], user: str, schema: dict, max_tokens: int = 16000, stream: bool = False) -> dict:
        kwargs: dict = {
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if self.effort:
            kwargs["output_config"]["effort"] = self.effort
        try:
            if stream or max_tokens > 16000:
                with self.client.messages.stream(**kwargs) as s:
                    msg = s.get_final_message()
            else:
                msg = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError("Invalid API key.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the API; try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the API (network).") from e
        self._record_usage(msg)
        self._check_stop(msg)
        text = self._text(msg)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"The model returned invalid JSON: {e}") from e

    def complete_text(self, system: list[dict], user: str, max_tokens: int = 4000) -> str:
        kwargs: dict = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        try:
            msg = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise LLMError("Invalid API key.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limited by the API; try again in a minute.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the API (network).") from e
        self._record_usage(msg)
        self._check_stop(msg)
        return self._text(msg)


class FakeLLMClient:
    """Stand-in used by the tests: returns canned answers, records prompts."""

    def __init__(self, json_answers: Optional[list[dict]] = None, text_answers: Optional[list[str]] = None):
        self.json_answers = list(json_answers or [])
        self.text_answers = list(text_answers or [])
        self.calls: list[dict] = []
        self.model = "fake"
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    system_blocks = staticmethod(LLMClient.system_blocks)

    def complete_json(self, system, user, schema, max_tokens=16000, stream=False) -> dict:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.json_answers:
            return {"symbols": [], "terms": [], "found": False, "meaning": "", "evidence_blocks": [], "evidence_quote": "", "note": "fake"}
        return self.json_answers.pop(0)

    def complete_text(self, system, user, max_tokens=4000) -> str:
        self.calls.append({"system": system, "user": user})
        return self.text_answers.pop(0) if self.text_answers else "Plain English paraphrase (fake)."
