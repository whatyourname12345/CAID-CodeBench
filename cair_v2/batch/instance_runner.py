from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cair_v2.batch.batch_state import BatchState
from cair_v2.batch.fallback_policy import should_use_template_fallback
from cair_v2.batch.status_semantics import (
    build_attempt_record,
    build_normalized_fields,
    decide_retry,
    normalize_status,
    resolve_filter_reason,
)
from cair_v2.batch.pipeline_steps import deepseek_client_factory, model_config_summary, run_staged_instance_pipeline, staged_step_configs
from cair_v2.batch.quality_gate_v2 import evaluate_v2_instance_dir, evaluate_v2_quality
from cair_v2.batch.state_recorder import record_staged_result
from cair_v2.construction.build_instance_skeleton import patch_metadata_from_candidate, source_record_from_candidate
from cair_v2.construction.cair_compiler_v2 import build_cair_instance_v2, write_intermediate_debug, write_v2_outputs
from cair_v2.construction.dialogue_plan import DialoguePlanResult, run_dialogue_plan, run_staged_dialogue_plan, validate_dialogue_plan, write_dialogue_plan
from cair_v2.construction.dialogue_template import build_template_dialogue_plan
from cair_v2.construction.instance_io import write_json
from cair_v2.construction.load_swebench_record import row_to_clean_dict
from cair_v2.construction.patch_summarizer import summarize_patch_record
from cair_v2.construction.semantic_capsule import run_semantic_capsule
from cair_v2.construction.sanitizer import sanitize_dialogue_plan
from cair_v2.llm.clients import DeepSeekClient, MissingAPIKeyError
from cair_v2.staged.source_spans import summarize_source_matches


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class BatchV2Options:
    input_file: Path
    output_dir: Path
    model_generator: str
    model_critical: str
    model_reviewer: str
    limit: int | None
    max_api_calls: int
    client_max_retries: int = 1
    client_timeout: int = 90
    dry_run: bool = False
    no_api: bool = False
    resume: bool = False
    force: bool = False
    include_rejected: bool = False
    optional_reviewer: bool = False
    dialogue_strategy: str = "monolithic"
    mode: str = "staged-llm"
    llm_params: dict[str, Any] | None = None


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_absolute() and path.is_relative_to(PROJECT_ROOT) else str(path)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def append_retry_log(instance_dir: Path, entry: dict[str, Any]) -> None:
    build_dir = instance_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)
    path = build_dir / "retry_log.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"created_at": timestamp(), **entry}, ensure_ascii=False) + "\n")


def create_or_load_instance_v2(record: dict[str, Any], output_dir: Path, *, force: bool) -> Path:
    clean = row_to_clean_dict(record)
    instance_id = str(clean["instance_id"])
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    build_dir = instance_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)
    if force:
        for relative in [
            "cair_instance.json",
            "quality_report.json",
            "agent_view.json",
            "evaluator_view.json",
            ".build/dialogue_plan.json",
            ".build/semantic_capsule.json",
            ".build/fact_extraction.json",
            ".build/intent_revision.json",
            ".build/dialogue_skeleton.json",
            ".build/utterance_realization.json",
            ".build/initial_report_plan.json",
            ".build/noisy_revision_event_plan.json",
            ".build/realistic_utterance_realization.json",
            ".build/semantic_reviewer.json",
            ".build/semantic_review.json",
            ".build/intermediate_debug.json",
            ".build/retry_log.jsonl",
            ".build/step_errors.jsonl",
            ".build/repair_logs.jsonl",
        ]:
            path = instance_dir / relative
            if path.exists() and path.is_file():
                path.unlink()
        raw_outputs = build_dir / "raw_llm_outputs"
        if raw_outputs.exists():
            shutil.rmtree(raw_outputs)

    source_path = instance_dir / "source_record.json"
    if force or not source_path.exists():
        write_json(source_path, source_record_from_candidate(clean))

    write_json(build_dir / "candidate_record_private.json", clean)
    patch_metadata = summarize_patch_record(clean, patch_metadata_from_candidate(clean))
    write_json(build_dir / "patch_metadata.json", patch_metadata)
    return instance_dir


def _client(model: str, options: BatchV2Options) -> DeepSeekClient | None:
    if options.no_api or options.dry_run:
        return None
    return DeepSeekClient(model=model, max_retries=options.client_max_retries, timeout=options.client_timeout)


def _step_params(options: BatchV2Options, step: str) -> dict[str, Any]:
    params = dict((options.llm_params or {}).get(step) or {})
    allowed = {"temperature", "max_tokens", "thinking", "response_format"}
    return {key: value for key, value in params.items() if key in allowed}


def _api_budget_available(state: BatchState, options: BatchV2Options) -> bool:
    return options.no_api or options.dry_run or state.api_calls < options.max_api_calls


def _count_api_call(state: BatchState, instance_id: str, result_api_call_made: bool | int, options: BatchV2Options) -> None:
    if options.no_api or options.dry_run:
        return
    count = int(result_api_call_made) if isinstance(result_api_call_made, int) else (1 if result_api_call_made else 0)
    if count > 0:
        state.add_instance_api_call(instance_id, count)


def _fallback_model_for(model: str, options: BatchV2Options) -> str | None:
    if model == options.model_generator and options.model_critical != model:
        return options.model_critical
    if model == options.model_critical and options.model_generator != model:
        return options.model_generator
    return None


def _record_llm_result_stats(state: BatchState, instance_id: str, step: str, result: Any | None) -> None:
    if result is None:
        state.record_llm_step(
            instance_id,
            step,
            calls=0,
            success=False,
            error_type="unknown",
            failure_reason="execution_exception",
        )
        return
    prompt_result = getattr(result, "prompt_result", None)
    call_results = list(getattr(result, "call_results", None) or getattr(prompt_result, "call_results", None) or [])
    error_types = [str(call.error_type) for call in call_results if getattr(call, "error_type", None)]
    result_error_type = getattr(result, "error_type", None)
    if result_error_type in error_types:
        result_error_type = None
    models_used = list(dict.fromkeys(str(call.model) for call in call_results if getattr(call, "model", None)))
    explicit_model = getattr(result, "model_used", None) or getattr(prompt_result, "model_used", None)
    if explicit_model and explicit_model not in models_used:
        models_used.append(explicit_model)
    state.record_llm_step(
        instance_id,
        step,
        calls=int(getattr(result, "api_calls_made", 0) or len(call_results) or (1 if getattr(result, "api_call_made", False) else 0)),
        success=bool(getattr(result, "ok", False)),
        error_type=result_error_type,
        error_types=error_types,
        repair_success=bool(getattr(result, "repair_success", False) or getattr(prompt_result, "repair_success", False)),
        retry_success=bool(getattr(result, "retry_success", False) or getattr(prompt_result, "retry_success", False)),
        fallback_used=bool(getattr(result, "fallback_used", False) or getattr(prompt_result, "fallback_used", False)),
        model_used=models_used,
        failure_reason=None
        if getattr(result, "ok", False)
        else (getattr(result, "failure_reason", None) or "; ".join(getattr(result, "errors", []) or [])),
    )


