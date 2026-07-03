from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import time
import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "date",
    "retry-after",
    "x-request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
}
TRANSIENT_ERROR_TYPES = {"timeout", "rate_limited", "server_error"}


class MissingAPIKeyError(RuntimeError):
    """Raised when an LLM client cannot find its API key."""


@dataclass
class LLMCallResult:
    ok: bool
    text: str = ""
    parsed_json: Any | None = None
    error_type: str | None = None
    error_message: str | None = None
    model: str = ""
    status_code: int | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    content_len: int = 0
    reasoning_content_len: int = 0
    raw_response_path: str | None = None
    retry_count: int = 0

    @property
    def content(self) -> str:
        return self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "text": self.text,
            "parsed_json": self.parsed_json,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "model": self.model,
            "status_code": self.status_code,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "content_len": self.content_len,
            "reasoning_content_len": self.reasoning_content_len,
            "raw_response_path": self.raw_response_path,
            "retry_count": self.retry_count,
        }


LLMResponse = LLMCallResult


@dataclass
class _HTTPCall:
    status_code: int | None
    headers: dict[str, str]
    body: str
    error_type: str | None = None
    error_message: str | None = None


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = "deepseek-v4-flash",
        base_url: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int = 90,
        max_retries: int = 3,
    ) -> None:
        load_dotenv()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise MissingAPIKeyError(
                'DEEPSEEK_API_KEY is not set.\nPlease run: export DEEPSEEK_API_KEY="..."'
            )
        self.model = model
        self.base_url = base_url or os.getenv("DEEPSEEK_API_URL") or os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_API_URL
        self.api_url = _normalize_chat_completions_url(self.base_url)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries

    def _request_once(self, payload: dict[str, Any]) -> _HTTPCall:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(self.api_url, data=data, headers=headers, method="POST")

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
                return _HTTPCall(
                    status_code=getattr(response, "status", None),
                    headers=_safe_headers(dict(response.headers.items())),
                    body=body,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return _HTTPCall(
                status_code=exc.code,
                headers=_safe_headers(dict(exc.headers.items()) if exc.headers else {}),
                body=body,
                error_type=_classify_status_code(exc.code),
                error_message=f"HTTP {exc.code}: {body[:1000]}",
            )
        except (TimeoutError, socket.timeout) as exc:
            return _HTTPCall(status_code=None, headers={}, body="", error_type="timeout", error_message=str(exc))
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            error_type = "timeout" if isinstance(reason, (TimeoutError, socket.timeout)) else "client_exception"
            return _HTTPCall(status_code=None, headers={}, body="", error_type=error_type, error_message=str(exc))
        except http.client.IncompleteRead as exc:
            return _HTTPCall(status_code=None, headers={}, body="", error_type="server_error", error_message=str(exc))
        except OSError as exc:
            return _HTTPCall(status_code=None, headers={}, body="", error_type="client_exception", error_message=str(exc))
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
        thinking: str | dict[str, Any] | None = None,
        response_format: str | dict[str, Any] | None = None,
        step_name: str = "llm_call",
        raw_output_dir: Path | None = None,
        retry_count: int = 0,
    ) -> LLMCallResult:
        payload = {
            "model": model or self.model,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        normalized_thinking = _normalize_thinking(thinking)
        normalized_response_format = _normalize_response_format(response_format)
        if normalized_thinking is not None:
            payload["thinking"] = normalized_thinking
        if normalized_response_format is not None:
            payload["response_format"] = normalized_response_format
        prompt_hash = _prompt_hash(system_prompt, user_prompt)
        http_result = self._request_once(payload)
        result = _result_from_http(
            http_result,
            model=str(payload["model"]),
            retry_count=retry_count,
        )
        result.raw_response_path = _write_raw_response(
            raw_output_dir=raw_output_dir or Path(".build") / "raw_llm_outputs",
            step_name=step_name,
            prompt_hash=prompt_hash,
            request_metadata={
                "model": payload["model"],
                "base_url": _redacted_url(self.api_url),
                "temperature": payload["temperature"],
                "max_tokens": payload["max_tokens"],
                "thinking": normalized_thinking,
                "response_format": normalized_response_format,
                "timeout": self.timeout,
                "system_prompt_hash": _sha256(system_prompt),
                "user_prompt_hash": _sha256(user_prompt),
                "system_prompt_chars": len(system_prompt),
                "user_prompt_chars": len(user_prompt),
            },
            http_result=http_result,
            call_result=result,
        )
        return result


def _normalize_chat_completions_url(base_url: str) -> str:
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    return f"{cleaned}/chat/completions"


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def _redacted_url(url: str) -> str:
    return re.sub(r"([?&](?:api[_-]?key|token|key)=)[^&]+", r"\1<redacted>", url, flags=re.IGNORECASE)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt_hash(system_prompt: str, user_prompt: str) -> str:
    return _sha256(f"{system_prompt}\n\n{user_prompt}")


def _safe_headers(headers: dict[str, Any]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for key, value in headers.items():
        lower = str(key).lower()
        if lower in SAFE_RESPONSE_HEADERS or lower.startswith("x-ratelimit"):
            safe[str(key)] = str(value)
    return safe


def _classify_status_code(status_code: int | None) -> str:
    if status_code in {408, 504}:
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if status_code is not None and 500 <= status_code <= 599:
        return "server_error"
    if status_code is not None and 400 <= status_code <= 499:
        return "client_exception"
    return "unknown"


def _normalize_thinking(value: str | dict[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        thinking_type = value.get("type")
        return {"type": str(thinking_type)} if thinking_type else None
    text = str(value).strip().lower()
    if not text or text in {"default", "default_enabled", "none", "null"}:
        return None
    if text in {"disabled", "enabled"}:
        return {"type": text}
    return {"type": text}


def _normalize_response_format(value: str | dict[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        format_type = value.get("type")
        return {"type": str(format_type)} if format_type else None
    text = str(value).strip().lower()
    if not text or text in {"text", "default", "none", "null"}:
        return None
    return {"type": text}


def _extract_choice(raw: dict[str, Any]) -> tuple[str, str | None, str]:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", None, ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if content is None:
        content = first.get("text")
    reasoning_content = message.get("reasoning_content")
    return (
        str(content or ""),
        str(first.get("finish_reason")) if first.get("finish_reason") is not None else None,
        str(reasoning_content or ""),
    )


def _result_from_http(http_result: _HTTPCall, *, model: str, retry_count: int) -> LLMCallResult:
    if http_result.error_type:
        return LLMCallResult(
            ok=False,
            model=model,
            status_code=http_result.status_code,
            error_type=http_result.error_type,
            error_message=http_result.error_message,
            retry_count=retry_count,
        )
    try:
        raw = json.loads(http_result.body)
    except json.JSONDecodeError as exc:
        return LLMCallResult(
            ok=False,
            model=model,
            status_code=http_result.status_code,
            error_type="unknown",
            error_message=f"API response was not JSON: {exc}",
            retry_count=retry_count,
        )
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    content, finish_reason, reasoning_content = _extract_choice(raw)
    error_type = None
    error_message = None
    ok = True
    if not content.strip():
        ok = False
        error_type = "empty_response"
        error_message = "LLM returned an empty message content"
    return LLMCallResult(
        ok=ok,
        text=content,
        model=model,
        status_code=http_result.status_code,
        finish_reason=finish_reason,
        prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
        completion_tokens=_int_or_none(usage.get("completion_tokens")),
        reasoning_tokens=_int_or_none(details.get("reasoning_tokens")),
        content_len=len(content),
        reasoning_content_len=len(reasoning_content),
        error_type=error_type,
        error_message=error_message,
        retry_count=retry_count,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")[:80] or "llm_call"


def _write_raw_response(
    *,
    raw_output_dir: Path,
    step_name: str,
    prompt_hash: str,
    request_metadata: dict[str, Any],
    http_result: _HTTPCall,
    call_result: LLMCallResult,
) -> str:
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    filename = (
        f"{time.time_ns()}_{_slug(step_name)}_{_slug(call_result.model)}_"
        f"{prompt_hash[:12]}_r{call_result.retry_count}.json"
    )
    path = raw_output_dir / filename
    payload = {
        "created_at": created_at,
        "step_name": step_name,
        "prompt_hash": prompt_hash,
        "model": call_result.model,
        "request": request_metadata,
        "response": {
            "raw_response_text": http_result.body or call_result.text,
            "status_code": call_result.status_code,
            "headers": http_result.headers,
            "finish_reason": call_result.finish_reason,
            "prompt_tokens": call_result.prompt_tokens,
            "completion_tokens": call_result.completion_tokens,
            "reasoning_tokens": call_result.reasoning_tokens,
            "content_len": call_result.content_len,
            "reasoning_content_len": call_result.reasoning_content_len,
        },
        "error_type": call_result.error_type,
        "error_message": call_result.error_message,
        "retry_count": call_result.retry_count,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)
