from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cair_v2.construction.dialogue_plan import write_dialogue_plan
from cair_v2.construction.domains import domain_for_repo
from cair_v2.construction.instance_io import read_json, write_json
from cair_v2.construction.localization_gold import extract_localization_gold
from cair_v2.construction.sanitizer import sanitize_dialogue_plan
from cair_v2.staged.schemas import StagedConstructionResult, StageStepResult
from cair_v2.staged.step_runner import StagedStepRunner


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _source_payload(instance_dir: Path) -> dict[str, Any]:
    source_record = read_json(instance_dir / "source_record.json")
    source = source_record.get("source_swebench") if isinstance(source_record.get("source_swebench"), dict) else {}
    repo = str(source_record.get("repo") or "")
    return {
        "instance_id": source_record.get("instance_id"),
        "repo": repo,
        "domain": domain_for_repo(repo),
        "problem_statement": source.get("problem_statement") or "",
        "hints_text": source.get("hints_text") or "",
    }


def _exposed_fact_units(fact_extraction: dict[str, Any]) -> list[dict[str, Any]]:
    units = fact_extraction.get("fact_units") if isinstance(fact_extraction.get("fact_units"), list) else []
    return [
        unit
        for unit in units
        if isinstance(unit, dict)
        and unit.get("expose_to_user") is not False
        and unit.get("type") != "implementation_hint"
    ]


def _semantic_capsule(fact_extraction: dict[str, Any], intent_revision: dict[str, Any]) -> dict[str, Any]:
    support = intent_revision.get("revision_support") if isinstance(intent_revision.get("revision_support"), dict) else {}
    has_revision = support.get("has_revision_fact") is True and bool(_str_list(support.get("revision_unit_ids")))
    revision_type = str(support.get("revision_type") or "").strip()
    revision_types = [revision_type] if revision_type else []
    return {
        "suitability": {
            "is_cair_suitable": bool(has_revision),
            "risk_level": "low" if has_revision else "manual_review_required",
            "reason": (
                "Source facts contain supported intent-revision evidence."
                if has_revision
                else "No source-grounded revision fact was found."
            ),
        },
        "issue_summary": str(fact_extraction.get("issue_summary") or "").strip(),
        "fact_units": fact_extraction.get("fact_units") if isinstance(fact_extraction.get("fact_units"), list) else [],
        "revision_support": {
            "has_revision_fact": bool(has_revision),
            "revision_unit_ids": _str_list(support.get("revision_unit_ids")) if has_revision else [],
            "revision_types": revision_types if has_revision else [],
            "reason": str(support.get("reason") or "").strip(),
        },
        "dialogue_guidance": {
            "revision_units": _str_list(support.get("revision_unit_ids")) if has_revision else [],
        },
        "final_intent": intent_revision.get("final_intent") if isinstance(intent_revision.get("final_intent"), dict) else {},
        "oracle": intent_revision.get("oracle") if isinstance(intent_revision.get("oracle"), dict) else {},
    }


def _dialogue_plan(event_plan: dict[str, Any], realization: dict[str, Any]) -> dict[str, Any]:
    utterances = realization.get("utterances") if isinstance(realization.get("utterances"), list) else []
    utterance_by_id = {
        str(item.get("turn_id")): str(item.get("user_utterance") or "").strip()
        for item in utterances
        if isinstance(item, dict)
    }
    turns = []
    passthrough_fields = [
        "claim_status",
        "revises_turns",
        "revises_units",
        "deactivates_claims",
        "activates_claims",
        "active_after_turn",
        "inactive_after_turn",
        "must_be_resolved_later",
    ]
    for raw in (event_plan.get("turns") if isinstance(event_plan.get("turns"), list) else []):
        if not isinstance(raw, dict):
            continue
        turn = {
            "operation": str(raw.get("operation") or "").strip(),
            "user_utterance": utterance_by_id.get(str(raw.get("turn_id")), ""),
            "introduced_units": _str_list(raw.get("introduced_units")),
        }
        for field in passthrough_fields:
            value = raw.get(field)
            if isinstance(value, list):
                turn[field] = _str_list(value)
            elif value not in (None, ""):
                turn[field] = value
        turns.append(turn)
    return {"turns": turns}


