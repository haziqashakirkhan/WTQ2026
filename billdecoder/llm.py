"""Thin LLM client for Google Gemini (google-genai SDK).

Features: allow-list enforcement (via Settings), global throttling, retries with
exponential backoff, and tolerant JSON parsing.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import Sequence

from .config import Settings

log = logging.getLogger(__name__)

_FATAL_MARKERS = ("401", "403", "api key not valid", "permission")


class LLMError(RuntimeError):
    pass


def extract_json(text: str):
    """Parse a JSON object out of model text (handles ``` fences and stray prose)."""
    if text is None:
        raise ValueError("empty response")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in response")
        return json.loads(cleaned[start : end + 1])


class LLMClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._client = None
        self._init_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_call = 0.0

    # ---- public API -------------------------------------------------------
    def complete(self, system: str, prompt: str, images: Sequence[bytes] = (),
                 json_output: bool = True, max_tokens: int = 4096) -> str:
        last: Exception | None = None
        for attempt in range(self.s.max_retries + 1):
            self._throttle()
            try:
                text = self._call(system, prompt, images, json_output, max_tokens)
                if not text or not text.strip():
                    raise LLMError("empty model response")
                return text
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise many types
                last = exc
                if attempt == self.s.max_retries or any(m in str(exc).lower() for m in _FATAL_MARKERS):
                    break
                delay = min(60.0, 2.0 * 2**attempt) + random.random()
                log.warning("LLM call failed (%s); retry %d in %.1fs", exc, attempt + 1, delay)
                time.sleep(delay)
        raise LLMError(f"LLM call failed: {last}") from last

    def complete_json(self, system: str, prompt: str, images: Sequence[bytes] = (),
                      max_tokens: int = 4096) -> dict:
        last: Exception | None = None
        for _ in range(3):
            text = self.complete(system, prompt, images, True, max_tokens)
            try:
                data = extract_json(text)
                if isinstance(data, dict):
                    return data
                last = ValueError("top-level JSON is not an object")
            except ValueError as exc:
                last = exc
                log.warning("Unparseable JSON from model (%s); retrying", exc)
        raise LLMError(f"model never returned valid JSON: {last}")

    # ---- internals --------------------------------------------------------
    def _throttle(self) -> None:
        if self.s.min_request_interval <= 0:
            return
        with self._rate_lock:
            wait = self._last_call + self.s.min_request_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def _sdk(self):
        with self._init_lock:
            if self._client is None:
                from google import genai
                self._client = genai.Client(api_key=self.s.api_key)
            return self._client

    def _call(self, system, prompt, images, json_output, max_tokens) -> str:
        from google.genai import types
        parts = [types.Part.from_bytes(data=img, mime_type="image/jpeg") for img in images] + [prompt]
        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=0, max_output_tokens=max_tokens + 4000,
            response_mime_type="application/json" if json_output else None)
        resp = self._sdk().models.generate_content(model=self.s.model, contents=parts, config=cfg)
        return resp.text or ""