def _record_prompt_result_stats(
    state: BatchState,
    instance_id: str,
    step: str,
    result: Any | None,
    *,
    success: bool,
    failure_reason: str | None = None,
    error_type: str | None = None,
) -> None:
    if result is None:
        state.record_llm_step(instance_id, step, error_type="unknown", failure_reason=failure_reason or "missing_prompt_result")
        return
    call_results = list(getattr(result, "call_results", None) or [])
    error_types = [str(call.error_type) for call in call_results if getattr(call, "error_type", None)]
    result_error_type = error_type or getattr(result, "error_type", None)
    if result_error_type in error_types:
        result_error_type = None
    models_used = list(dict.fromkeys(str(call.model) for call in call_results if getattr(call, "model", None)))
    if getattr(result, "model_used", None) and result.model_used not in models_used:
        models_used.append(result.model_used)
    state.record_llm_step(
        instance_id,
        step,
        calls=len(call_results),
        success=success,
        error_type=result_error_type,
        error_types=error_types,
        repair_success=bool(getattr(result, "repair_success", False)),
        retry_success=bool(getattr(result, "retry_success", False)),
        fallback_used=bool(getattr(result, "fallback_used", False)),
        model_used=models_used,
        failure_reason=None if success else (failure_reason or getattr(result, "error", None)),
    )


def _record_staged_dialogue_stats(state: BatchState, instance_id: str, result: DialoguePlanResult) -> None:
    step_results = result.step_results or {}
    skeleton_result = step_results.get("dialogue_skeleton")
    utterance_result = step_results.get("utterance_realization")
    if skeleton_result is not None:
        skeleton_ok = skeleton_result.status == "ok" and not result.source.startswith("llm_staged_skeleton")
        _record_prompt_result_stats(
            state,
            instance_id,
            "dialogue_skeleton",
            skeleton_result,
            success=skeleton_ok,
            failure_reason=None if skeleton_ok else result.failure_reason or "; ".join(result.errors),
            error_type=result.error_type if not skeleton_ok else None,
        )
    if utterance_result is not None:
        utterance_ok = result.ok
        _record_prompt_result_stats(
            state,
            instance_id,
            "utterance_realization",
            utterance_result,
            success=utterance_ok,
            failure_reason=None if utterance_ok else result.failure_reason or "; ".join(result.errors),
            error_type=result.error_type if not utterance_ok else None,
        )


def _template_dialogue_result(instance_dir: Path, capsule: dict[str, Any]) -> DialoguePlanResult:
    raw_plan = build_template_dialogue_plan(capsule)
    sanitized = sanitize_dialogue_plan(raw_plan)
    plan = sanitized.data if isinstance(sanitized.data, dict) else {"turns": []}
    errors, warnings = validate_dialogue_plan(plan, capsule)
    errors = [*sanitized.hard_failures, *errors]
    warnings = [*sanitized.warnings, *warnings]
    if not errors:
        write_dialogue_plan(instance_dir, plan, source="template")
    return DialoguePlanResult(
        ok=not errors,
        plan=plan,
        status="template" if not errors else "template_failed",
        warnings=warnings,
        errors=errors,
        source="template",
        failure_reason=None if not errors else "; ".join(errors),
    )


def _has_revision_support(capsule: dict[str, Any]) -> bool:
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    return revision_support.get("has_revision_fact") is True and bool(revision_support.get("revision_unit_ids"))


def _source_span_stats_from_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(capsule, dict):
        return {
            "source_span_repaired_count": 0,
            "source_span_unmatched_count": 0,
            "source_match_status": {},
        }
    status = capsule.get("source_match_status") if isinstance(capsule.get("source_match_status"), dict) else None
    if status is not None:
        return {
            "source_span_repaired_count": int(capsule.get("source_span_repaired_count") or 0),
            "source_span_unmatched_count": int(capsule.get("source_span_unmatched_count") or 0),
            "source_match_status": status,
        }
    units = capsule.get("fact_units") if isinstance(capsule.get("fact_units"), list) else []
    return summarize_source_matches(units)


def _source_span_stats_from_result(result: Any) -> dict[str, Any]:
    step_results = getattr(result, "step_results", {}) if result is not None else {}
    fact_result = step_results.get("fact_extraction") if isinstance(step_results, dict) else None
    fact_data = getattr(fact_result, "data", None)
    if isinstance(fact_data, dict):
        status = fact_data.get("source_match_status") if isinstance(fact_data.get("source_match_status"), dict) else None
        if status is not None:
            return {
                "source_span_repaired_count": int(fact_data.get("source_span_repaired_count") or 0),
                "source_span_unmatched_count": int(fact_data.get("source_span_unmatched_count") or 0),
                "source_match_status": status,
            }
        units = fact_data.get("fact_units") if isinstance(fact_data.get("fact_units"), list) else []
        return summarize_source_matches(units)
    return _source_span_stats_from_capsule(getattr(result, "semantic_capsule", {}) or {})


def _add_source_span_stats(report: dict[str, Any], stats: dict[str, Any]) -> None:
    report["source_span_repaired_count"] = int(stats.get("source_span_repaired_count") or 0)
    report["source_span_unmatched_count"] = int(stats.get("source_span_unmatched_count") or 0)
    report["source_match_status"] = (
        stats.get("source_match_status") if isinstance(stats.get("source_match_status"), dict) else {}
    )