def _realization_context(
    *,
    initial_report_plan: dict[str, Any],
    event_plan: dict[str, Any],
    fact_extraction: dict[str, Any],
    intent_revision: dict[str, Any],
) -> dict[str, Any]:
    units_by_id = {
        str(unit.get("unit_id")): unit
        for unit in fact_extraction.get("fact_units", [])
        if isinstance(unit, dict) and unit.get("unit_id")
    }
    turn_unit_facts = []
    for raw in (event_plan.get("turns") if isinstance(event_plan.get("turns"), list) else []):
        if not isinstance(raw, dict):
            continue
        introduced = _str_list(raw.get("introduced_units"))
        turn_unit_facts.append(
            {
                "turn_id": str(raw.get("turn_id") or ""),
                "operation": raw.get("operation"),
                "facts": [
                    {
                        "unit_id": unit_id,
                        "type": units_by_id.get(unit_id, {}).get("type"),
                        "text": units_by_id.get(unit_id, {}).get("text"),
                    }
                    for unit_id in introduced
                    if unit_id in units_by_id
                ],
            }
        )
    return {
        "initial_report_plan": initial_report_plan,
        "noisy_revision_event_plan": event_plan,
        "turn_unit_facts": turn_unit_facts,
        "final_intent_summary": intent_revision.get("final_intent"),
        "forbidden_terms": [
            "patch",
            "diff",
            "test",
            "tests",
            "benchmark",
            "gold",
            "oracle",
            "FAIL_TO_PASS",
            "PASS_TO_PASS",
            "hidden test",
            "reference patch",
            "implementation_hint",
        ],
    }


def _localization_summary(instance_dir: Path) -> dict[str, Any]:
    gold = extract_localization_gold(instance_dir)
    return {
        "status": "ready" if gold.files else "not_ready",
        "gold_files_count": len(gold.files),
        "gold_functions_count": len(gold.functions),
        "function_gold_available": bool(gold.functions),
        "source": gold.source,
        "warnings": gold.warnings,
    }


def _write_intermediate_debug(instance_dir: Path, updates: dict[str, Any]) -> None:
    build_dir = instance_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)
    path = build_dir / "intermediate_debug.json"
    current: dict[str, Any] = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update(updates)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _failed_result(
    *,
    status: str,
    failed_step: str,
    step_results: dict[str, StageStepResult],
    errors: list[str],
    semantic_capsule: dict[str, Any] | None = None,
    dialogue_plan: dict[str, Any] | None = None,
    semantic_review: dict[str, Any] | None = None,
) -> StagedConstructionResult:
    return StagedConstructionResult(
        ok=False,
        status=status,
        semantic_capsule=semantic_capsule or {},
        dialogue_plan=dialogue_plan or {},
        semantic_review=semantic_review or {},
        step_results=step_results,
        errors=errors,
        failed_step=failed_step,
    )


