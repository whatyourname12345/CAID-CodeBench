from __future__ import annotations

import csv
import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cair_v2.batch.batch_config import BatchConfig
from cair_v2.batch.batch_runner import write_manual_review_queue
from cair_v2.batch.batch_state import BatchState
from cair_v2.batch.candidate_loader import load_candidate_rows
from cair_v2.batch.quality_gate_v2 import evaluate_v2_instance_dir, evaluate_v2_quality
from cair_v2.construction.build_instance_skeleton import patch_metadata_from_candidate, source_record_from_candidate
from cair_v2.construction.cair_compiler_v2 import build_cair_instance_v2, write_intermediate_debug, write_v2_outputs
from cair_v2.construction.dialogue_plan import DialoguePlanResult, run_dialogue_plan, validate_dialogue_plan, write_dialogue_plan
from cair_v2.construction.dialogue_template import build_template_dialogue_plan
from cair_v2.construction.instance_io import write_json
from cair_v2.construction.load_swebench_record import row_to_clean_dict
from cair_v2.construction.patch_summarizer import summarize_patch_record
from cair_v2.construction.semantic_capsule import run_semantic_capsule
from cair_v2.construction.sanitizer import sanitize_dialogue_plan
from cair_v2.llm.clients import DeepSeekClient, MissingAPIKeyError


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


def select_rows_for_v2(input_file: Path, *, limit: int | None, include_rejected: bool) -> list[dict[str, Any]]:
    rows = load_candidate_rows(input_file)
    if not include_rejected:
        rows = [row for row in rows if str(row.get("manual_override_label") or "") != "REJECT_OR_DOWNRANK"]
    return rows[:limit] if limit is not None else rows


def create_or_load_instance_v2(record: dict[str, Any], output_dir: Path, *, force: bool) -> Path:
    clean = row_to_clean_dict(record)
    instance_id = str(clean["instance_id"])
    instance_dir = output_dir / instance_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    build_dir = instance_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)

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


def _api_budget_available(state: BatchState, options: BatchV2Options) -> bool:
    return options.no_api or options.dry_run or state.api_calls < options.max_api_calls


def _count_api_call(state: BatchState, instance_id: str, result_api_call_made: bool, options: BatchV2Options) -> None:
    if result_api_call_made and not (options.no_api or options.dry_run):
        state.add_instance_api_call(instance_id)


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
    )


def _has_revision_support(capsule: dict[str, Any]) -> bool:
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    return revision_support.get("has_revision_fact") is True and bool(revision_support.get("revision_unit_ids"))


def finalize_v2(
    *,
    instance_dir: Path,
    instance_id: str,
    capsule: dict[str, Any],
    dialogue_plan: dict[str, Any],
    dialogue_source: str,
    state: BatchState,
    options: BatchV2Options,
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
    )
    quality = evaluate_v2_quality(compact=compact, semantic_capsule=capsule, dialogue_plan=dialogue_plan)
    if quality.get("passed"):
        status = pre_status
    else:
        status = str(quality.get("status") or "manual_review_required")
        if status == "accepted":
            status = "manual_review_required"
    compact.setdefault("metadata", {})["construction_status"] = status
    compact["metadata"]["quality_gate_passed"] = bool(quality.get("passed"))
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
    )
    state.save()
    return status


def run_one_instance_v2(
    *,
    record: dict[str, Any],
    output_dir: Path,
    state: BatchState,
    options: BatchV2Options,
) -> None:
    instance_id = str(record.get("instance_id"))
    repo = str(record.get("repo") or "")
    instance_dir = create_or_load_instance_v2(record, output_dir, force=options.force)
    state.ensure_instance(instance_id, repo=repo, path=rel(instance_dir))
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
            result = run_semantic_capsule(
                instance_dir,
                model=model,
                client=_client(model, options),
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
            _count_api_call(state, instance_id, result.api_call_made, options)
            if result.ok:
                semantic = result
                append_retry_log(instance_dir, {"step": "semantic_capsule", "event": "ok", "label": label, "warnings": result.warnings})
                if label.endswith("_pro"):
                    state.add_escalation(instance_id, "semantic_capsule", options.model_generator, model, "Flash semantic capsule failed; retried with Pro")
                break
            append_retry_log(
                instance_dir,
                {"step": "semantic_capsule", "event": "quality_failure", "label": label, "errors": result.errors, "warnings": result.warnings},
            )
            semantic = result
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
            result = run_dialogue_plan(
                instance_dir,
                capsule=capsule,
                model=model,
                client=_client(model, options),
                force=True,
                no_api=options.no_api,
                dry_run=options.dry_run,
                cache_label=label,
            )
        except Exception as exc:
            state.add_instance_api_call(instance_id)
            append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "failure", "error": str(exc), "label": label})
            result = DialoguePlanResult(ok=False, status="exception", api_call_made=False, errors=[str(exc)], source=f"llm:{model}")
        _count_api_call(state, instance_id, result.api_call_made, options)
        if result.ok:
            plan_result = result
            append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "ok", "label": label, "warnings": result.warnings})
            break
        if result.status == "manual_review_required":
            plan_result = result
            append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "manual_review_required", "label": label, "errors": result.errors})
            break
        append_retry_log(instance_dir, {"step": "dialogue_plan", "model": model, "event": "quality_failure", "label": label, "errors": result.errors})

    if plan_result is None or not plan_result.ok:
        if plan_result is not None and plan_result.status == "manual_review_required":
            state.update_instance(
                instance_id,
                status="manual_review_required",
                current_step="dialogue_plan",
                last_error="; ".join(plan_result.errors),
                failure_reason="no_supported_revision_fact",
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
                last_error="semantic_capsule has no supported revision fact; template fallback skipped",
                failure_reason="no_supported_revision_fact",
                suggested_fix="Manual review can decide whether this issue should be used as underspecification-only or excluded from CAIR revision seeds.",
            )
            state.save()
            return
        state.add_escalation(instance_id, "dialogue_plan", options.model_critical, "template", "Pro and Flash dialogue plan failed or budget exhausted")
        plan_result = _template_dialogue_result(instance_dir, capsule)
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
            last_error="; ".join(plan_result.errors),
            failure_reason="template_dialogue_needs_revision_fact" if plan_result.source == "template" else "dialogue_plan_and_template_failed",
        )
        state.save()
        return

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


def run_batch_v2(options: BatchV2Options, config: BatchConfig) -> BatchState:
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    state = BatchState.load_or_create(
        output_dir / "batch_state.json",
        batch_id=output_dir.name,
        input_file=options.input_file,
        output_dir=output_dir,
        model_generator=options.model_generator,
        model_critical=options.model_critical,
        model_reviewer=options.model_reviewer,
    )
    state.data["pipeline_version"] = "v2_minimal_robust"
    rows = select_rows_for_v2(options.input_file, limit=options.limit, include_rejected=options.include_rejected)
    for record in rows:
        if state.api_calls >= options.max_api_calls and not (options.dry_run or options.no_api):
            break
        run_one_instance_v2(record=record, output_dir=output_dir, state=state, options=options)
        write_manual_review_queue(output_dir, state)
        if config.defaults.sleep_between_instances:
            time.sleep(config.defaults.sleep_between_instances)
    write_manual_review_queue(output_dir, state)
    state.save()
    return state