def _realization_validation_metadata(result: Any) -> dict[str, Any]:
    step_results = getattr(result, "step_results", {}) if result is not None else {}
    realization = step_results.get("realistic_utterance_realization") if isinstance(step_results, dict) else None
    data = getattr(realization, "data", None)
    if isinstance(data, dict) and data.get("realization_leakage_detected"):
        return {
            "realization_leakage_detected": True,
            "realization_retry_used": bool(data.get("realization_retry_used")),
            "leakage_severity": str(data.get("leakage_severity") or "unknown"),
            "final_status": str(data.get("final_status") or getattr(result, "status", "") or "unknown"),
        }
    return {
        "realization_leakage_detected": False,
        "realization_retry_used": False,
        "leakage_severity": "none",
        "final_status": str(getattr(result, "status", "") or "not_applicable"),
    }


def finalize_v2(
    *,
    instance_dir: Path,
    instance_id: str,
    capsule: dict[str, Any],
    dialogue_plan: dict[str, Any],
    dialogue_source: str,
    state: BatchState,
    options: BatchV2Options,
    pipeline_version: str = "v2_minimal_robust",
    model_config: dict[str, Any] | None = None,
    semantic_review: dict[str, Any] | None = None,
) -> str:
    pre_status = "accepted_with_template_dialogue" if dialogue_source == "template" else "accepted"
    compact = build_cair_instance_v2(
        instance_dir,
        semantic_capsule=capsule,
        dialogue_plan=dialogue_plan,
        generator_model=options.model_generator,
        critical_model=options.model_critical,
        reviewer_model=options.model_reviewer,
        construction_status=pre_status,
        quality_gate_passed=False,
        dialogue_source=dialogue_source,
        pipeline_version=pipeline_version,
        model_config_summary=model_config,
        semantic_review=semantic_review,
    )
    quality = evaluate_v2_quality(compact=compact, semantic_capsule=capsule, dialogue_plan=dialogue_plan)
    source_stats = _source_span_stats_from_capsule(capsule)
    _add_source_span_stats(quality, source_stats)
    noisy_quality = quality.get("noisy_refinement") if isinstance(quality.get("noisy_refinement"), dict) else {}
    if quality.get("passed"):
        status = pre_status
    else:
        status = str(quality.get("status") or "manual_review_required")
        if status == "accepted":
            status = "manual_review_required"
    compact.setdefault("metadata", {})["construction_status"] = status
    compact["metadata"]["quality_gate_passed"] = bool(quality.get("passed"))
    if noisy_quality:
        compact["metadata"]["old_progressive_disclosure_pattern"] = bool(noisy_quality.get("old_progressive_disclosure_pattern"))
        compact["metadata"]["unresolved_wrong_claims"] = int(noisy_quality.get("unresolved_wrong_claims") or 0)
        compact["metadata"]["scenario_fit"] = str(noisy_quality.get("scenario_fit") or compact["metadata"].get("scenario_fit") or "")
    write_v2_outputs(instance_dir, compact, quality)
    cache_dir = instance_dir / ".llm_cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    write_intermediate_debug(
        instance_dir,
        {
            "quality_gate_v2": quality,
        },
    )
    summary = "; ".join(quality.get("hard_failures", []) + quality.get("soft_warnings", []))
    state.update_instance(
        instance_id,
        status=status,
        current_step="done" if quality.get("passed") else "quality_gate",
        quality_gate="pass" if quality.get("passed") else "fail",
        quality_gate_summary=summary or "all v2 quality gates passed",
        release_candidate=bool(quality.get("passed")),
        reviewer="skipped",
        localization_checkpoint_ready=quality.get("checks", {}).get("localization_checkpoint_ready"),
        gold_files_count=quality.get("localization", {}).get("gold_files_count"),
        gold_functions_count=quality.get("localization", {}).get("gold_functions_count"),
        function_gold_available=quality.get("localization", {}).get("function_gold_available"),
        last_error=None if quality.get("passed") else summary,
        failure_reason=None if quality.get("passed") else quality.get("recommended_action"),
        source_span_repaired_count=source_stats.get("source_span_repaired_count"),
        source_span_unmatched_count=source_stats.get("source_span_unmatched_count"),
        source_match_status=source_stats.get("source_match_status"),
    )
    state.save()
    return status


def _staged_nonaccepted_outputs(
    *,
    instance_dir: Path,
    instance_id: str,
    staged_status: str,
    capsule: dict[str, Any],
    dialogue_plan: dict[str, Any],
    dialogue_source: str,
    semantic_review: dict[str, Any],
    state: BatchState,
    options: BatchV2Options,
    model_config: dict[str, Any],
) -> None:
    compact = build_cair_instance_v2(
        instance_dir,
        semantic_capsule=capsule,
        dialogue_plan=dialogue_plan,
        generator_model=options.model_generator,
        critical_model=options.model_critical,
        reviewer_model=options.model_reviewer,
        construction_status=staged_status,
        quality_gate_passed=False,
        dialogue_source=dialogue_source,
        pipeline_version="v2_noisy_refinement",
        model_config_summary=model_config,
        semantic_review=semantic_review,
    )
    quality = evaluate_v2_quality(compact=compact, semantic_capsule=capsule, dialogue_plan=dialogue_plan)
    source_stats = _source_span_stats_from_capsule(capsule)
    _add_source_span_stats(quality, source_stats)
    noisy_quality = quality.get("noisy_refinement") if isinstance(quality.get("noisy_refinement"), dict) else {}
    quality["semantic_review"] = semantic_review
    quality["passed"] = False
    quality["status"] = staged_status
    quality.setdefault("hard_failures", []).append(f"semantic_reviewer decision: {staged_status}")
    compact.setdefault("metadata", {})["construction_status"] = staged_status
    compact["metadata"]["quality_gate_passed"] = False
    if noisy_quality:
        compact["metadata"]["old_progressive_disclosure_pattern"] = bool(noisy_quality.get("old_progressive_disclosure_pattern"))
        compact["metadata"]["unresolved_wrong_claims"] = int(noisy_quality.get("unresolved_wrong_claims") or 0)
        compact["metadata"]["scenario_fit"] = str(noisy_quality.get("scenario_fit") or compact["metadata"].get("scenario_fit") or "")
    write_v2_outputs(instance_dir, compact, quality)
    summary = "; ".join(quality.get("hard_failures", []) + quality.get("soft_warnings", []))
    state.update_instance(
        instance_id,
        status=staged_status,
        current_step="semantic_reviewer",
        semantic_capsule="pass",
        dialogue_plan="pass",
        dialogue_plan_llm_success=True,
        dialogue_plan_template_fallback_used=False,
        quality_gate="fail",
        quality_gate_summary=summary,
        release_candidate=False,
        localization_checkpoint_ready=quality.get("checks", {}).get("localization_checkpoint_ready"),
        gold_files_count=quality.get("localization", {}).get("gold_files_count"),
        gold_functions_count=quality.get("localization", {}).get("gold_functions_count"),
        function_gold_available=quality.get("localization", {}).get("function_gold_available"),
        last_error=summary,
        failure_reason="semantic_reviewer",
        source_span_repaired_count=source_stats.get("source_span_repaired_count"),
        source_span_unmatched_count=source_stats.get("source_span_unmatched_count"),
        source_match_status=source_stats.get("source_match_status"),
    )
    state.save()