def run_staged_pipeline(
    *,
    instance_dir: Path,
    runner: StagedStepRunner,
) -> StagedConstructionResult:
    step_results: dict[str, StageStepResult] = {}
    source = _source_payload(instance_dir)

    fact_context = {
        "instance_id": source["instance_id"],
        "repo": source["repo"],
        "domain": source["domain"],
        "problem_statement": source["problem_statement"],
        "hints_text": source["hints_text"],
    }
    fact = runner.run_step("fact_extraction", fact_context)
    step_results["fact_extraction"] = fact
    if not fact.ok:
        return _failed_result(status="step_failed", failed_step="fact_extraction", step_results=step_results, errors=fact.errors)

    intent_context = {
        "issue_summary": fact.data.get("issue_summary"),
        "fact_units": fact.data.get("fact_units"),
        "fact_extraction": fact.data,
    }
    intent = runner.run_step("intent_revision", intent_context)
    step_results["intent_revision"] = intent
    if not intent.ok:
        return _failed_result(status="step_failed", failed_step="intent_revision", step_results=step_results, errors=intent.errors)

    capsule = _semantic_capsule(fact.data, intent.data)
    write_json(instance_dir / ".build" / "semantic_capsule.json", capsule)
    if capsule.get("revision_support", {}).get("has_revision_fact") is not True:
        _write_intermediate_debug(
            instance_dir,
            {
                "staged_status": "manual_review_required",
                "manual_review_reason": "No source-grounded revision fact supports CAIR intent-revision sharding.",
            },
        )
        return _failed_result(
            status="manual_review_required",
            failed_step="intent_revision",
            step_results=step_results,
            errors=["No source-grounded revision fact supports CAIR intent-revision sharding."],
            semantic_capsule=capsule,
        )

    initial_context = {
        "fact_units": _exposed_fact_units(fact.data),
        "final_intent": intent.data.get("final_intent"),
        "revision_support": intent.data.get("revision_support"),
        "fact_extraction": fact.data,
        "intent_revision": intent.data,
    }
    initial_plan = runner.run_step("initial_report_plan", initial_context)
    step_results["initial_report_plan"] = initial_plan
    if not initial_plan.ok:
        return _failed_result(
            status="step_failed",
            failed_step="initial_report_plan",
            step_results=step_results,
            errors=initial_plan.errors,
            semantic_capsule=capsule,
        )
    if initial_plan.data.get("status") == "manual_review_required":
        reason = str(initial_plan.data.get("reason") or "initial_report_plan requested manual review")
        _write_intermediate_debug(
            instance_dir,
            {
                "staged_status": "manual_review_required",
                "review_stage": "initial_report_plan",
                "manual_review_reason": reason,
                "fact_extraction": fact.data,
                "intent_revision": intent.data,
                "initial_report_plan": initial_plan.data,
            },
        )
        return _failed_result(
            status="manual_review_required",
            failed_step="initial_report_plan",
            step_results=step_results,
            errors=[reason],
            semantic_capsule=capsule,
        )

    event_context = {
        "fact_units": _exposed_fact_units(fact.data),
        "final_intent": intent.data.get("final_intent"),
        "revision_support": intent.data.get("revision_support"),
        "initial_report_plan": initial_plan.data,
        "fact_extraction": fact.data,
        "intent_revision": intent.data,
    }
    event_plan = runner.run_step("noisy_revision_event_plan", event_context)
    step_results["noisy_revision_event_plan"] = event_plan
    if not event_plan.ok:
        return _failed_result(
            status="step_failed",
            failed_step="noisy_revision_event_plan",
            step_results=step_results,
            errors=event_plan.errors,
            semantic_capsule=capsule,
        )
    if event_plan.data.get("status") == "manual_review_required":
        reason = str(event_plan.data.get("reason") or "noisy_revision_event_plan requested manual review")
        _write_intermediate_debug(
            instance_dir,
            {
                "staged_status": "manual_review_required",
                "review_stage": "noisy_revision_event_plan",
                "manual_review_reason": reason,
                "fact_extraction": fact.data,
                "intent_revision": intent.data,
                "initial_report_plan": initial_plan.data,
                "noisy_revision_event_plan": event_plan.data,
            },
        )
        return _failed_result(
            status="manual_review_required",
            failed_step="noisy_revision_event_plan",
            step_results=step_results,
            errors=[reason],
            semantic_capsule=capsule,
        )

    utterance_context = _realization_context(
        initial_report_plan=initial_plan.data,
        event_plan=event_plan.data,
        fact_extraction=fact.data,
        intent_revision=intent.data,
    )
    utterance_context.update(
        {
            "fact_extraction": fact.data,
            "intent_revision": intent.data,
        }
    )
    utterance = runner.run_step("realistic_utterance_realization", utterance_context)
    step_results["realistic_utterance_realization"] = utterance
    if not utterance.ok:
        return _failed_result(
            status="step_failed",
            failed_step="realistic_utterance_realization",
            step_results=step_results,
            errors=utterance.errors,
            semantic_capsule=capsule,
        )

    plan = _dialogue_plan(event_plan.data, utterance.data)
    sanitized = sanitize_dialogue_plan(plan)
    plan = sanitized.data if isinstance(sanitized.data, dict) else plan
    write_dialogue_plan(instance_dir, plan, source="llm_staged")

    review_context = {
        "issue_summary": fact.data.get("issue_summary"),
        "fact_units": fact.data.get("fact_units"),
        "final_intent": intent.data.get("final_intent"),
        "revision_support": intent.data.get("revision_support"),
        "dialogue": plan,
        "oracle": intent.data.get("oracle"),
        "localization_summary": _localization_summary(instance_dir),
        "fact_extraction": fact.data,
        "intent_revision": intent.data,
        "initial_report_plan": initial_plan.data,
        "noisy_revision_event_plan": event_plan.data,
        "realistic_utterance_realization": utterance.data,
    }
    review = runner.run_step("semantic_reviewer", review_context)
    step_results["semantic_reviewer"] = review
    if not review.ok:
        return _failed_result(
            status="step_failed",
            failed_step="semantic_reviewer",
            step_results=step_results,
            errors=review.errors,
            semantic_capsule=capsule,
            dialogue_plan=plan,
            semantic_review=review.data,
        )

    decision = str(review.data.get("decision") or "")
    status = "accepted" if decision == "accept" else decision or "manual_review_required"
    _write_intermediate_debug(
        instance_dir,
        {
            "staged_status": status,
            "fact_extraction": fact.data,
            "intent_revision": intent.data,
            "initial_report_plan": initial_plan.data,
            "noisy_revision_event_plan": event_plan.data,
            "realistic_utterance_realization": utterance.data,
            "semantic_review": review.data,
        },
    )
    return StagedConstructionResult(
        ok=decision == "accept",
        status=status,
        semantic_capsule=capsule,
        dialogue_plan=plan,
        semantic_review=review.data,
        step_results=step_results,
        errors=[] if decision == "accept" else _str_list(review.data.get("issues")),
        warnings=[*sanitized.warnings],
        dialogue_source="llm_staged",
        failed_step=None if decision == "accept" else "semantic_reviewer",
    )
