from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cair_v2.llm.clients import DEEPSEEK_API_URL, MissingAPIKeyError, load_dotenv
from cair_v2.llm.json_utils import parse_model_output


MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]
MAX_TOKENS = [900, 1500, 3000]
TIMEOUT = 180

TASKS = {
    "health_json": {
        "system": "Return strict JSON only. No markdown.",
        "user": 'Return exactly this JSON object: {"ok": true}',
    },
    "skeleton_json": {
        "system": "Return strict JSON only. No markdown. The word json is required.",
        "user": (
            'Return this JSON shape with exactly 4 turns: '
            '{"turns":[{"turn_id":"T1","operation":"reveal_vague_goal","introduced_units":["U1"]},'
            '{"turn_id":"T2","operation":"add_information","introduced_units":["U2"]},'
            '{"turn_id":"T3","operation":"correct","introduced_units":["U3"]},'
            '{"turn_id":"T4","operation":"confirm","introduced_units":["U4"]}]}'
        ),
    },
    "utterance_json": {
        "system": "Return strict JSON only. No markdown. The word json is required.",
        "user": (
            'Return this JSON shape with exactly 2 utterances: '
            '{"utterances":[{"turn_id":"T1","user_utterance":"A result looks wrong."},'
            '{"turn_id":"T2","user_utterance":"It happens in the nested case."}]}'
        ),
    },
}

MODES = {
    "default_text": {
        "description": "Current-style request: thinking defaults to enabled; no JSON response_format.",
        "extra_payload": {},
    },
    "default_json": {
        "description": "Thinking defaults to enabled; JSON response_format enabled.",
        "extra_payload": {"response_format": {"type": "json_object"}},
    },
    "non_reasoning_text": {
        "description": "Explicit non-thinking mode; no JSON response_format.",
        "extra_payload": {"thinking": {"type": "disabled"}},
    },
    "non_reasoning_json": {
        "description": "Explicit non-thinking mode plus JSON response_format.",
        "extra_payload": {"thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}},
    },
}


@dataclass
class ProbeResult:
    model: str
    task: str
    mode: str
    max_tokens: int
    status_code: int | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    content_len: int
    reasoning_content_len: int
    parsed_json_success: bool
    error_type: str | None
    elapsed_seconds: float
    response_format: str
    thinking: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "task": self.task,
            "mode": self.mode,
            "max_tokens": self.max_tokens,
            "status_code": self.status_code,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "content_len": self.content_len,
            "reasoning_content_len": self.reasoning_content_len,
            "parsed_json_success": self.parsed_json_success,
            "error_type": self.error_type,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "response_format": self.response_format,
            "thinking": self.thinking,
        }


def api_url() -> str:
    base = os.getenv("DEEPSEEK_API_URL") or os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_API_URL
    cleaned = base.rstrip("/")
    return cleaned if cleaned.endswith("/chat/completions") else f"{cleaned}/chat/completions"


def classify_status(status_code: int | None) -> str:
    if status_code in {408, 504}:
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if status_code is not None and 500 <= status_code <= 599:
        return "server_error"
    if status_code is not None and 400 <= status_code <= 499:
        return "client_exception"
    return "unknown"


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def post_chat(payload: dict[str, Any], api_key: str) -> tuple[int | None, dict[str, Any] | None, str | None]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        api_url(),
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8", errors="replace")
            return getattr(response, "status", None), json.loads(body), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = None
        return exc.code, parsed, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return None, None, f"{type(exc).__name__}: {exc}"
    except json.JSONDecodeError as exc:
        return None, None, f"response_json_decode_error: {exc}"


def run_one(model: str, task_name: str, task: dict[str, str], mode_name: str, mode: dict[str, Any], max_tokens: int, api_key: str) -> ProbeResult:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": task["system"]},
            {"role": "user", "content": task["user"]},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    payload.update(mode["extra_payload"])
    start = time.time()
    status_code, raw, transport_error = post_chat(payload, api_key)
    elapsed = time.time() - start

    finish_reason = None
    content = ""
    reasoning_content = ""
    prompt_tokens = None
    completion_tokens = None
    reasoning_tokens = None
    parsed_success = False
    error_type = None

    if raw and isinstance(raw, dict):
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        prompt_tokens = int_or_none(usage.get("prompt_tokens"))
        completion_tokens = int_or_none(usage.get("completion_tokens"))
        details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        reasoning_tokens = int_or_none(details.get("reasoning_tokens"))
        choices = raw.get("choices") if isinstance(raw.get("choices"), list) else []
        if choices and isinstance(choices[0], dict):
            first = choices[0]
            finish_reason = str(first.get("finish_reason")) if first.get("finish_reason") is not None else None
            message = first.get("message") if isinstance(first.get("message"), dict) else {}
            content = str(message.get("content") or "")
            reasoning_content = str(message.get("reasoning_content") or "")
    if transport_error:
        error_type = classify_status(status_code)
    elif not content.strip():
        error_type = "empty_response"
    else:
        parsed = parse_model_output(content)
        parsed_success = bool(parsed.ok and isinstance(parsed.data, dict))
        if not parsed_success:
            error_type = parsed.error_type or "invalid_json"

    return ProbeResult(
        model=model,
        task=task_name,
        mode=mode_name,
        max_tokens=max_tokens,
        status_code=status_code,
        finish_reason=finish_reason,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        content_len=len(content),
        reasoning_content_len=len(reasoning_content),
        parsed_json_success=parsed_success,
        error_type=error_type,
        elapsed_seconds=elapsed,
        response_format="json_object" if "response_format" in mode["extra_payload"] else "text",
        thinking=(mode["extra_payload"].get("thinking") or {}).get("type", "default_enabled"),
    )


