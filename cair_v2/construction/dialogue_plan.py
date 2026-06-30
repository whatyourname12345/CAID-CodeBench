from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cair_v2.construction.sanitizer import SanitizerResult, sanitize_dialogue_plan
from cair_v2.construction.semantic_capsule import REVISION_FACT_TYPES
from cair_v2.llm.clients import DeepSeekClient
from cair_v2.llm.prompt_runner import PromptStepResult, run_prompt_step


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = PROJECT_ROOT / "cair_v2/prompts"

ALLOWED_DIALOGUE_OPERATIONS = {
    "reveal_vague_goal",
    "add_information",
    "refine",
    "correct",
    "reverse",
    "retract",
    "override",
    "obsolete",
    "discard",
    "introduce_conflict",
    "resolve_conflict",
    "reject",
    "add_regression_constraint",
    "add_negative_constraint",
    "confirm",
}

REVISION_OPERATIONS = {
    "correct",
    "reverse",
    "retract",
    "override",
    "obsolete",
    "discard",
    "introduce_conflict",
    "resolve_conflict",
    "reject",
}


@dataclass
class DialoguePlanResult:
    ok: bool
    plan: dict[str, Any] = field(default_factory=dict)
    status: str = "unknown"
    api_call_made: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    prompt_result: PromptStepResult | None = None
    source: str = "llm"


def exposed_semantic_context(capsule: dict[str, Any]) -> dict[str, Any]:
    units = capsule.get("fact_units") if isinstance(capsule.get("fact_units"), list) else []
    exposed_units = [
        {
            "unit_id": unit.get("unit_id"),
            "type": unit.get("type"),
            "text": unit.get("text"),
            "active_by_default": unit.get("active_by_default"),
        }
        for unit in units
        if isinstance(unit, dict) and unit.get("expose_to_user") is not False and unit.get("type") != "implementation_hint"
    ]
    return {
        "suitability": capsule.get("suitability"),
        "fact_units": exposed_units,
        "final_intent": capsule.get("final_intent"),
        "revision_support": capsule.get("revision_support"),
        "dialogue_guidance": capsule.get("dialogue_guidance"),
        "rules": {
            "turn_count": "4-6",
            "first_turn_max_chars": 90,
            "must_include_revision_operation": sorted(REVISION_OPERATIONS),
            "revision_turn_must_reference_revision_unit_ids": True,
            "do_not_reference_units_with_expose_to_user_false": True,
        },
    }


def validate_dialogue_plan(plan: dict[str, Any], capsule: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if plan.get("status") == "manual_review_required":
        reason = str(plan.get("reason") or "").strip() or "manual review requested by dialogue planner"
        errors.append(reason)
        return errors, warnings
    turns = plan.get("turns") if isinstance(plan.get("turns"), list) else []
    if not (4 <= len(turns) <= 6):
        errors.append("dialogue_plan.turns must contain 4-6 turns")
    exposed_ids = {
        str(unit.get("unit_id"))
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict) and unit.get("expose_to_user") is not False and unit.get("type") != "implementation_hint"
    }
    all_ids = {str(unit.get("unit_id")) for unit in capsule.get("fact_units", []) if isinstance(unit, dict)}
    unit_type_by_id = {
        str(unit.get("unit_id")): str(unit.get("type"))
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict) and unit.get("unit_id")
    }
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    has_revision_fact = revision_support.get("has_revision_fact") is True
    revision_unit_ids = {str(unit_id) for unit_id in revision_support.get("revision_unit_ids", []) if str(unit_id).strip()}
    operations: set[str] = set()
    revision_refs_ok = True
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            errors.append(f"dialogue_plan.turns[{index}] must be an object")
            continue
        operation = str(turn.get("operation") or "")
        operations.add(operation)
        if operation not in ALLOWED_DIALOGUE_OPERATIONS:
            errors.append(f"dialogue_plan.turns[{index}].operation is invalid: {operation}")
        utterance = str(turn.get("user_utterance") or "").strip()
        if not utterance:
            errors.append(f"dialogue_plan.turns[{index}].user_utterance is empty")
        if index == 0:
            if len(utterance) > 90:
                errors.append("dialogue_plan T1 must be <= 90 chars")
            if sum(token.lower() in utterance.lower() for token in ["expected", "root cause", "fix", "patch", "should"]) >= 2:
                errors.append("dialogue_plan T1 is too complete or benchmark-like")
        introduced = turn.get("introduced_units")
        if not isinstance(introduced, list):
            errors.append(f"dialogue_plan.turns[{index}].introduced_units must be a list")
            continue
        for unit_id in introduced:
            text_id = str(unit_id)
            if text_id not in all_ids:
                errors.append(f"dialogue_plan.turns[{index}] references unknown unit {text_id}")
            elif text_id not in exposed_ids:
                errors.append(f"dialogue_plan.turns[{index}] references non-exposable unit {text_id}")
        if operation in REVISION_OPERATIONS:
            if not has_revision_fact:
                revision_refs_ok = False
                errors.append(f"dialogue_plan.turns[{index}] contains revision operation but semantic_capsule.revision_support.has_revision_fact=false")
            matching_revision_ids = set(map(str, introduced)) & revision_unit_ids
            if not matching_revision_ids:
                revision_refs_ok = False
                errors.append(f"dialogue_plan.turns[{index}] revision operation must reference revision_support.revision_unit_ids")
            for unit_id in matching_revision_ids:
                if unit_type_by_id.get(unit_id) not in REVISION_FACT_TYPES:
                    revision_refs_ok = False
                    errors.append(f"dialogue_plan.turns[{index}] revision unit {unit_id} has invalid revision type {unit_type_by_id.get(unit_id)}")
    if has_revision_fact and not (operations & REVISION_OPERATIONS):
        errors.append("dialogue_plan must include at least one intent-revision operation")
    if not has_revision_fact and (operations & REVISION_OPERATIONS):
        errors.append("dialogue_plan must not include revision operation when revision_support.has_revision_fact=false")
    if has_revision_fact and not revision_refs_ok:
        warnings.append("dialogue_plan has revision operation but it is not properly bound to revision_support")
    return errors, warnings


