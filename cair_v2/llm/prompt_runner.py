from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cair_v2.llm.clients import DeepSeekClient, LLMCallResult
from cair_v2.llm.json_utils import ParsedModelOutput, parse_model_output, to_yaml_text


SYSTEM_PROMPT = """You construct CAIR-CodeBench instances.
CAIR v2 staged construction is realistic noisy multi-turn issue refinement, not progressive disclosure or ordinary issue splitting.
Return only strict JSON or YAML matching the requested schema.
Never reveal API keys, reference patch code, hidden tests, or implementation diffs."""


@dataclass
class PromptStepResult:
    step_name: str
    status: str
    parsed: Any | None = None
    raw_path: Path | None = None
    parsed_path: Path | None = None
    error: str | None = None
    parsed_format: str | None = None
    error_type: str | None = None
    llm_result: LLMCallResult | None = None
    call_results: list[LLMCallResult] | None = None
    retry_success: bool = False
    repair_success: bool = False
    compact_used: bool = False
    fallback_used: bool = False
    model_used: str | None = None


def ensure_cache_dir(instance_dir: Path) -> Path:
    cache_dir = instance_dir / ".llm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def render_user_prompt(prompt_text: str, context: dict[str, Any]) -> str:
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"{prompt_text.rstrip()}\n\n## Actual Input\n\n```json\n{context_json}\n```\n"


def write_parsed_cache(path: Path, parsed: ParsedModelOutput) -> None:
    if parsed.data is None:
        path.write_text("", encoding="utf-8")
        return
    path.write_text(to_yaml_text(parsed.data), encoding="utf-8")


def _raw_output_dir(instance_dir: Path) -> Path:
    raw_dir = instance_dir / ".build" / "raw_llm_outputs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


def _write_raw_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _parse_and_cache(
    *,
    raw_text: str,
    parsed_path: Path,
    error_path: Path,
) -> ParsedModelOutput:
    parsed = parse_model_output(raw_text)
    if parsed.ok:
        write_parsed_cache(parsed_path, parsed)
        if error_path.exists():
            error_path.unlink()
    else:
        error_path.write_text(parsed.error or "Unknown parse error", encoding="utf-8")
    return parsed


def _json_repair_prompt(raw_text: str) -> str:
    return (
        "只修复 JSON 语法，不改变含义，不新增内容。\n"
        "Return only one strict JSON object. No markdown, no prose.\n\n"
        f"{raw_text[:6000]}"
    )