def _manual_review_reason(errors: list[str]) -> str:
    text = "; ".join(errors).lower()
    if "fact_grounding_validation_failed" in text:
        return "fact_grounding_validation_failed"
    if "source_span is not aligned to source" in text:
        return "fact_grounding_validation_failed"
    if "utterance_template_artifact" in text:
        return "utterance_template_artifact"
    if "template artifact" in text:
        return "utterance_template_artifact"
    if "invalid_noisy_revision_event_plan" in text:
        return "invalid_noisy_revision_event_plan"
    if "realization_private_leakage_rejected" in text:
        return "realization_private_leakage_rejected"
    if "realization_leakage_manual_review" in text:
        return "realization_leakage_manual_review"
    if "leaks forbidden implementation/benchmark text" in text or "benchmark/private wording" in text:
        return "realization_leakage_manual_review"
    if "no_withheld_units_for_later_refinement" in text:
        return "no_withheld_units_for_later_refinement"
    if "would_degenerate_into_progressive_disclosure" in text:
        return "would_degenerate_into_progressive_disclosure"
    if "no source-grounded revision fact" in text:
        return "insufficient_source_facts_for_noisy_refinement"
    if "insufficient_source_facts_for_noisy_refinement" in text:
        return "insufficient_source_facts_for_noisy_refinement"
    return "insufficient_source_facts_for_noisy_refinement"


def _broad_manual_failure_reason(manual_reason: str) -> str:
    if manual_reason in {
        "no_withheld_units_for_later_refinement",
        "would_degenerate_into_progressive_disclosure",
        "insufficient_source_facts_for_noisy_refinement",
    }:
        return "insufficient_source_facts_for_noisy_refinement"
    if manual_reason == "invalid_noisy_revision_event_plan":
        return "invalid_noisy_revision_event_plan"
    if manual_reason == "fact_grounding_validation_failed":
        return "fact_grounding_validation_failed"
    if manual_reason == "utterance_template_artifact":
        return "utterance_template_artifact"
    return manual_reason


def _write_staged_manual_review_report(
    *,
    instance_dir: Path,
    instance_id: str,
    result,
    state: BatchState,
    manual_reason: str,
    status: str = "manual_review_required",
) -> None:
    step_results = getattr(result, "step_results", {}) if result is not None else {}
    step_data = {}
    if isinstance(step_results, dict) and result.failed_step:
        step = step_results.get(result.failed_step)
        if isinstance(getattr(step, "data", None), dict):
            step_data = step.data
    if step_data.get("manual_review_reason"):
        manual_reason = str(step_data.get("manual_review_reason"))
    elif step_data.get("reason"):
        manual_reason = str(step_data.get("reason"))
    broad_reason = str(step_data.get("reason") or _broad_manual_failure_reason(manual_reason))
    source_stats = _source_span_stats_from_result(result)
    realization_meta = _realization_validation_metadata(result)
    final_status = status if status in {"manual_review_required", "rejected"} else "manual_review_required"
    report = {
        "passed": False,
        "status": final_status,
        "failed_stage": result.failed_step,
        "review_stage": result.failed_step,
        "failure_reason": broad_reason,
        "manual_review_reason": manual_reason,
        "hard_failures": list(result.errors or []) if final_status == "rejected" else [],
        "soft_warnings": list(result.errors or []),
        "checks": {
            "old_progressive_disclosure_pattern": "not_generated",
            "unresolved_wrong_claims": "not_applicable",
            "quality_gate_runtime": "not_run",
        },
        "noisy_refinement": {
            "old_progressive_disclosure_pattern": "not_generated",
            "unresolved_wrong_claims": "not_applicable",
            "scenario_fit": "not_generated",
        },
        "recommended_action": "reject" if final_status == "rejected" else "manual_review",
    }
    _add_source_span_stats(report, source_stats)
    report.update(realization_meta)
    write_json(instance_dir / "quality_report.json", report)
    write_intermediate_debug(instance_dir, {"quality_gate_v2": report})
    state.update_instance(
        instance_id,
        status=final_status,
        current_step=result.failed_step or "staged_pipeline",
        failed_stage=result.failed_step,
        review_stage=result.failed_step,
        semantic_capsule="pass" if result.semantic_capsule else "fail",
        dialogue_plan=final_status,
        dialogue_plan_llm_success=False,
        dialogue_plan_template_fallback_used=False,
        quality_gate="not_run",
        release_candidate=False,
        last_error=manual_reason,
        failure_reason=broad_reason,
        manual_review_reason=manual_reason,
        old_progressive_disclosure_pattern="not_generated",
        unresolved_wrong_claims="not_applicable",
        source_span_repaired_count=source_stats.get("source_span_repaired_count"),
        source_span_unmatched_count=source_stats.get("source_span_unmatched_count"),
        source_match_status=source_stats.get("source_match_status"),
        realization_leakage_detected=realization_meta.get("realization_leakage_detected"),
        realization_retry_used=realization_meta.get("realization_retry_used"),
        leakage_severity=realization_meta.get("leakage_severity"),
        final_status=realization_meta.get("final_status"),
        suggested_fix="Inspect staged .build/*.json; source facts may not support noisy refinement.",
    )
    state.save()


