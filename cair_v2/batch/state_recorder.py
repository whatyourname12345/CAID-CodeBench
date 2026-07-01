from __future__ import annotations

from typing import Any

from cair_v2.batch.batch_state import BatchState
from cair_v2.staged.schemas import StageStepResult


def _error_types(result: StageStepResult) -> list[str]:
    values = [str(call.error_type) for call in result.call_results if getattr(call, "error_type", None)]
    if result.error_type and result.error_type not in values:
        values.append(result.error_type)
    return values


def _request_value(call: Any, key: str) -> Any:
    path = getattr(call, "raw_response_path", None)
    if not path:
        return None
    return None


def _content_len(result: StageStepResult) -> int:
    return sum(int(getattr(call, "content_len", 0) or 0) for call in result.call_results)


def _reasoning_content_len(result: StageStepResult) -> int:
    return sum(int(getattr(call, "reasoning_content_len", 0) or 0) for call in result.call_results)


def record_staged_step(
    state: BatchState,
    instance_id: str,
    result: StageStepResult,
    *,
    params: dict[str, Any],
) -> None:
    models = list(dict.fromkeys(str(call.model) for call in result.call_results if getattr(call, "model", None)))
    if result.model_used and result.model_used not in models:
        models.append(result.model_used)
    state.record_llm_step(
        instance_id,
        result.step_name,
        calls=result.api_calls_made,
        success=result.ok,
        error_types=_error_types(result),
        repair_success=result.repair_success,
        retry_success=result.retry_success,
        fallback_used=result.fallback_used,
        model_used=models,
        failure_reason=None if result.ok else "; ".join(result.errors),
        thinking=params.get("thinking"),
        response_format=params.get("response_format"),
        content_len=_content_len(result),
        reasoning_content_len=_reasoning_content_len(result),
    )


def record_staged_result(
    state: BatchState,
    instance_id: str,
    step_results: dict[str, StageStepResult],
    *,
    params_by_step: dict[str, dict[str, Any]],
) -> None:
    for step_name, result in step_results.items():
        record_staged_step(state, instance_id, result, params=params_by_step.get(step_name, {}))