def run_prompt_step(
    *,
    instance_dir: Path,
    step_name: str,
    cache_prefix: str,
    prompt_path: Path,
    context: dict[str, Any],
    client: DeepSeekClient | None,
    model: str = "deepseek-v4-flash",
    compact_prompt_path: Path | None = None,
    fallback_client: DeepSeekClient | None = None,
    fallback_model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    thinking: str | dict[str, Any] | None = None,
    response_format: str | dict[str, Any] | None = None,
    repair_temperature: float | None = None,
    repair_max_tokens: int | None = None,
    repair_thinking: str | dict[str, Any] | None = None,
    repair_response_format: str | dict[str, Any] | None = None,
    force: bool = False,
    no_api: bool = False,
    repair_json: bool = True,
    same_model_retries: int = 1,
    compact_retries: int = 1,
    fallback_retries: int = 1,
) -> PromptStepResult:
    cache_dir = ensure_cache_dir(instance_dir)
    raw_path = cache_dir / f"{cache_prefix}.raw.txt"
    parsed_path = cache_dir / f"{cache_prefix}.parsed.yaml"
    error_path = cache_dir / f"{cache_prefix}.error.txt"

    if parsed_path.exists() and not force:
        return PromptStepResult(step_name=step_name, status="cached", raw_path=raw_path, parsed_path=parsed_path)

    prompt_text = prompt_path.read_text(encoding="utf-8")
    user_prompt = render_user_prompt(prompt_text, context)
    compact_user_prompt = None
    if compact_prompt_path is not None:
        compact_user_prompt = render_user_prompt(compact_prompt_path.read_text(encoding="utf-8"), context)

    if no_api:
        print(f"\n--- DRY RUN STEP: {step_name} ---")
        print(f"Prompt file: {prompt_path}")
        print(f"Instance: {instance_dir}")
        print(f"Input keys: {', '.join(context.keys())}")
        preview = user_prompt[:4000]
        print(preview)
        if len(user_prompt) > len(preview):
            print(f"\n[Prompt truncated for dry-run preview: {len(user_prompt)} chars total]")
        return PromptStepResult(step_name=step_name, status="dry_run")

    if client is None:
        raise ValueError("client is required when no_api is False")

    call_results: list[LLMCallResult] = []
    fallback_model = fallback_model if fallback_model and fallback_model != model else None
    attempt_specs: list[tuple[str, DeepSeekClient, str, str, Path]] = [
        ("primary", client, model, user_prompt, raw_path),
    ]
    for retry_index in range(max(0, same_model_retries)):
        suffix = "retry" if retry_index == 0 else f"retry{retry_index + 1}"
        attempt_specs.append(("same_model_retry", client, model, user_prompt, cache_dir / f"{cache_prefix}.{suffix}.raw.txt"))
    if compact_user_prompt is not None and compact_retries > 0:
        for retry_index in range(max(0, compact_retries)):
            suffix = "compact" if retry_index == 0 else f"compact{retry_index + 1}"
            attempt_specs.append(
                ("compact_retry", client, model, compact_user_prompt, cache_dir / f"{cache_prefix}.{suffix}.raw.txt")
            )
    if fallback_model and fallback_retries > 0:
        for retry_index in range(max(0, fallback_retries)):
            suffix = "fallback" if retry_index == 0 else f"fallback{retry_index + 1}"
            attempt_specs.append(
                (
                    "fallback_retry",
                    fallback_client or client,
                    fallback_model,
                    compact_user_prompt or user_prompt,
                    cache_dir / f"{cache_prefix}.{suffix}.raw.txt",
                )
            )

    last_result: LLMCallResult | None = None
    for attempt_index, (attempt_name, attempt_client, attempt_model, attempt_prompt, attempt_raw_path) in enumerate(attempt_specs):
        if attempt_index > 0 and last_result and last_result.error_type not in {"empty_response", "timeout", "rate_limited", "server_error"}:
            break
        response = attempt_client.complete(
            SYSTEM_PROMPT,
            attempt_prompt,
            model=attempt_model,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            response_format=response_format,
            step_name=f"{step_name}.{attempt_name}",
            raw_output_dir=_raw_output_dir(instance_dir),
            retry_count=attempt_index,
        )
        call_results.append(response)
        last_result = response
        if attempt_index == 0 or response.text:
            _write_raw_text(attempt_raw_path, response.text)
        if not response.ok:
            continue
        _write_raw_text(attempt_raw_path, response.text)
        parsed = _parse_and_cache(raw_text=response.text, parsed_path=parsed_path, error_path=error_path)
        if parsed.ok:
            response.parsed_json = parsed.data
            return PromptStepResult(
                step_name=step_name,
                status="ok",
                parsed=parsed.data,
                raw_path=attempt_raw_path,
                parsed_path=parsed_path,
                parsed_format=parsed.format,
                llm_result=response,
                call_results=call_results,
                retry_success=attempt_index > 0,
                compact_used=attempt_name == "compact_retry",
                fallback_used=attempt_name == "fallback_retry",
                model_used=attempt_model,
                repair_success=parsed.format == "json_repaired",
            )
        response.error_type = parsed.error_type or "invalid_json"
        response.error_message = parsed.error
        if repair_json and response.text.strip():
            repair_raw_path = cache_dir / f"{cache_prefix}.repair.raw.txt"
            repair_response = attempt_client.complete(
                "Return repaired strict JSON only.",
                _json_repair_prompt(response.text),
                model=attempt_model,
                temperature=0.0 if repair_temperature is None else repair_temperature,
                max_tokens=repair_max_tokens if repair_max_tokens is not None else max_tokens,
                thinking=repair_thinking if repair_thinking is not None else thinking,
                response_format=repair_response_format if repair_response_format is not None else response_format,
                step_name=f"{step_name}.json_repair",
                raw_output_dir=_raw_output_dir(instance_dir),
                retry_count=len(call_results),
            )
            call_results.append(repair_response)
            _write_raw_text(repair_raw_path, repair_response.text)
            if repair_response.ok:
                repaired = _parse_and_cache(raw_text=repair_response.text, parsed_path=parsed_path, error_path=error_path)
                if repaired.ok:
                    repair_response.parsed_json = repaired.data
                    return PromptStepResult(
                        step_name=step_name,
                        status="ok",
                        parsed=repaired.data,
                        raw_path=repair_raw_path,
                        parsed_path=parsed_path,
                        parsed_format=repaired.format,
                        llm_result=repair_response,
                        call_results=call_results,
                        retry_success=attempt_index > 0,
                        repair_success=True,
                        compact_used=attempt_name == "compact_retry",
                        fallback_used=attempt_name == "fallback_retry",
                        model_used=attempt_model,
                    )
                repair_response.error_type = repaired.error_type or "invalid_json"
                repair_response.error_message = repaired.error
        return PromptStepResult(
            step_name=step_name,
            status="parse_error",
            raw_path=attempt_raw_path,
            parsed_path=parsed_path,
            error=parsed.error,
            error_type=parsed.error_type or "invalid_json",
            llm_result=response,
            call_results=call_results,
            compact_used=attempt_name == "compact_retry",
            fallback_used=attempt_name == "fallback_retry",
            model_used=attempt_model,
        )

    error_type = last_result.error_type if last_result else "unknown"
    return PromptStepResult(
        step_name=step_name,
        status=error_type or "unknown",
        raw_path=raw_path,
        parsed_path=parsed_path,
        error=(last_result.error_message if last_result else "LLM call failed"),
        error_type=error_type or "unknown",
        llm_result=last_result,
        call_results=call_results,
        retry_success=False,
        compact_used=any(index >= 0 and result.retry_count > 0 for index, result in enumerate(call_results)),
        fallback_used=bool(fallback_model and any(result.model == fallback_model for result in call_results)),
        model_used=last_result.model if last_result else model,
    )