def _run_staged_attempt(
    *,
    record: dict[str, Any],
    output_dir: Path,
    state: BatchState,
    options: BatchV2Options,
    force: bool,
) -> None:
    instance_id = str(record.get("instance_id"))
    repo = str(record.get("repo") or "")
    instance_dir = create_or_load_instance_v2(record, output_dir, force=force)
    state.ensure_instance(instance_id, repo=repo, path=rel(instance_dir))
    state.update_instance(instance_id, dialogue_strategy="staged", mode="staged-llm")
    item = state.ensure_instance(instance_id)
    if options.resume and item.get("status") in {"accepted", "accepted_with_template_dialogue"} and not force:
        quality = evaluate_v2_instance_dir(instance_dir)
        state.update_instance(
            instance_id,
            quality_gate="pass" if quality.get("passed") else "fail",
            localization_checkpoint_ready=quality.get("checks", {}).get("localization_checkpoint_ready"),
            gold_files_count=quality.get("localization", {}).get("gold_files_count"),
            gold_functions_count=quality.get("localization", {}).get("gold_functions_count"),
            function_gold_available=quality.get("localization", {}).get("function_gold_available"),
        )
        state.save()
        return

    llm_params = options.llm_params or {}
    configs = staged_step_configs(
        model_generator=options.model_generator,
        model_critical=options.model_critical,
        llm_params=llm_params,
    )
    config_summary = model_config_summary(configs)
    for step_name, config in configs.items():
        state.add_model_used(instance_id, step_name, config.model)
    state.update_instance(instance_id, status="running", current_step="staged_pipeline", last_error=None)
    state.save()

    try:
        result = run_staged_instance_pipeline(
            instance_dir=instance_dir,
            step_configs=configs,
            client_factory=deepseek_client_factory(
                no_api=options.no_api,
                dry_run=options.dry_run,
                client_max_retries=options.client_max_retries,
                client_timeout=options.client_timeout,
            ),
            no_api=options.no_api,
            dry_run=options.dry_run,
        )
    except MissingAPIKeyError:
        raise
    except Exception as exc:
        state.add_instance_api_call(instance_id)
        state.update_instance(
            instance_id,
            status="step_failed",
            current_step="staged_pipeline",
            last_error=str(exc),
            failure_reason="staged_pipeline_exception",
        )
        append_retry_log(instance_dir, {"step": "staged_pipeline", "event": "exception", "error": str(exc)})
        state.save()
        return

    if result.api_calls_made:
        state.add_instance_api_call(instance_id, result.api_calls_made)
    record_staged_result(
        state,
        instance_id,
        result.step_results,
        params_by_step={name: config.params for name, config in configs.items()},
    )
    if options.dry_run or options.no_api:
        state.update_instance(instance_id, status="pending", current_step="dry_run_complete", release_candidate=False)
        state.save()
        return

    if result.ok:
        state.update_instance(
            instance_id,
            semantic_capsule="pass",
            dialogue_plan="pass",
            dialogue_plan_llm_success=True,
            dialogue_plan_template_fallback_used=False,
            dialogue_plan_failure_reason=None,
        )
        status = finalize_v2(
            instance_dir=instance_dir,
            instance_id=instance_id,
            capsule=result.semantic_capsule,
            dialogue_plan=result.dialogue_plan,
            dialogue_source="llm_staged",
            state=state,
            options=options,
            pipeline_version="v2_noisy_refinement",
            model_config=config_summary,
            semantic_review=result.semantic_review,
        )
        if status not in {"accepted", "accepted_with_template_dialogue"}:
            state.update_instance(instance_id, suggested_fix="Inspect staged .build/*.json and quality_report.json.")
            state.save()
        return

    if should_use_template_fallback(result):
        state.add_escalation(
            instance_id,
            result.failed_step or "staged_dialogue",
            options.model_generator,
            "template",
            "Staged dialogue failed after targeted retry; using final template fallback",
        )
        plan_result = _template_dialogue_result(instance_dir, result.semantic_capsule)
        state.record_llm_step(instance_id, result.failed_step or "realistic_utterance_realization", fallback_used=True)
        state.update_instance(
            instance_id,
            dialogue_plan_template_fallback_used=True,
            dialogue_plan_llm_success=False,
            dialogue_plan_failure_reason="; ".join(result.errors),
        )
        append_retry_log(
            instance_dir,
            {"step": "dialogue_template", "event": "ok" if plan_result.ok else "failure", "errors": plan_result.errors, "warnings": plan_result.warnings},
        )
        if plan_result.ok:
            state.update_instance(
                instance_id,
                semantic_capsule="pass",
                dialogue_plan="template",
                dialogue_plan_llm_success=False,
                dialogue_plan_template_fallback_used=True,
            )
            status = finalize_v2(
                instance_dir=instance_dir,
                instance_id=instance_id,
                capsule=result.semantic_capsule,
                dialogue_plan=plan_result.plan,
                dialogue_source="template",
                state=state,
                options=options,
                pipeline_version="v2_noisy_refinement",
                model_config=config_summary,
            )
            if status not in {"accepted", "accepted_with_template_dialogue"}:
                state.update_instance(instance_id, suggested_fix="Template fallback compiled but failed quality gate.")
                state.save()
            return

    if result.failed_step == "semantic_reviewer" and result.semantic_capsule and result.dialogue_plan:
        staged_status = result.status if result.status in {"manual_review_required", "rejected"} else "manual_review_required"
        _staged_nonaccepted_outputs(
            instance_dir=instance_dir,
            instance_id=instance_id,
            staged_status=staged_status,
            capsule=result.semantic_capsule,
            dialogue_plan=result.dialogue_plan,
            dialogue_source=result.dialogue_source,
            semantic_review=result.semantic_review,
            state=state,
            options=options,
            model_config=config_summary,
        )
        return

    if result.status in {"manual_review_required", "rejected"}:
        manual_reason = _manual_review_reason(result.errors)
        _write_staged_manual_review_report(
            instance_dir=instance_dir,
            instance_id=instance_id,
            result=result,
            state=state,
            manual_reason=manual_reason,
            status=result.status,
        )
        return

    status = result.status if result.status in {"manual_review_required", "rejected", "step_failed"} else "step_failed"
    source_stats = _source_span_stats_from_result(result)
    step_failed_report = {
        "passed": False,
        "status": status,
        "failed_stage": result.failed_step or "staged_pipeline",
        "failure_reason": result.failed_step or "staged_pipeline_failed",
        "hard_failures": list(result.errors or []),
        "soft_warnings": list(result.warnings or []),
        "checks": {
            "quality_gate_runtime": "not_run",
        },
        "recommended_action": "inspect_runtime_or_schema_failure",
    }
    _add_source_span_stats(step_failed_report, source_stats)
    write_json(instance_dir / "quality_report.json", step_failed_report)
    write_intermediate_debug(instance_dir, {"quality_gate_v2": step_failed_report})
    state.update_instance(
        instance_id,
        status=status,
        current_step=result.failed_step or "staged_pipeline",
        semantic_capsule="pass" if result.semantic_capsule else "fail",
        dialogue_plan="pass" if result.dialogue_plan else "fail",
        dialogue_plan_llm_success=False,
        dialogue_plan_template_fallback_used=False,
        last_error="; ".join(result.errors),
        failure_reason=result.failed_step or "staged_pipeline_failed",
        source_span_repaired_count=source_stats.get("source_span_repaired_count"),
        source_span_unmatched_count=source_stats.get("source_span_unmatched_count"),
        source_match_status=source_stats.get("source_match_status"),
        suggested_fix="Inspect staged .build/*.json; do not force a revision without source fact support.",
    )
    state.save()


