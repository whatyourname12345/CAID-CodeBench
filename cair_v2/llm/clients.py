from __future__ import annotations

import json
import os
import signal
import time
import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


class MissingAPIKeyError(RuntimeError):
    """Raised when an LLM client cannot find its API key."""


@dataclass
class LLMResponse:
    model: str
    content: str
    raw: dict[str, Any]


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 90,
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise MissingAPIKeyError(
                'DEEPSEEK_API_KEY is not set.\nPlease run: export DEEPSEEK_API_KEY="..."'
            )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries

    def _request_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(DEEPSEEK_API_URL, data=data, headers=headers, method="POST")

        def _raise_timeout(signum, frame):  # type: ignore[no-untyped-def]
            raise TimeoutError(f"DeepSeek request exceeded total timeout of {self.timeout}s")

        old_handler = None
        try:
            old_handler = signal.signal(signal.SIGALRM, _raise_timeout)
            signal.setitimer(signal.ITIMER_REAL, self.timeout)
        except (ValueError, AttributeError):
            old_handler = None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
            return json.loads(body)
        finally:
            if old_handler is not None:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_handler)

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = {
            "model": model or self.model,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                raw = self._request_once(payload)
                content = raw["choices"][0]["message"]["content"]
                if not str(content).strip():
                    last_error = RuntimeError("DeepSeek returned an empty message content")
                    if attempt == self.max_retries - 1:
                        break
                    sleep_seconds = min(2**attempt, 30) + 0.25
                    time.sleep(sleep_seconds)
                    continue
                return LLMResponse(model=payload["model"], content=content, raw=raw)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    safe_body = exc.read().decode("utf-8", errors="replace")[:1000]
                    raise RuntimeError(f"DeepSeek request failed with HTTP {exc.code}: {safe_body}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, http.client.IncompleteRead) as exc:
                last_error = exc

            sleep_seconds = min(2**attempt, 30) + 0.25
            time.sleep(sleep_seconds)

        raise RuntimeError(f"DeepSeek request failed after {self.max_retries} retries: {last_error}")