def group_key(result: ProbeResult) -> tuple[str, str, int]:
    return (result.model, result.mode, result.max_tokens)


def summarize(results: list[ProbeResult]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[ProbeResult]] = {}
    for result in results:
        grouped.setdefault(group_key(result), []).append(result)
    rows = []
    for (model, mode, max_tokens), items in sorted(grouped.items()):
        content_ok = sum(1 for item in items if item.content_len > 0)
        parsed_ok = sum(1 for item in items if item.parsed_json_success)
        empty = sum(1 for item in items if item.error_type == "empty_response")
        avg_reasoning = sum(item.reasoning_content_len for item in items) / len(items)
        rows.append(
            {
                "model": model,
                "mode": mode,
                "max_tokens": max_tokens,
                "tasks": len(items),
                "content_ok": content_ok,
                "parsed_ok": parsed_ok,
                "empty_response": empty,
                "avg_reasoning_content_len": round(avg_reasoning, 1),
            }
        )
    return rows


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join([header, sep, *body])


def best_configs(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in summary_rows
        if row["content_ok"] == row["tasks"] and row["parsed_ok"] == row["tasks"] and row["empty_response"] == 0
    ]


def config_label(row: dict[str, Any]) -> str:
    return f"{row['model']} / {row['mode']} / max_tokens={row['max_tokens']}"


def recommendation_lines(summary_rows: list[dict[str, Any]], results: list[ProbeResult]) -> list[str]:
    stable = best_configs(summary_rows)
    stable_labels = [config_label(row) for row in stable]
    empty_with_reasoning = [
        result for result in results if result.error_type == "empty_response" and result.reasoning_content_len > 0
    ]
    default_stable = [row for row in stable if row["mode"].startswith("default")]
    non_reasoning_stable = [row for row in stable if row["mode"].startswith("non_reasoning")]
    json_stable = [row for row in stable if row["mode"].endswith("_json")]

    by_token = {
        max_tokens: sum(1 for result in results if result.max_tokens == max_tokens and result.error_type == "empty_response")
        for max_tokens in MAX_TOKENS
    }
    token_counts = [by_token[max_tokens] for max_tokens in MAX_TOKENS]
    if token_counts == sorted(token_counts, reverse=True) and token_counts[0] > token_counts[-1]:
        token_effect = "Increasing `max_tokens` reduced empty responses in this probe."
    elif len(set(token_counts)) == 1:
        token_effect = "Increasing `max_tokens` did not change the empty-response count in this probe."
    else:
        token_effect = "Increasing `max_tokens` had mixed effects in this probe; prefer the per-configuration table."

    if stable_labels:
        stable_answer = "; ".join(stable_labels)
    else:
        stable_answer = "No tested configuration was stable across all three JSON tasks."

    if empty_with_reasoning:
        offenders = sorted(
            {
                f"{result.model} / {result.mode} / max_tokens={result.max_tokens}"
                for result in empty_with_reasoning
            }
        )
        empty_answer = "; ".join(offenders)
    else:
        empty_answer = "No tested configuration produced empty content with non-empty reasoning_content."

    if non_reasoning_stable and not default_stable:
        route_answer = "Yes: the stable configurations are non-reasoning, so JSON-generation steps should route to non-reasoning mode/model settings."
    elif non_reasoning_stable and default_stable:
        route_answer = "Recommended: non-reasoning configurations are stable and directly address reasoning budget exhaustion, even though some default configurations also passed."
    elif stable:
        route_answer = "Not proven by this probe: stable configurations exist, but they are not specifically non-reasoning."
    else:
        route_answer = "Inconclusive: no tested configuration was stable enough to recommend a route."

    if json_stable:
        config_answer = "Yes: configure JSON steps separately with a stable JSON-mode route and token budget from this probe."
    elif stable:
        config_answer = "Yes: configure JSON steps separately using the stable route from this probe, even without JSON response_format."
    else:
        config_answer = "Yes in principle, but this probe did not identify a stable route yet."

    if stable and not empty_with_reasoning:
        staged_answer = "Yes, after applying the stable JSON route to dialogue skeleton/utterance calls."
    elif stable:
        staged_answer = "Yes, but only after routing staged JSON calls away from configurations that still show reasoning-only empty responses."
    else:
        staged_answer = "No: first identify a stable JSON-output route."

    return [
        f"- Stable `message.content` + parseable JSON: {stable_answer}",
        f"- Reasoning-heavy empty content: {empty_answer}",
        f"- Max-token effect: {token_effect}",
        f"- Need non-reasoning route for dialogue/skeleton/utterance: {route_answer}",
        f"- Separate CAIR JSON-step model/token config: {config_answer}",
        f"- Continue staged-llm refactor next: {staged_answer}",
    ]


