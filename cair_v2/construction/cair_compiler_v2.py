from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cair_v2.construction.domains import domain_for_repo
from cair_v2.construction.instance_io import read_json, write_json
from cair_v2.construction.localization_checkpoint import build_localization_checkpoint
from cair_v2.construction.localization_gold import LocalizationGold, extract_localization_gold
from cair_v2.construction.sanitizer import SanitizerResult, sanitize_compact_instance


REVISION_OPERATIONS = {
    "correct_previous_claim",
    "retract_previous_claim",
    "replace_previous_claim",
    "resolve_conflict",
    "narrow_scope",
    "broaden_scope",
    "add_regression_constraint",
    "confirm_final_active_intent",
}

SPECULATIVE_OPERATIONS = {
    "speculative_hypothesis",
    "mistaken_clarification",
    "incorrect_reproduction_detail",
}

ACTIVE_OPERATIONS = {
    "initial_imperfect_report",
    "add_detail",
    "add_missing_detail",
    "add_reproduction_detail",
    "narrow_scope",
    "broaden_scope",
    "confirm_final_active_intent",
}

ACTIVE_UNIT_TYPES = {
    "symptom",
    "observed_behavior",
    "expected_behavior",
    "reproduction",
    "error_message",
    "affected_component",
    "active_constraint",
    "negative_constraint",
    "regression_expectation",
    "boundary_case",
    "acceptance_signal",
    "ambiguity_or_correction",
    "conflict_or_tension",
}