def _read_quality_report(instance_dir: Path) -> dict[str, Any]:
    path = instance_dir / "quality_report.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _apply_normalized_outcome(
    *,
    instance_dir: Path,
    instance_id: str,
    state: BatchState,
    fields: dict[str, Any],
    attempts: list[dict[str, Any]],
    accepted_after_retry: bool,
    previous_normalized_status: str | None,
) -> None:
    """Persist normalized-status fields to state, quality_report, and metadata."""
    normalized = str(fields.get("normalized_status") or "")
    accepted_via = "retry" if accepted_after_retry else ("initial" if normalized == "accepted" else None)
    state_updates: dict[str, Any] = dict(fields)
    state_updates["normalized_attempts"] = attempts
    state_updates["normalized_attempt_count"] = len(attempts)
    state_updates["accepted_after_retry"] = accepted_after_retry
    if normalized == "accepted":
        state_updates["accepted_via"] = accepted_via
    if accepted_after_retry:
        state_updates["status_changed_by_retry"] = True
        state_updates["previous_normalized_status"] = previous_normalized_status or "auto_filtered"
        state_updates["final_normalized_status"] = "accepted"
    state.update_instance(instance_id, **state_updates)
    state.save()

    quality_report = _read_quality_report(instance_dir)
    if quality_report:
        quality_report.update(fields)
        quality_report["attempts"] = attempts
        quality_report["accepted_after_retry"] = accepted_after_retry
        if accepted_after_retry:
            quality_report["status_changed_by_retry"] = True
            quality_report["previous_normalized_status"] = previous_normalized_status or "auto_filtered"
            quality_report["final_normalized_status"] = "accepted"
        write_json(instance_dir / "quality_report.json", quality_report)

    instance_path = instance_dir / "cair_instance.json"
    if instance_path.exists():
        try:
            compact = json.loads(instance_path.read_text(encoding="utf-8"))
        except Exception:
            compact = None
        if isinstance(compact, dict):
            metadata = compact.setdefault("metadata", {})
            metadata["normalized_status"] = fields.get("normalized_status")
            metadata["review_mode"] = fields.get("review_mode")
            metadata["human_review_expected"] = fields.get("human_review_expected")
            if "filter_reason" in fields:
                metadata["filter_reason"] = fields.get("filter_reason")
            if normalized == "accepted":
                metadata["accepted_via"] = accepted_via
                metadata["accepted_after_retry"] = accepted_after_retry
            write_json(instance_path, compact)


def run_one_instance_staged_v2(
    *,
    record: dict[str, Any],
    output_dir: Path,
    state: BatchState,
    options: BatchV2Options,
) -> None:
    """Run the staged pipeline with a bounded, typed retry policy.

    Attempt 1 always runs. If the terminal outcome is a retry-eligible
    auto_filter subclass or a transient step_failed, exactly one additional
    full-chain re-execution is attempted (``max_full_chain_attempts == 2``).
    accepted / rejected / non-transient step_failed / non-retryable auto_filter
    are never retried.
    """
    instance_id = str(record.get("instance_id"))
    attempts: list[dict[str, Any]] = []
    previous_normalized_status: str | None = None
    accepted_after_retry = False
    max_attempts = 2

    for attempt_id in range(1, max_attempts + 1):
        item_before = state.ensure_instance(instance_id)
        api_before = int(item_before.get("api_calls", 0) or 0)
        force = options.force if attempt_id == 1 else True
        _run_staged_attempt(
            record=record,
            output_dir=output_dir,
            state=state,
            options=options,
            force=force,
        )
        item = state.ensure_instance(instance_id)
        status = str(item.get("status") or "")

        # Dry-run / no-api attempts do not produce a terminal semantic outcome.
        if options.dry_run or options.no_api or status in {"pending", "running"}:
            return

        instance_dir = output_dir / instance_id
        quality_report = _read_quality_report(instance_dir)
        normalized = normalize_status(status)
        filter_reason = resolve_filter_reason(item, quality_report) if normalized == "auto_filtered" else None
        retry_plan = decide_retry(normalized, filter_reason or "", item)

        api_after = int(item.get("api_calls", 0) or 0)
        attempts.append(
            build_attempt_record(
                attempt_id=attempt_id,
                status=status,
                item=item,
                quality_report=quality_report,
                filter_reason=filter_reason,
                api_calls=max(0, api_after - api_before),
            )
        )

        budget_ok = _api_budget_available(state, options)
        will_retry = (
            retry_plan.eligible
            and attempt_id < retry_plan.max_full_chain_attempts
            and budget_ok
            and normalized in {"auto_filtered", "step_failed"}
        )
        if will_retry:
            previous_normalized_status = normalized
            append_retry_log(
                instance_dir,
                {
                    "step": "full_chain_retry",
                    "event": "retry",
                    "attempt_id": attempt_id,
                    "from_normalized_status": normalized,
                    "filter_reason": filter_reason,
                    "retry_policy": retry_plan.policy,
                    "retry_reason": retry_plan.reason,
                },
            )
            continue

        if attempt_id > 1 and normalized == "accepted":
            accepted_after_retry = True

        filter_stage = item.get("failed_stage") or item.get("review_stage") or item.get("current_step")
        fields = build_normalized_fields(
            status=status,
            filter_reason=filter_reason,
            filter_stage=filter_stage,
            retry_plan=retry_plan,
        )
        _apply_normalized_outcome(
            instance_dir=instance_dir,
            instance_id=instance_id,
            state=state,
            fields=fields,
            attempts=attempts,
            accepted_after_retry=accepted_after_retry,
            previous_normalized_status=previous_normalized_status,
        )
        return