def write_report(results: list[ProbeResult]) -> Path:
    docs = PROJECT_ROOT / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    path = docs / "llm_json_output_probe.md"
    result_rows = [result.to_dict() for result in results]
    summary_rows = summarize(results)
    stable = best_configs(summary_rows)
    empty_reasoning = [
        result.to_dict()
        for result in results
        if result.error_type == "empty_response" and result.reasoning_content_len > 0
    ]
    max_token_rows = []
    for max_tokens in MAX_TOKENS:
        items = [result for result in results if result.max_tokens == max_tokens]
        max_token_rows.append(
            {
                "max_tokens": max_tokens,
                "calls": len(items),
                "content_ok": sum(1 for item in items if item.content_len > 0),
                "parsed_ok": sum(1 for item in items if item.parsed_json_success),
                "empty_response": sum(1 for item in items if item.error_type == "empty_response"),
            }
        )
    lines = [
        "# LLM JSON Output Probe",
        "",
        "This probe is independent of CAIR batch construction. It sends three short JSON tasks to DeepSeek chat completions and records only structural response metadata.",
        "",
        "Official API notes used for this probe:",
        "- DeepSeek chat completion supports `thinking: {\"type\": \"enabled\"|\"disabled\"}`; thinking defaults to enabled.",
        "- DeepSeek JSON Output uses `response_format: {\"type\": \"json_object\"}` and still requires the prompt to request JSON.",
        "",
        "Sources: https://api-docs.deepseek.com/guides/thinking_mode and https://api-docs.deepseek.com/guides/json_mode",
        "",
        "## Configurations",
        "",
        markdown_table(
            [{"mode": name, "description": cfg["description"]} for name, cfg in MODES.items()],
            ["mode", "description"],
        ),
        "",
        "## Summary By Configuration",
        "",
        markdown_table(
            summary_rows,
            ["model", "mode", "max_tokens", "tasks", "content_ok", "parsed_ok", "empty_response", "avg_reasoning_content_len"],
        ),
        "",
        "## Max Token Effect",
        "",
        markdown_table(max_token_rows, ["max_tokens", "calls", "content_ok", "parsed_ok", "empty_response"]),
        "",
        "## Stable Configurations",
        "",
    ]
    if stable:
        lines.append(markdown_table(stable, ["model", "mode", "max_tokens", "tasks", "content_ok", "parsed_ok", "empty_response", "avg_reasoning_content_len"]))
    else:
        lines.append("No configuration produced valid JSON content for every task.")
    lines.extend(
        [
            "",
            "## Empty Responses With Reasoning Content",
            "",
        ]
    )
    if empty_reasoning:
        lines.append(
            markdown_table(
                empty_reasoning,
                [
                    "model",
                    "task",
                    "mode",
                    "max_tokens",
                    "status_code",
                    "finish_reason",
                    "prompt_tokens",
                    "completion_tokens",
                    "reasoning_tokens",
                    "content_len",
                    "reasoning_content_len",
                    "error_type",
                ],
            )
        )
    else:
        lines.append("No empty responses with non-empty reasoning content were observed.")
    lines.extend(
        [
            "",
            "## Detailed Results",
            "",
            markdown_table(
                result_rows,
                [
                    "model",
                    "task",
                    "mode",
                    "max_tokens",
                    "status_code",
                    "finish_reason",
                    "prompt_tokens",
                    "completion_tokens",
                    "reasoning_tokens",
                    "content_len",
                    "reasoning_content_len",
                    "parsed_json_success",
                    "error_type",
                    "thinking",
                    "response_format",
                ],
            ),
        "",
        "## Interpretation",
        "",
        *recommendation_lines(summary_rows, results),
        "",
        "- A configuration is considered stable here only if all three tasks returned non-empty `message.content` and parsed as JSON.",
        "- Empty `message.content` with non-empty `reasoning_content` is treated as a generation-mode failure, not a parser failure.",
        "- The probe intentionally does not store raw response bodies or secrets.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise MissingAPIKeyError('DEEPSEEK_API_KEY is not set. Run scripts/setup_deepseek_api.py or export it in your shell.')
    results: list[ProbeResult] = []
    total = len(MODELS) * len(MAX_TOKENS) * len(TASKS) * len(MODES)
    index = 0
    for model in MODELS:
        for max_tokens in MAX_TOKENS:
            for mode_name, mode in MODES.items():
                for task_name, task in TASKS.items():
                    index += 1
                    print(f"[{index}/{total}] model={model} mode={mode_name} max_tokens={max_tokens} task={task_name}")
                    results.append(run_one(model, task_name, task, mode_name, mode, max_tokens, api_key))
    report_path = write_report(results)
    summary_rows = summarize(results)
    stable = best_configs(summary_rows)
    print(f"wrote: {report_path}")
    print(json.dumps({"calls": len(results), "stable_config_count": len(stable), "stable_configs": stable}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