OBSOLETE_UNIT_TYPES = {"obsolete_candidate", "rejected_solution", "non_goal", "design_suggestion_non_goal", "workaround_to_reject"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _str_list(value: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in _list(value):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _sparse_delta(delta: dict[str, Any]) -> dict[str, Any]:
    return {key: values for key, values in delta.items() if isinstance(values, list) and values}


def _unit_map(capsule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    units = capsule.get("fact_units") if isinstance(capsule.get("fact_units"), list) else []
    return {str(unit.get("unit_id")): unit for unit in units if isinstance(unit, dict) and unit.get("unit_id")}


def _safe_fact_units(capsule: dict[str, Any]) -> list[dict[str, Any]]:
    units = capsule.get("fact_units") if isinstance(capsule.get("fact_units"), list) else []
    safe_units: list[dict[str, Any]] = []
    for raw in units:
        if not isinstance(raw, dict):
            continue
        safe_units.append(
            {
                "unit_id": str(raw.get("unit_id") or "").strip(),
                "type": str(raw.get("type") or "").strip(),
                "text": str(raw.get("text") or "").strip(),
                "source": str(raw.get("source") or "").strip(),
                "active_by_default": bool(raw.get("active_by_default")),
                "expose_to_user": bool(raw.get("expose_to_user")),
                "risk": None if raw.get("risk") in (None, "", "null") else str(raw.get("risk")).strip(),
            }
        )
    return [unit for unit in safe_units if unit["unit_id"] and unit["text"]]


def normalize_semantic_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    suitability = capsule.get("suitability") if isinstance(capsule.get("suitability"), dict) else {}
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    guidance = capsule.get("dialogue_guidance") if isinstance(capsule.get("dialogue_guidance"), dict) else {}
    return {
        "suitability": {
            "is_cair_suitable": suitability.get("is_cair_suitable") is True,
            "risk_level": str(suitability.get("risk_level") or "").strip(),
            "reason": str(suitability.get("reason") or "").strip(),
        },
        "fact_units": _safe_fact_units(capsule),
        "revision_support": {
            "has_revision_fact": revision_support.get("has_revision_fact") is True,
            "revision_unit_ids": _str_list(revision_support.get("revision_unit_ids")),
            "revision_types": _str_list(revision_support.get("revision_types")),
            "reason": str(revision_support.get("reason") or "").strip(),
        },
        "dialogue_guidance": {
            key: _str_list(value)
            for key, value in guidance.items()
            if key in {"vague_symptom_units", "context_units", "revision_units", "regression_units"}
        },
    }


def _introduced_units(turn: dict[str, Any], units_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for unit_id in _str_list(turn.get("introduced_units")):
        unit = units_by_id.get(unit_id)
        if unit:
            result.append(unit)
    return result


def _unit_texts(units: list[dict[str, Any]], allowed_types: set[str] | None = None) -> list[str]:
    values: list[str] = []
    for unit in units:
        if unit.get("expose_to_user") is False or unit.get("type") == "implementation_hint":
            continue
        if allowed_types and unit.get("type") not in allowed_types:
            continue
        text = str(unit.get("text") or "").strip()
        if text:
            values.append(_public_unit_text(text))
    return _str_list(values)


def _public_unit_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(
        r"\bthen\s+call\s+[A-Za-z_][A-Za-z0-9_.]*\([^)]*\)",
        "then trigger that case",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bcall\s+[A-Za-z_][A-Za-z0-9_.]*\([^)]*\)",
        "trigger that case",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def compile_state_delta(turn: dict[str, Any], introduced: list[dict[str, Any]], final_intent: dict[str, Any]) -> dict[str, Any]:
    operation = str(turn.get("operation") or "")
    claim_status = str(turn.get("claim_status") or "")
    explicit_deactivates = _str_list(turn.get("deactivates_claims"))
    explicit_activates = _str_list(turn.get("activates_claims"))
    delta: dict[str, Any] = {
        "add_active_goals": [],
        "add_constraints": [],
        "add_regression_expectations": [],
        "add_forbidden_actions": [],
        "deactivate_goals": [],
        "deactivate_assumptions": [],
        "replace_assumptions": [],
        "mark_claims_speculative": [],
        "mark_claims_mistaken": [],
        "resolve_conflicts": [],
    }
    active_texts = _unit_texts(introduced, ACTIVE_UNIT_TYPES)
    obsolete_texts = _unit_texts(introduced, OBSOLETE_UNIT_TYPES)
    constraint_texts = _unit_texts(introduced, {"active_constraint", "negative_constraint", "conflict_or_tension", "ambiguity_or_correction"})
    regression_texts = _unit_texts(introduced, {"regression_expectation"})

    if operation in ACTIVE_OPERATIONS:
        delta["add_active_goals"] = active_texts
    if operation == "add_regression_constraint":
        delta["add_regression_expectations"] = regression_texts or active_texts
        delta["add_constraints"] = constraint_texts
    if operation in {"speculative_hypothesis"} or claim_status == "speculative":
        delta["mark_claims_speculative"] = active_texts or explicit_activates
    if operation in {"mistaken_clarification", "incorrect_reproduction_detail"} or claim_status == "mistaken":
        delta["mark_claims_mistaken"] = active_texts or explicit_activates
    if operation in {"correct_previous_claim", "retract_previous_claim", "replace_previous_claim"}:
        delta["deactivate_assumptions"] = explicit_deactivates or obsolete_texts or constraint_texts
        delta["add_active_goals"] = explicit_activates or active_texts
        if operation == "replace_previous_claim" and (delta["deactivate_assumptions"] or delta["add_active_goals"]):
            old_values = delta["deactivate_assumptions"] or ["previous claim"]
            new_values = delta["add_active_goals"] or explicit_activates or ["corrected claim"]
            delta["replace_assumptions"] = [
                {
                    "old": old_values[0],
                    "new": new_values[0],
                    "reason": "User replaced an earlier claim during issue refinement.",
                }
            ]
    elif operation == "resolve_conflict":
        delta["resolve_conflicts"] = explicit_deactivates + explicit_activates or constraint_texts
        delta["deactivate_assumptions"] = explicit_deactivates or obsolete_texts
        delta["add_active_goals"] = explicit_activates or active_texts
    if operation == "narrow_scope":
        delta["add_constraints"] = constraint_texts or active_texts
        delta["deactivate_goals"] = explicit_deactivates
    if operation == "broaden_scope":
        delta["add_active_goals"] = explicit_activates or active_texts
    if "negative_constraint" in {str(unit.get("type") or "") for unit in introduced}:
        delta["add_forbidden_actions"] = _unit_texts(introduced, {"negative_constraint"})
    if obsolete_texts and operation not in {"correct_previous_claim", "retract_previous_claim", "replace_previous_claim", "resolve_conflict"}:
        delta["deactivate_assumptions"] = obsolete_texts
    if operation == "confirm_final_active_intent" and not any(delta.values()):
        objective = str(final_intent.get("objective") or "").strip()
        if objective:
            delta["add_active_goals"] = [objective]
    return _sparse_delta(delta)


def compile_dialogue_turns(capsule: dict[str, Any], dialogue_plan: dict[str, Any]) -> list[dict[str, Any]]:
    units_by_id = _unit_map(capsule)
    final_intent = capsule.get("final_intent") if isinstance(capsule.get("final_intent"), dict) else {}
    turns = dialogue_plan.get("turns") if isinstance(dialogue_plan.get("turns"), list) else []
    compiled: list[dict[str, Any]] = []
    for index, raw in enumerate(turns, start=1):
        if not isinstance(raw, dict):
            continue
        introduced_ids = [unit_id for unit_id in _str_list(raw.get("introduced_units")) if unit_id in units_by_id]
        introduced = _introduced_units(raw, units_by_id)
        turn = {
            "turn_id": f"T{index}",
            "operation": str(raw.get("operation") or ""),
            "user_utterance": str(raw.get("user_utterance") or "").strip(),
            "introduced_units": introduced_ids,
            "claim_status": str(raw.get("claim_status") or "").strip(),
            "revises_turns": _str_list(raw.get("revises_turns")),
            "revises_units": _str_list(raw.get("revises_units")),
            "deactivates_claims": _str_list(raw.get("deactivates_claims")),
            "activates_claims": _str_list(raw.get("activates_claims")),
            "state_delta": compile_state_delta(raw, introduced, final_intent),
        }
        compiled.append(turn)
    return compiled


def build_final_issue_prompt(final_intent: dict[str, Any]) -> str:
    lines = [
        "Please implement the following final active issue intent.",
        "",
        f"Objective: {final_intent.get('objective', '')}",
    ]
    must = _str_list(final_intent.get("must_satisfy"))
    if must:
        lines.extend(["", "Must satisfy:"])
        lines.extend(f"- {item}" for item in must)
    regressions = _str_list(final_intent.get("regression_expectations"))
    if regressions:
        lines.extend(["", "Preserve:"])
        lines.extend(f"- {item}" for item in regressions)
    return "\n".join(lines).strip()


def build_concat_dialogue_prompt(turns: list[dict[str, Any]], final_intent: dict[str, Any]) -> str:
    lines = [
        "The user may add, revise, contradict, retract, or correct earlier claims.",
        "Infer the final active intent from the whole conversation.",
        "Do not implement withdrawn, obsolete, unresolved speculative, mistaken, or rejected assumptions.",
        "",
    ]
    for turn in turns:
        lines.append(f"{turn.get('turn_id')}: {turn.get('user_utterance')}")
    inactive = _str_list(final_intent.get("must_not_satisfy")) + _str_list(final_intent.get("non_goals"))
    if inactive:
        lines.extend(["", "Inactive or rejected context:"])
        lines.extend(f"- {item}" for item in inactive)
    return "\n".join(lines).strip()


def build_recap_prompt(final_intent: dict[str, Any]) -> str:
    lines = [
        "Before implementing, recap the final active claims and separate withdrawn, mistaken, or speculative claims.",
        "Only implement the final active intent; do not implement intermediate guesses that were withdrawn or left unresolved.",
        "",
        "Active objective:",
        str(final_intent.get("objective") or ""),
    ]
    if final_intent.get("must_satisfy"):
        lines.extend(["", "Must satisfy:"])
        lines.extend(f"- {item}" for item in _str_list(final_intent.get("must_satisfy")))
    inactive = _str_list(final_intent.get("must_not_satisfy")) + _str_list(final_intent.get("non_goals"))
    if inactive:
        lines.extend(["", "Inactive or rejected:"])
        lines.extend(f"- {item}" for item in inactive)
    return "\n".join(lines).strip()


def build_oracle_prompt(final_intent: dict[str, Any], oracle: dict[str, Any]) -> str:
    must = _str_list(oracle.get("must_satisfy")) or _str_list(final_intent.get("must_satisfy"))
    must_not = _str_list(oracle.get("must_not_satisfy")) or _str_list(final_intent.get("must_not_satisfy"))
    lines = [
        "Judge whether a proposed patch satisfies the final active CAIR intent.",
        "",
        f"Final active intent: {final_intent.get('objective', '')}",
        "",
        "Must satisfy:",
        *[f"- {item}" for item in must],
    ]
    if must_not:
        lines.extend(["", "Must not satisfy / must not keep active:"])
        lines.extend(f"- {item}" for item in must_not)
    return "\n".join(lines).strip()


def normalize_final_intent(capsule: dict[str, Any]) -> dict[str, Any]:
    raw = capsule.get("final_intent") if isinstance(capsule.get("final_intent"), dict) else {}
    return {
        "objective": str(raw.get("objective") or "").strip(),
        "must_satisfy": _str_list(raw.get("must_satisfy")),
        "must_not_satisfy": _str_list(raw.get("must_not_satisfy")),
        "non_goals": _str_list(raw.get("non_goals")),
        "regression_expectations": _str_list(raw.get("regression_expectations")),
    }


def normalize_oracle(capsule: dict[str, Any], final_intent: dict[str, Any]) -> dict[str, Any]:
    raw = capsule.get("oracle") if isinstance(capsule.get("oracle"), dict) else {}
    return {
        "must_satisfy": _str_list(raw.get("must_satisfy")) or _str_list(final_intent.get("must_satisfy")),
        "must_not_satisfy": _str_list(raw.get("must_not_satisfy")) or _str_list(final_intent.get("must_not_satisfy")),
        "obsolete_intent_checks": _str_list(raw.get("obsolete_intent_checks")) or _str_list(final_intent.get("non_goals")),
        "regression_checks": _str_list(raw.get("regression_checks")) or _str_list(final_intent.get("regression_expectations")),
        "forbidden_checks": _str_list(raw.get("forbidden_checks")),
        "clarification_checks": _str_list(raw.get("clarification_checks")),
    }


def build_evaluation_modes(turns: list[dict[str, Any]], final_intent: dict[str, Any], oracle: dict[str, Any], localization_prompt: str) -> dict[str, Any]:
    return {
        "final_issue_prompt": build_final_issue_prompt(final_intent),
        "concat_dialogue_prompt": build_concat_dialogue_prompt(turns, final_intent),
        "multi_turn_cair_script": [
            {"turn_id": turn.get("turn_id"), "user_utterance": turn.get("user_utterance")}
            for turn in turns
        ],
        "recap_cair_prompt": build_recap_prompt(final_intent),
        "oracle_intent_prompt": build_oracle_prompt(final_intent, oracle),
        "localization_prompt": localization_prompt,
    }


def write_intermediate_debug(instance_dir: Path, updates: dict[str, Any]) -> None:
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


def build_cair_instance_v2(
    instance_dir: Path,
    *,
    semantic_capsule: dict[str, Any],
    dialogue_plan: dict[str, Any],
    generator_model: str,
    critical_model: str,
    reviewer_model: str,
    construction_status: str,
    quality_gate_passed: bool,
    dialogue_source: str,
    localization_gold: LocalizationGold | None = None,
    pipeline_version: str = "v2_noisy_refinement",
    model_config_summary: dict[str, Any] | None = None,
    semantic_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_record = read_json(instance_dir / "source_record.json")
    final_intent = normalize_final_intent(semantic_capsule)
    compact_semantic_capsule = normalize_semantic_capsule(semantic_capsule)
    oracle = normalize_oracle(semantic_capsule, final_intent)
    turns = compile_dialogue_turns(semantic_capsule, dialogue_plan)
    localization_gold = localization_gold or extract_localization_gold(instance_dir)
    localization_checkpoint = build_localization_checkpoint(localization_gold)
    evaluation_modes = build_evaluation_modes(turns, final_intent, oracle, localization_checkpoint["prompt"])
    review = semantic_review if isinstance(semantic_review, dict) else {}
    is_noisy = pipeline_version == "v2_noisy_refinement"
    compact = {
        "instance_id": source_record.get("instance_id"),
        "repo": source_record.get("repo"),
        "domain": domain_for_repo(str(source_record.get("repo") or "")),
        "source_name": source_record.get("source_name"),
        "base_commit": source_record.get("base_commit"),
        "semantic_capsule": compact_semantic_capsule,
        "final_intent": final_intent,
        "dialogue": {
            "turns": turns,
        },
        "evaluation_modes": evaluation_modes,
        "localization_checkpoint": localization_checkpoint,
        "oracle": oracle,
        "metadata": {
            "pipeline_version": pipeline_version,
            "dialogue_scenario": "noisy_issue_refinement" if is_noisy else "deprecated_minimal_robust",
            "old_progressive_disclosure_pattern": bool(review.get("old_progressive_disclosure_pattern", False)),
            "unresolved_wrong_claims": int(review.get("unresolved_wrong_claims", 0) or 0),
            "scenario_fit": str(review.get("scenario_fit") or ("pass" if is_noisy else "deprecated")),
            "generator_model": generator_model,
            "critical_model": critical_model,
            "reviewer_model": reviewer_model,
            "dialogue_source": dialogue_source,
            "construction_status": construction_status,
            "quality_gate_passed": quality_gate_passed,
            "model_config_summary": model_config_summary or {},
            "golden_instance": source_record.get("instance_id") == "django__django-14011",
            "created_at": utc_now(),
        },
    }
    sanitized: SanitizerResult = sanitize_compact_instance(compact)
    result = sanitized.data if isinstance(sanitized.data, dict) else compact
    write_intermediate_debug(
        instance_dir,
        {
            "semantic_capsule": semantic_capsule,
            "dialogue_plan": dialogue_plan,
            "compiled_dialogue_turns": turns,
            "localization_gold_warnings": localization_gold.warnings,
            "localization_gold_source": localization_gold.source,
            "compiler_sanitizer_warnings": sanitized.warnings,
            "compiler_sanitizer_hard_failures": sanitized.hard_failures,
        },
    )
    return result


def write_v2_outputs(instance_dir: Path, compact: dict[str, Any], quality_report: dict[str, Any]) -> None:
    write_json(instance_dir / "cair_instance.json", compact)
    write_json(instance_dir / "quality_report.json", quality_report)
    readme = f"""# {compact.get('instance_id')}

This is a CAIR pipeline v2 compact instance.

Formal files:

- `cair_instance.json`: compact benchmark instance consumed by downstream evaluators.
- `quality_report.json`: local construction-time quality gate result.
- `source_record.json`: public source metadata for traceability; it excludes private test lists and reference patches.
- `README.md`: this summary.

Debug-only files live under `.build/` and are not part of release exports.

Construction status: `{compact.get('metadata', {}).get('construction_status')}`

Dialogue source: `{compact.get('metadata', {}).get('dialogue_source')}`
"""
    (instance_dir / "README.md").write_text(readme, encoding="utf-8")