def run_one_instance_v2(
    *,
    record: dict[str, Any],
    output_dir: Path,
    state: BatchState,
    options: BatchV2Options,
) -> None:
    if options.mode == "staged-llm":
        run_one_instance_staged_v2(record=record, output_dir=output_dir, state=state, options=options)
        return

    instance_id = str(record.get("instance_id"))
    repo = str(record.get("repo") or "")
    instance_dir = create_or_load_instance_v2(record, output_dir, force=options.force)
    state.ensure_instance(instance_id, repo=repo, path=rel(instance_dir))
    state.update_instance(instance_id, dialogue_strategy=options.dialogue_strategy)
    item = state.ensure_instance(instance_id)
    if options.resume and item.get("status") in {"accepted", "accepted_with_template_dialogue"} and not options.force:
        quality = evaluate_v2_instance_dir(instance_dir)
        state.update_instance(
            instance_id,
            quality_gate="pass" if quality.get("passed") else "fail",
            localization_checkpoint_ready=quality.get("checks", {}).get("localization_checkpoint_ready"),
            gold_files_count=quality.get("localization", {}).get("gold_files_count"),
            gold_functions_count=quality.get("localization", {}).get("gold_functions_count"),
            function_gold_available=quality.get("localization", {}).get("function_gold_available"),
        )
        state.save()
        return

    semantic = None
    semantic_attempts = [
        ("semantic_capsule", options.model_generator),
        ("semantic_capsule_pro", options.model_critical),
    ]
    for label, model in semantic_attempts:
        state.update_instance(instance_id, status="running", current_step="semantic_capsule", last_error=None)
        state.add_model_used(instance_id, label, model)
        state.save()
        append_retry_log(instance_dir, {"step": "semantic_capsule", "model": model, "event": "start", "label": label})
        if not _api_budget_available(state, options):
            break
        try:
            fallback_model = _fallback_model_for(model, options)
            result = run_semantic_capsule(
                instance_dir,
                model=model,
                client=_client(model, options),
                fallback_model=fallback_model,
                fallback_client=_client(fallback_model, options) if fallback_model else None,
                llm_params=_step_params(options, "semantic_capsule"),
                json_repair_params=_step_params(options, "json_repair"),
                force=options.force or label.endswith("_pro"),
                no_api=options.no_api,
                dry_run=options.dry_run,
            )
        except MissingAPIKeyError:
            raise
        except Exception as exc:
            state.add_instance_api_call(instance_id)
            error = f"semantic_capsule execution failed: {exc}"
            append_retry_log(instance_dir, {"step": "semantic_capsule", "event": "failure", "error": error, "label": label})
            result = None
        if result is not None:
            _count_api_call(state, instance_id, result.api_calls_made, options)
            _record_llm_result_stats(state, instance_id, "semantic_capsule", result)
            if result.ok:
                semantic = result
                state.update_instance(instance_id, semantic_capsule="pass")
                append_retry_log(
                    instance_dir,
                    {
                        "step": "semantic_capsule",
                        "event": "ok",
                        "label": label,
                        "warnings": result.warnings,
                        "retry_success": result.retry_success,
                        "repair_success": result.repair_success,
                        "fallback_used": result.fallback_used,
                        "model_used": result.model_used,
                    },
                )
                if label.endswith("_pro"):
                    state.add_escalation(instance_id, "semantic_capsule", options.model_generator, model, "Flash semantic capsule failed; retried with Pro")
                break
            append_retry_log(
                instance_dir,
                {
                    "step": "semantic_capsule",
                    "event": "quality_failure",
                    "label": label,
                    "errors": result.errors,
                    "warnings": result.warnings,
                    "error_type": result.error_type,
                },
            )
            semantic = result
        else:
            _record_llm_result_stats(state, instance_id, "semantic_capsule", None)
    if options.dry_run or options.no_api:
        state.update_instance(instance_id, status="pending", current_step="dry_run_complete", release_candidate=False)
        state.save()
        return
    if semantic is None or not semantic.ok:
        append_retry_log(
            instance_dir,
            {
                "step": "semantic_capsule",
                "event": "quality_failure",
                "errors": semantic.errors if semantic else ["max_api_calls_reached"],
                "warnings": semantic.warnings if semantic else [],
            },
        )
        state.update_instance(
            instance_id,
            status="manual_review_required",
            current_step="semantic_capsule",
            semantic_capsule="fail",
            last_error="; ".join(semantic.errors if semantic else ["max_api_calls_reached"]),
            failure_reason="semantic_capsule_quality_failed",
        )
        state.save()
        return

    capsule = semantic.capsule
    revision_supported = _has_revision_support(capsule)
    plan_result: DialoguePlanResult | None = None
    dialogue_attempts = [
        ("dialogue_plan_pro", options.model_critical),
        ("dialogue_plan_flash", options.model_generator),
    ]
    for label, model in dialogue_attempts:
        if not _api_budget_available(state, options):
            break
        state.update_instance(instance_id, status="running", current_step="dialogue_plan")
        state.add_model_used(instance_id, label, model)
        state.save()
        append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "start", "label": label})
        try:
            fallback_model = _fallback_model_for(model, options)
            if options.dialogue_strategy == "staged":
                result = run_staged_dialogue_plan(
                    instance_dir,
                    capsule=capsule,
                    model=model,
                    client=_client(model, options),
                    skeleton_params=_step_params(options, "dialogue_skeleton"),
                    utterance_params=_step_params(options, "utterance_realization"),
                    json_repair_params=_step_params(options, "json_repair"),
                    force=True,
                    no_api=options.no_api,
                    dry_run=options.dry_run,
                    cache_label=label,
                )
            else:
                result = run_dialogue_plan(
                    instance_dir,
                    capsule=capsule,
                    model=model,
                    client=_client(model, options),
                    fallback_model=fallback_model,
                    fallback_client=_client(fallback_model, options) if fallback_model else None,
                    llm_params=_step_params(options, "dialogue_plan"),
                    json_repair_params=_step_params(options, "json_repair"),
                    force=True,
                    no_api=options.no_api,
                    dry_run=options.dry_run,
                    cache_label=label,
                )
        except Exception as exc:
            state.add_instance_api_call(instance_id)
            append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "failure", "error": str(exc), "label": label})
            result = DialoguePlanResult(ok=False, status="exception", api_call_made=False, errors=[str(exc)], source=f"llm:{model}", error_type="client_exception")
        _count_api_call(state, instance_id, result.api_calls_made, options)
        if options.dialogue_strategy == "staged":
            _record_staged_dialogue_stats(state, instance_id, result)
        else:
            _record_llm_result_stats(state, instance_id, "dialogue_plan", result)
        if result.ok:
            plan_result = result
            state.update_instance(
                instance_id,
                dialogue_plan="pass",
                dialogue_plan_llm_success=True,
                dialogue_plan_repaired=bool(result.repaired),
                dialogue_plan_quality_retry_used=bool(result.quality_retry_used),
                dialogue_plan_template_fallback_used=False,
                dialogue_plan_failure_reason=None,
            )
            append_retry_log(
                instance_dir,
                {
                    "step": "dialogue_plan",
                    "model": model,
                    "event": "ok",
                    "label": label,
                    "warnings": result.warnings,
                    "retry_success": result.retry_success,
                    "repair_success": result.repair_success,
                    "fallback_used": result.fallback_used,
                    "model_used": result.model_used,
                },
            )
            break
        if result.status == "manual_review_required":
            plan_result = result
            state.update_instance(
                instance_id,
                dialogue_plan_repaired=bool(result.repaired),
                dialogue_plan_quality_retry_used=bool(result.quality_retry_used),
                dialogue_plan_failure_reason=result.failure_reason or "; ".join(result.errors),
            )
            append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "manual_review_required", "label": label, "errors": result.errors, "error_type": result.error_type})
            if not revision_supported:
                break
            continue
        state.update_instance(
            instance_id,
            dialogue_plan_repaired=bool(result.repaired),
            dialogue_plan_quality_retry_used=bool(result.quality_retry_used),
            dialogue_plan_failure_reason=result.failure_reason or "; ".join(result.errors),
        )
        append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "quality_failure", "label": label, "errors": result.errors, "error_type": result.error_type})
        plan_result = result

    if plan_result is None or not plan_result.ok:
        if plan_result is not None and plan_result.status == "manual_review_required" and not revision_supported:
            state.update_instance(
                instance_id,
                status="manual_review_required",
                current_step="dialogue_plan",
                dialogue_plan="manual_review_required",
                last_error="; ".join(plan_result.errors),
                failure_reason="no_supported_revision_fact",
                dialogue_plan_llm_success=False,
                dialogue_plan_template_fallback_used=False,
                dialogue_plan_failure_reason=plan_result.failure_reason or "; ".join(plan_result.errors),
                suggested_fix="Inspect .build/semantic_capsule.json revision_support; do not force a CAIR revision without issue evidence.",
            )
            state.save()
            return
        if not revision_supported:
            append_retry_log(
                instance_dir,
                {
                    "step": "dialogue_template",
                    "event": "skipped",
                    "reason": "semantic_capsule.revision_support.has_revision_fact is false",
                },
            )
            state.update_instance(
                instance_id,
                status="manual_review_required",
                current_step="dialogue_plan",
                dialogue_plan="manual_review_required",
                last_error="semantic_capsule has no supported revision fact; template fallback skipped",
                failure_reason="no_supported_revision_fact",
                dialogue_plan_llm_success=False,
                dialogue_plan_template_fallback_used=False,
                dialogue_plan_failure_reason="semantic_capsule has no supported revision fact; template fallback skipped",
                suggested_fix="Manual review can decide whether this issue should be used as underspecification-only or excluded from CAIR revision seeds.",
            )
            state.save()
            return
        llm_failure_reason = plan_result.failure_reason if plan_result is not None else "dialogue_plan budget exhausted"
        if not llm_failure_reason and plan_result is not None:
            llm_failure_reason = "; ".join(plan_result.errors)
        state.add_escalation(instance_id, "dialogue_plan", options.model_critical, "template", "Pro and Flash dialogue plan failed or budget exhausted")
        plan_result = _template_dialogue_result(instance_dir, capsule)
        state.record_llm_step(
            instance_id,
            "dialogue_plan" if options.dialogue_strategy == "monolithic" else "utterance_realization",
            fallback_used=True,
        )
        state.update_instance(
            instance_id,
            dialogue_plan_template_fallback_used=True,
            dialogue_plan_llm_success=False,
            dialogue_plan_failure_reason=llm_failure_reason or plan_result.failure_reason or "; ".join(plan_result.errors),
        )
        append_retry_log(
            instance_dir,
            {"step": "dialogue_template", "event": "ok" if plan_result.ok else "failure", "errors": plan_result.errors, "warnings": plan_result.warnings},
        )
    if not plan_result.ok:
        status = "manual_review_required" if plan_result.source == "template" else "step_failed"
        state.update_instance(
            instance_id,
            status=status,
            current_step="dialogue_template" if plan_result.source == "template" else "dialogue_plan",
            dialogue_plan="fail",
            last_error="; ".join(plan_result.errors),
            failure_reason="template_dialogue_needs_revision_fact" if plan_result.source == "template" else "dialogue_plan_and_template_failed",
            dialogue_plan_llm_success=False,
            dialogue_plan_template_fallback_used=plan_result.source == "template",
            dialogue_plan_failure_reason=plan_result.failure_reason or "; ".join(plan_result.errors),
        )
        state.save()
        return
    if plan_result.source == "template":
        state.update_instance(instance_id, dialogue_plan="template", dialogue_plan_template_fallback_used=True)

    status = finalize_v2(
        instance_dir=instance_dir,
        instance_id=instance_id,
        capsule=capsule,
        dialogue_plan=plan_result.plan,
        dialogue_source="template" if plan_result.source == "template" else "llm",
        state=state,
        options=options,
    )
    if status not in {"accepted", "accepted_with_template_dialogue"}:
        state.update_instance(instance_id, suggested_fix="Inspect .build/semantic_capsule.json, .build/dialogue_plan.json, and quality_report.json.")
        state.save()