def _archive_prompt_result(instance_dir: Path, result: PromptStepResult, label: str) -> None:
    raw_dir = instance_dir / ".build" / "raw_llm_outputs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for path in [result.raw_path, result.parsed_path]:
        if path and path.exists():
            shutil.copy2(path, raw_dir / f"{label}.{path.name.split('.', 1)[-1]}")


def write_dialogue_plan(instance_dir: Path, plan: dict[str, Any], *, source: str) -> None:
    build_dir = instance_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(plan)
    payload["_source"] = source
    (build_dir / "dialogue_plan.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_dialogue_plan(instance_dir: Path) -> dict[str, Any]:
    path = instance_dir / ".build" / "dialogue_plan.json"
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.pop("_source", None)
        return data
    return {"turns": []}


def run_dialogue_plan(
    instance_dir: Path,
    *,
    capsule: dict[str, Any],
    model: str,
    client: DeepSeekClient | None,
    force: bool = False,
    no_api: bool = False,
    dry_run: bool = False,
    cache_label: str = "dialogue_plan",
) -> DialoguePlanResult:
    cache_path = instance_dir / ".build" / "dialogue_plan.json"
    if not force and cache_path.exists():
        plan = load_dialogue_plan(instance_dir)
        errors, warnings = validate_dialogue_plan(plan, capsule)
        return DialoguePlanResult(ok=not errors, plan=plan, status="cached", warnings=warnings, errors=errors)

    result = run_prompt_step(
        instance_dir=instance_dir,
        step_name="dialogue_plan",
        cache_prefix=f"v2_02_{cache_label}",
        prompt_path=PROMPT_DIR / "dialogue_plan.md",
        context=exposed_semantic_context(capsule),
        client=client,
        model=model,
        temperature=0.0,
        max_tokens=1400,
        force=force,
        no_api=no_api or dry_run,
    )
    if result.status == "dry_run":
        return DialoguePlanResult(ok=True, status="dry_run", prompt_result=result, source="dry_run")
    if result.status != "ok" or not isinstance(result.parsed, dict):
        return DialoguePlanResult(
            ok=False,
            status=result.status,
            api_call_made=result.status != "cached",
            errors=[result.error or "dialogue_plan parse failed"],
            prompt_result=result,
        )
    if result.parsed.get("status") == "manual_review_required":
        return DialoguePlanResult(
            ok=False,
            plan=result.parsed,
            status="manual_review_required",
            api_call_made=result.status != "cached",
            errors=[str(result.parsed.get("reason") or "dialogue_plan requested manual review")],
            prompt_result=result,
            source=f"llm:{model}",
        )
    _archive_prompt_result(instance_dir, result, cache_label)
    sanitized: SanitizerResult = sanitize_dialogue_plan(result.parsed)
    plan = sanitized.data if isinstance(sanitized.data, dict) else {"turns": []}
    errors, warnings = validate_dialogue_plan(plan, capsule)
    errors = [*sanitized.hard_failures, *errors]
    warnings = [*sanitized.warnings, *warnings]
    if not errors:
        write_dialogue_plan(instance_dir, plan, source=f"llm:{model}")
    else:
        (instance_dir / ".build" / f"dialogue_plan_{cache_label}.errors.json").write_text(
            json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return DialoguePlanResult(
        ok=not errors,
        plan=plan,
        status="ok" if not errors else "quality_failed",
        api_call_made=True,
        warnings=warnings,
        errors=errors,
        prompt_result=result,
        source=f"llm:{model}",
    )
