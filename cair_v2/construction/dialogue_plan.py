from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cair_v2.construction.sanitizer import SanitizerResult, sanitize_dialogue_plan
from cair_v2.construction.semantic_capsule import REVISION_FACT_TYPES
from cair_v2.construction.dialogue_template import (
    _confirm_utterance,
    _context_utterance,
    _refine_utterance,
    _regression_utterance,
    _revision_utterance,
    _units,
    _vague_t1,
)
from cair_v2.llm.clients import DeepSeekClient
from cair_v2.llm.json_utils import parse_model_output, to_yaml_text
from cair_v2.llm.prompt_runner import PromptStepResult, ensure_cache_dir, run_prompt_step


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

T1_FORBIDDEN_RE = re.compile(
    r"\b(expected|root cause|fix|patch|implementation|should|incorrectly)\b|"
    r"\b[A-Za-z]+_[A-Za-z0-9_]*\b|"
    r"\bseems off\.?$",
    re.IGNORECASE,
)

STAGED_GENERIC_T1_RE = re.compile(
    r"confusing failure around this behavior|one specific behavior looks wrong|"
    r"something (is|seems) wrong|this behavior (is|seems)|not working$|"
    r"having an issue with [a-z ]+\.?$",
    re.IGNORECASE,
)

TOKEN_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "when",
    "that",
    "this",
    "should",
    "must",
    "from",
    "into",
    "without",
    "behavior",
    "expected",
    "observed",
    "current",
    "existing",
    "please",
    "seems",
    "issue",
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
    api_calls_made: int = 0
    repaired: bool = False
    quality_retry_used: bool = False
    failure_reason: str | None = None
    error_type: str | None = None
    repair_success: bool = False
    retry_success: bool = False
    fallback_used: bool = False
    model_used: str | None = None
    call_results: list[Any] = field(default_factory=list)
    step_results: dict[str, PromptStepResult] = field(default_factory=dict)


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
        "final_intent": capsule.get("final_intent"),
        "fact_units": exposed_units,
        "revision_support": capsule.get("revision_support"),
        "dialogue_guidance": capsule.get("dialogue_guidance"),
    }


def staged_skeleton_context(capsule: dict[str, Any]) -> dict[str, Any]:
    exposed = exposed_semantic_context(capsule)
    final_intent = capsule.get("final_intent") if isinstance(capsule.get("final_intent"), dict) else {}
    return {
        "final_intent_objective": str(final_intent.get("objective") or "").strip(),
        "fact_units": [
            {
                "unit_id": unit.get("unit_id"),
                "type": unit.get("type"),
                "text": unit.get("text"),
            }
            for unit in exposed["fact_units"]
        ],
        "revision_support": exposed.get("revision_support"),
    }


def _unit_map(capsule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(unit.get("unit_id")): unit
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict) and unit.get("unit_id")
    }


def _id_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _tokens(text: Any) -> set[str]:
    aliases = {
        "cloning": "clone",
        "cloned": "clone",
        "fails": "fail",
        "failing": "fail",
        "failed": "fail",
        "warnings": "warning",
        "arguments": "argument",
        "abbreviations": "abbreviation",
        "verbose": "verbosity",
        "layout": "alignment",
        "aligned": "alignment",
        "align": "alignment",
        "class": "type",
        "classes": "type",
    }
    raw = re.sub(r"[-_/().:=]+", " ", str(text or "").lower())
    result: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", raw):
        token = aliases.get(token, token)
        if token.endswith("ing") and len(token) > 5:
            token = token[:-3]
        elif token.endswith("ed") and len(token) > 4:
            token = token[:-2]
        elif token.endswith("s") and len(token) > 4:
            token = token[:-1]
        token = aliases.get(token, token)
        if token not in TOKEN_STOPWORDS:
            result.add(token)
    return result


def _token_overlap(a: Any, b: Any) -> int:
    return len(_tokens(a) & _tokens(b))


def _skeleton_turns(plan: dict[str, Any]) -> list[dict[str, Any]]:
    turns = plan.get("turns") if isinstance(plan.get("turns"), list) else []
    clean: list[dict[str, Any]] = []
    for raw in turns:
        if not isinstance(raw, dict):
            continue
        clean.append(
            {
                "turn_id": str(raw.get("turn_id") or f"T{len(clean) + 1}").strip(),
                "operation": str(raw.get("operation") or "").strip(),
                "introduced_units": _id_list(raw.get("introduced_units")),
                "intent_delta": str(raw.get("intent_delta") or "").strip(),
            }
        )
    return clean


def validate_dialogue_skeleton(plan: dict[str, Any], capsule: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if plan.get("status") == "manual_review_required":
        reason = str(plan.get("reason") or "").strip() or "manual review requested by dialogue planner"
        errors.append(reason)
        return errors, warnings
    turns = _skeleton_turns(plan)
    if len(turns) != 5:
        errors.append("dialogue skeleton must contain exactly 5 turns")
    if turns and turns[0].get("operation") != "reveal_vague_goal":
        errors.append("dialogue skeleton T1 operation must be reveal_vague_goal")
    exposed_ids = {
        str(unit.get("unit_id"))
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict) and unit.get("expose_to_user") is not False and unit.get("type") != "implementation_hint"
    }
    unit_type_by_id = {
        str(unit.get("unit_id")): str(unit.get("type"))
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict) and unit.get("unit_id")
    }
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    has_revision_fact = revision_support.get("has_revision_fact") is True
    revision_unit_ids = {str(unit_id) for unit_id in revision_support.get("revision_unit_ids", []) if str(unit_id).strip()}
    operations: set[str] = set()
    revision_bound = False
    for index, turn in enumerate(turns):
        expected_turn_id = f"T{index + 1}"
        if str(turn.get("turn_id") or "") != expected_turn_id:
            errors.append(f"dialogue skeleton turns[{index}].turn_id must be {expected_turn_id}")
        operation = str(turn.get("operation") or "")
        operations.add(operation)
        if operation not in ALLOWED_DIALOGUE_OPERATIONS:
            errors.append(f"dialogue skeleton turns[{index}].operation is invalid: {operation}")
        introduced = _id_list(turn.get("introduced_units"))
        if not introduced:
            errors.append(f"dialogue skeleton turns[{index}].introduced_units must be non-empty")
        if len(introduced) > 2:
            errors.append(f"dialogue skeleton turns[{index}].introduced_units must contain at most 2 ids")
        for unit_id in introduced:
            if unit_id not in exposed_ids:
                errors.append(f"dialogue skeleton turns[{index}] references unknown or non-exposed unit {unit_id}")
        if operation in REVISION_OPERATIONS:
            if not has_revision_fact:
                errors.append(f"dialogue skeleton turns[{index}] contains revision operation but revision_support.has_revision_fact=false")
            matching = set(introduced) & revision_unit_ids
            if not matching:
                errors.append(f"dialogue skeleton turns[{index}] revision operation must reference revision_support.revision_unit_ids")
            elif any(unit_type_by_id.get(unit_id) in REVISION_FACT_TYPES for unit_id in matching):
                revision_bound = True
    if has_revision_fact and not (operations & REVISION_OPERATIONS):
        errors.append("dialogue skeleton must include at least one intent-revision operation")
    if has_revision_fact and not revision_bound:
        warnings.append("dialogue skeleton revision operation is not properly bound to revision_support")
    return errors, warnings


def _units_by_ids(capsule: dict[str, Any], unit_ids: list[str]) -> list[dict[str, Any]]:
    unit_by_id = _unit_map(capsule)
    return [unit_by_id[unit_id] for unit_id in unit_ids if unit_id in unit_by_id]


def _units_by_type(units: list[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    wanted = set(types)
    return [unit for unit in units if unit.get("type") in wanted]


def render_dialogue_from_skeleton(skeleton: dict[str, Any], capsule: dict[str, Any]) -> dict[str, Any]:
    final_intent = capsule.get("final_intent") if isinstance(capsule.get("final_intent"), dict) else {}
    rendered: list[dict[str, Any]] = []
    for raw in _skeleton_turns(skeleton):
        operation = str(raw.get("operation") or "")
        introduced_ids = _id_list(raw.get("introduced_units"))
        introduced_units = _units_by_ids(capsule, introduced_ids)
        utterance = ""
        if operation == "reveal_vague_goal":
            utterance = _vague_t1(
                _units_by_type(introduced_units, "symptom") or _units(capsule, "symptom"),
                _units_by_type(introduced_units, "observed_behavior", "error_message") or _units(capsule, "observed_behavior", "error_message"),
                final_intent,
            )
        elif operation in {"add_information", "add_negative_constraint"}:
            utterance = _context_utterance(
                _units_by_type(introduced_units, "affected_component", "reproduction", "boundary_case")
                or introduced_units
                or _units(capsule, "affected_component", "reproduction", "boundary_case")
            )
        elif operation == "refine":
            utterance = _refine_utterance(
                _units_by_type(introduced_units, "observed_behavior", "error_message") or _units(capsule, "observed_behavior", "error_message"),
                _units_by_type(introduced_units, "expected_behavior", "active_constraint", "negative_constraint") or _units(capsule, "expected_behavior", "active_constraint", "negative_constraint"),
            )
        elif operation in REVISION_OPERATIONS:
            utterance = _revision_utterance(introduced_units or _units(capsule, *REVISION_FACT_TYPES))
        elif operation == "add_regression_constraint":
            utterance = _regression_utterance(
                _units_by_type(introduced_units, "regression_expectation") or _units(capsule, "regression_expectation")
            )
        elif operation == "confirm":
            utterance = _confirm_utterance(
                final_intent,
                _units_by_type(introduced_units, "expected_behavior", "active_constraint") or _units(capsule, "expected_behavior", "active_constraint"),
            )
        else:
            utterance = _context_utterance(introduced_units)
        rendered.append(
            {
                "operation": operation,
                "user_utterance": utterance,
                "introduced_units": introduced_ids,
                "intent_delta": str(raw.get("intent_delta") or "").strip(),
            }
        )
    return {"turns": rendered}


def staged_utterance_context(skeleton: dict[str, Any], capsule: dict[str, Any]) -> dict[str, Any]:
    unit_by_id = _unit_map(capsule)
    skeleton_turns = []
    turn_unit_facts = []
    for index, turn in enumerate(_skeleton_turns(skeleton), start=1):
        turn_id = str(turn.get("turn_id") or f"T{index}")
        introduced_ids = _id_list(turn.get("introduced_units"))
        skeleton_turns.append(
            {
                "turn_id": turn_id,
                "operation": turn.get("operation"),
                "introduced_units": introduced_ids,
            }
        )
        turn_unit_facts.append(
            {
                "turn_id": turn_id,
                "operation": turn.get("operation"),
                "facts": [
                    {
                        "unit_id": unit_id,
                        "type": unit_by_id.get(unit_id, {}).get("type"),
                        "text": unit_by_id.get(unit_id, {}).get("text"),
                    }
                    for unit_id in introduced_ids
                    if unit_id in unit_by_id
                ],
            }
        )
    return {
        "dialogue_skeleton": {"turns": skeleton_turns},
        "turn_unit_facts": turn_unit_facts,
        "forbidden_terms": [
            "patch",
            "diff",
            "test",
            "tests",
            "benchmark",
            "oracle",
            "implementation",
            "FAIL_TO_PASS",
            "PASS_TO_PASS",
            ".py",
        ],
    }


def merge_staged_dialogue(skeleton: dict[str, Any], utterances: dict[str, Any]) -> dict[str, Any]:
    utterance_items = utterances.get("utterances") if isinstance(utterances.get("utterances"), list) else []
    utterance_by_id = {
        str(item.get("turn_id")): str(item.get("user_utterance") or "").strip()
        for item in utterance_items
        if isinstance(item, dict)
    }
    turns = []
    for index, turn in enumerate(_skeleton_turns(skeleton), start=1):
        turn_id = str(turn.get("turn_id") or f"T{index}")
        turns.append(
            {
                "operation": str(turn.get("operation") or "").strip(),
                "user_utterance": utterance_by_id.get(turn_id, ""),
                "introduced_units": _id_list(turn.get("introduced_units")),
                "intent_delta": "",
            }
        )
    return {"turns": turns}


def validate_utterance_realization(
    skeleton: dict[str, Any],
    utterances: dict[str, Any],
    capsule: dict[str, Any] | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    skeleton_turns = _skeleton_turns(skeleton)
    expected_ids = [str(turn.get("turn_id") or f"T{index}") for index, turn in enumerate(skeleton_turns, start=1)]
    turn_by_id = {str(turn.get("turn_id") or f"T{index}"): turn for index, turn in enumerate(skeleton_turns, start=1)}
    unit_by_id = _unit_map(capsule or {})
    items = utterances.get("utterances") if isinstance(utterances.get("utterances"), list) else []
    if len(items) != len(expected_ids):
        errors.append("utterance_realization must contain one utterance per skeleton turn")
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"utterances[{index}] must be an object")
            continue
        turn_id = str(item.get("turn_id") or "").strip()
        if turn_id not in expected_ids:
            errors.append(f"utterances[{index}].turn_id is unknown: {turn_id}")
        if turn_id in seen:
            errors.append(f"duplicate utterance turn_id: {turn_id}")
        seen.add(turn_id)
        text = str(item.get("user_utterance") or "").strip()
        if not text:
            errors.append(f"utterances[{index}].user_utterance is empty")
        if index == 0:
            if len(text) > 90:
                errors.append("utterance_realization T1 must be <= 90 chars")
            if T1_FORBIDDEN_RE.search(text) or STAGED_GENERIC_T1_RE.search(text):
                errors.append("utterance_realization T1 must be vague but specific, with no code-like identifiers or complete issue details")
        lowered = text.lower()
        for forbidden in ["patch", "diff", "benchmark", "oracle", "implementation", "fail_to_pass", "pass_to_pass", ".py"]:
            if forbidden in lowered:
                errors.append(f"utterances[{index}] contains forbidden term: {forbidden}")
        introduced_ids = _id_list((turn_by_id.get(turn_id) or {}).get("introduced_units"))
        if capsule is not None and introduced_ids:
            if not any(_token_overlap(text, unit_by_id.get(unit_id, {}).get("text", "")) > 0 for unit_id in introduced_ids):
                errors.append(f"utterances[{index}] does not align with introduced_units")
    missing = set(expected_ids) - seen
    if missing:
        errors.append(f"utterance_realization is missing turn ids: {', '.join(sorted(missing))}")
    return errors, warnings


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
            if T1_FORBIDDEN_RE.search(utterance):
                errors.append("dialogue_plan T1 exposes code-like identifiers or complete benchmark-style wording")
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


def _archive_plan_errors(instance_dir: Path, cache_label: str, errors: list[str], warnings: list[str]) -> None:
    (instance_dir / ".build" / f"dialogue_plan_{cache_label}.errors.json").write_text(
        json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _api_call_count(result: PromptStepResult, *, no_api: bool, dry_run: bool) -> int:
    if no_api or dry_run or result.status == "cached":
        return 0
    return len(result.call_results or []) or 1


def _raw_text(result: PromptStepResult) -> str:
    if result.raw_path and result.raw_path.exists():
        return result.raw_path.read_text(encoding="utf-8")
    return ""


def _run_dialogue_prompt(
    *,
    instance_dir: Path,
    capsule: dict[str, Any],
    model: str,
    client: DeepSeekClient | None,
    fallback_model: str | None = None,
    fallback_client: DeepSeekClient | None = None,
    force: bool,
    no_api: bool,
    dry_run: bool,
    cache_label: str,
    previous_errors: list[str] | None = None,
    previous_skeleton: dict[str, Any] | None = None,
    short_prompt: bool = False,
    llm_params: dict[str, Any] | None = None,
    json_repair_params: dict[str, Any] | None = None,
) -> PromptStepResult:
    context = exposed_semantic_context(capsule)
    if previous_errors:
        context["previous_errors"] = previous_errors[:12]
    if previous_skeleton:
        context["previous_skeleton"] = previous_skeleton
    params = {"temperature": 0.0, "max_tokens": 900, **(llm_params or {})}
    repair_params = json_repair_params or {}
    return run_prompt_step(
        instance_dir=instance_dir,
        step_name="dialogue_plan_skeleton",
        cache_prefix=f"v2_02_{cache_label}",
        prompt_path=PROMPT_DIR / ("dialogue_plan_short.md" if short_prompt else "dialogue_plan.md"),
        context=context,
        client=client,
        model=model,
        compact_prompt_path=PROMPT_DIR / "dialogue_plan_compact.md",
        fallback_client=fallback_client,
        fallback_model=fallback_model,
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        thinking=params.get("thinking"),
        response_format=params.get("response_format"),
        repair_temperature=repair_params.get("temperature"),
        repair_max_tokens=repair_params.get("max_tokens"),
        repair_thinking=repair_params.get("thinking"),
        repair_response_format=repair_params.get("response_format"),
        force=force,
        no_api=no_api or dry_run,
    )


def _safe_run_dialogue_prompt(**kwargs: Any) -> PromptStepResult:
    try:
        return _run_dialogue_prompt(**kwargs)
    except Exception as exc:
        return PromptStepResult(step_name="dialogue_plan_skeleton", status="exception", error=str(exc), error_type="client_exception")


def _run_staged_prompt(
    *,
    instance_dir: Path,
    step_name: str,
    cache_prefix: str,
    prompt_file: str,
    compact_prompt_file: str,
    context: dict[str, Any],
    model: str,
    client: DeepSeekClient | None,
    force: bool,
    no_api: bool,
    dry_run: bool,
    max_tokens: int,
    llm_params: dict[str, Any] | None = None,
    json_repair_params: dict[str, Any] | None = None,
    previous_errors: list[str] | None = None,
    previous_output: dict[str, Any] | None = None,
) -> PromptStepResult:
    params = {"temperature": 0.0, "max_tokens": max_tokens, **(llm_params or {})}
    repair_params = json_repair_params or {}
    retry_context = dict(context)
    if previous_errors:
        retry_context["previous_errors"] = previous_errors[:12]
    if previous_output is not None:
        retry_context["previous_output"] = previous_output
    return run_prompt_step(
        instance_dir=instance_dir,
        step_name=step_name,
        cache_prefix=cache_prefix,
        prompt_path=PROMPT_DIR / prompt_file,
        compact_prompt_path=PROMPT_DIR / compact_prompt_file,
        context=retry_context,
        client=client,
        model=model,
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        thinking=params.get("thinking"),
        response_format=params.get("response_format"),
        repair_temperature=repair_params.get("temperature"),
        repair_max_tokens=repair_params.get("max_tokens"),
        repair_thinking=repair_params.get("thinking"),
        repair_response_format=repair_params.get("response_format"),
        force=force,
        no_api=no_api or dry_run,
        same_model_retries=0,
        compact_retries=1,
        fallback_retries=0,
    )


def _safe_run_staged_prompt(**kwargs: Any) -> PromptStepResult:
    try:
        return _run_staged_prompt(**kwargs)
    except Exception as exc:
        return PromptStepResult(step_name=str(kwargs.get("step_name") or "staged_dialogue"), status="exception", error=str(exc), error_type="client_exception")


def _repair_prompt_result(
    *,
    instance_dir: Path,
    raw_text: str,
    model: str,
    client: DeepSeekClient | None,
    no_api: bool,
    dry_run: bool,
    cache_label: str,
    llm_params: dict[str, Any] | None = None,
) -> PromptStepResult:
    cache_dir = ensure_cache_dir(instance_dir)
    raw_path = cache_dir / f"v2_02_{cache_label}_repair.raw.txt"
    parsed_path = cache_dir / f"v2_02_{cache_label}_repair.parsed.yaml"
    error_path = cache_dir / f"v2_02_{cache_label}_repair.error.txt"
    if no_api or dry_run:
        return PromptStepResult(step_name="dialogue_plan_json_repair", status="dry_run", raw_path=raw_path, parsed_path=parsed_path)
    if client is None:
        raise ValueError("client is required when no_api is False")
    prompt = (
        "Fix this into one strict JSON object matching the existing content. "
        "Do not add, remove, or reinterpret turns. No markdown, no prose.\n\n"
        f"{raw_text[:6000]}"
    )
    params = {"temperature": 0.0, "max_tokens": 900, **(llm_params or {})}
    response = client.complete(
        "Return repaired strict JSON only.",
        prompt,
        model=model,
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        thinking=params.get("thinking"),
        response_format=params.get("response_format"),
        step_name="dialogue_plan_json_repair",
        raw_output_dir=instance_dir / ".build" / "raw_llm_outputs",
    )
    raw_path.write_text(response.content, encoding="utf-8")
    parsed = parse_model_output(response.content)
    if not parsed.ok:
        error_path.write_text(parsed.error or "Unknown repair parse error", encoding="utf-8")
        return PromptStepResult(
            step_name="dialogue_plan_json_repair",
            status="parse_error",
            raw_path=raw_path,
            parsed_path=parsed_path,
            error=parsed.error,
        )
    parsed_path.write_text(to_yaml_text(parsed.data), encoding="utf-8")
    if error_path.exists():
        error_path.unlink()
    return PromptStepResult(
        step_name="dialogue_plan_json_repair",
        status="ok",
        parsed=parsed.data,
        raw_path=raw_path,
        parsed_path=parsed_path,
        parsed_format=parsed.format,
    )


def _safe_repair_prompt_result(**kwargs: Any) -> PromptStepResult:
    try:
        return _repair_prompt_result(**kwargs)
    except Exception as exc:
        return PromptStepResult(step_name="dialogue_plan_json_repair", status="exception", error=str(exc))


def _finalize_skeleton_result(
    *,
    instance_dir: Path,
    capsule: dict[str, Any],
    skeleton: dict[str, Any],
    source: str,
    repaired: bool,
    quality_retry_used: bool,
    prompt_result: PromptStepResult,
    cache_label: str,
) -> DialoguePlanResult:
    if skeleton.get("status") == "manual_review_required":
        return DialoguePlanResult(
            ok=False,
            plan=skeleton,
            status="manual_review_required",
            errors=[str(skeleton.get("reason") or "dialogue_plan requested manual review")],
            prompt_result=prompt_result,
            source=source,
            repaired=repaired,
            quality_retry_used=quality_retry_used,
            failure_reason=str(skeleton.get("reason") or "manual_review_required"),
            error_type=None,
            repair_success=repaired,
            retry_success=bool(prompt_result.retry_success),
            fallback_used=bool(prompt_result.fallback_used),
            model_used=prompt_result.model_used,
        )
    skeleton_errors, skeleton_warnings = validate_dialogue_skeleton(skeleton, capsule)
    if skeleton_errors:
        return DialoguePlanResult(
            ok=False,
            plan=skeleton,
            status="quality_failed",
            errors=skeleton_errors,
            warnings=skeleton_warnings,
            prompt_result=prompt_result,
            source=source,
            repaired=repaired,
            quality_retry_used=quality_retry_used,
            failure_reason="; ".join(skeleton_errors),
            error_type="schema_invalid",
            repair_success=repaired,
            retry_success=bool(prompt_result.retry_success),
            fallback_used=bool(prompt_result.fallback_used),
            model_used=prompt_result.model_used,
        )
    rendered = render_dialogue_from_skeleton(skeleton, capsule)
    sanitized: SanitizerResult = sanitize_dialogue_plan(rendered)
    plan = sanitized.data if isinstance(sanitized.data, dict) else {"turns": []}
    errors, warnings = validate_dialogue_plan(plan, capsule)
    errors = [*sanitized.hard_failures, *errors]
    warnings = [*sanitized.warnings, *skeleton_warnings, *warnings]
    if not errors:
        write_dialogue_plan(instance_dir, plan, source=source)
    else:
        _archive_plan_errors(instance_dir, cache_label, errors, warnings)
    return DialoguePlanResult(
        ok=not errors,
        plan=plan,
        status="ok" if not errors else "quality_failed",
        warnings=warnings,
        errors=errors,
        prompt_result=prompt_result,
        source=source,
        repaired=repaired,
        quality_retry_used=quality_retry_used,
        failure_reason=None if not errors else "; ".join(errors),
        error_type=None if not errors else "schema_invalid",
        repair_success=repaired,
        retry_success=bool(prompt_result.retry_success),
        fallback_used=bool(prompt_result.fallback_used),
        model_used=prompt_result.model_used,
    )


def _apply_prompt_metrics(target: DialoguePlanResult, result: PromptStepResult, *, no_api: bool, dry_run: bool) -> None:
    target.api_calls_made += _api_call_count(result, no_api=no_api, dry_run=dry_run)
    target.api_call_made = target.api_calls_made > 0
    target.call_results.extend(result.call_results or [])
    target.repair_success = bool(target.repair_success or result.repair_success)
    target.retry_success = bool(target.retry_success or result.retry_success)
    target.fallback_used = bool(target.fallback_used or result.fallback_used)
    target.model_used = target.model_used or result.model_used


def _prompt_failure_result(
    *,
    step: str,
    result: PromptStepResult,
    model: str,
    api_calls_made: int,
    call_results: list[Any],
) -> DialoguePlanResult:
    return DialoguePlanResult(
        ok=False,
        status=result.status,
        api_call_made=api_calls_made > 0,
        api_calls_made=api_calls_made,
        errors=[result.error or f"{step} parse failed"],
        prompt_result=result,
        source=f"llm_staged:{model}",
        failure_reason=result.error or result.error_type or f"{step}_failed",
        error_type=result.error_type or "unknown",
        repair_success=bool(result.repair_success),
        retry_success=bool(result.retry_success),
        fallback_used=bool(result.fallback_used),
        model_used=result.model_used,
        call_results=call_results,
    )


def run_dialogue_plan(
    instance_dir: Path,
    *,
    capsule: dict[str, Any],
    model: str,
    client: DeepSeekClient | None,
    fallback_model: str | None = None,
    fallback_client: DeepSeekClient | None = None,
    force: bool = False,
    no_api: bool = False,
    dry_run: bool = False,
    cache_label: str = "dialogue_plan",
    llm_params: dict[str, Any] | None = None,
    json_repair_params: dict[str, Any] | None = None,
) -> DialoguePlanResult:
    cache_path = instance_dir / ".build" / "dialogue_plan.json"
    if not force and cache_path.exists():
        plan = load_dialogue_plan(instance_dir)
        errors, warnings = validate_dialogue_plan(plan, capsule)
        return DialoguePlanResult(ok=not errors, plan=plan, status="cached", warnings=warnings, errors=errors)

    api_calls_made = 0
    repaired = False
    quality_retry_used = False
    all_call_results: list[Any] = []
    result = _safe_run_dialogue_prompt(
        instance_dir=instance_dir,
        capsule=capsule,
        model=model,
        client=client,
        fallback_model=fallback_model,
        fallback_client=fallback_client,
        force=force,
        no_api=no_api,
        dry_run=dry_run,
        cache_label=cache_label,
        llm_params=llm_params,
        json_repair_params=json_repair_params,
    )
    api_calls_made += _api_call_count(result, no_api=no_api, dry_run=dry_run)
    all_call_results.extend(result.call_results or [])
    if result.status == "dry_run":
        return DialoguePlanResult(ok=True, status="dry_run", prompt_result=result, source="dry_run", api_calls_made=api_calls_made)

    if result.status != "ok" or not isinstance(result.parsed, dict):
        return DialoguePlanResult(
            ok=False,
            status=result.status,
            api_call_made=api_calls_made > 0,
            api_calls_made=api_calls_made,
            errors=[result.error or "dialogue_plan parse failed"],
            prompt_result=result,
            source=f"llm:{model}",
            repaired=repaired,
            failure_reason=result.error or "parse_error",
            error_type=result.error_type or "unknown",
            repair_success=bool(result.repair_success),
            retry_success=bool(result.retry_success),
            fallback_used=bool(result.fallback_used),
            model_used=result.model_used,
            call_results=all_call_results,
        )

    if result.parsed_format == "json_repaired":
        repaired = True
    _archive_prompt_result(instance_dir, result, cache_label)
    finalized = _finalize_skeleton_result(
        instance_dir=instance_dir,
        capsule=capsule,
        skeleton=result.parsed,
        source=f"llm_skeleton:{model}",
        repaired=repaired,
        quality_retry_used=False,
        prompt_result=result,
        cache_label=cache_label,
    )
    finalized.api_call_made = api_calls_made > 0
    finalized.api_calls_made = api_calls_made
    finalized.call_results = list(all_call_results)
    finalized.repair_success = bool(finalized.repair_success or result.repair_success)
    finalized.retry_success = bool(finalized.retry_success or result.retry_success)
    finalized.fallback_used = bool(finalized.fallback_used or result.fallback_used)
    finalized.model_used = finalized.model_used or result.model_used
    if finalized.ok or finalized.status == "manual_review_required":
        return finalized

    quality_retry_used = True
    retry = _safe_run_dialogue_prompt(
        instance_dir=instance_dir,
        capsule=capsule,
        model=model,
        client=client,
        fallback_model=fallback_model,
        fallback_client=fallback_client,
        force=True,
        no_api=no_api,
        dry_run=dry_run,
        cache_label=f"{cache_label}_quality_retry",
        previous_errors=finalized.errors,
        previous_skeleton=result.parsed,
        llm_params=llm_params,
        json_repair_params=json_repair_params,
    )
    api_calls_made += _api_call_count(retry, no_api=no_api, dry_run=dry_run)
    all_call_results.extend(retry.call_results or [])
    if retry.status == "ok" and isinstance(retry.parsed, dict):
        if retry.parsed_format == "json_repaired":
            repaired = True
        _archive_prompt_result(instance_dir, retry, f"{cache_label}_quality_retry")
        retried = _finalize_skeleton_result(
            instance_dir=instance_dir,
            capsule=capsule,
            skeleton=retry.parsed,
            source=f"llm_skeleton_retry:{model}",
            repaired=repaired,
            quality_retry_used=quality_retry_used,
            prompt_result=retry,
            cache_label=f"{cache_label}_quality_retry",
        )
        retried.api_call_made = api_calls_made > 0
        retried.api_calls_made = api_calls_made
        retried.call_results = list(all_call_results)
        retried.repair_success = bool(retried.repair_success or retry.repair_success)
        retried.retry_success = True
        retried.fallback_used = bool(retried.fallback_used or retry.fallback_used)
        retried.model_used = retried.model_used or retry.model_used
        return retried

    finalized.api_calls_made = api_calls_made
    finalized.api_call_made = api_calls_made > 0
    finalized.call_results = list(all_call_results)
    finalized.quality_retry_used = quality_retry_used
    finalized.failure_reason = finalized.failure_reason or "; ".join(finalized.errors)
    finalized.repair_success = bool(finalized.repair_success or retry.repair_success)
    finalized.retry_success = bool(finalized.retry_success or retry.retry_success)
    finalized.fallback_used = bool(finalized.fallback_used or retry.fallback_used)
    finalized.model_used = finalized.model_used or retry.model_used
    return finalized


def run_staged_dialogue_plan(
    instance_dir: Path,
    *,
    capsule: dict[str, Any],
    model: str,
    client: DeepSeekClient | None,
    force: bool = False,
    no_api: bool = False,
    dry_run: bool = False,
    cache_label: str = "dialogue_plan_staged",
    skeleton_params: dict[str, Any] | None = None,
    utterance_params: dict[str, Any] | None = None,
    json_repair_params: dict[str, Any] | None = None,
) -> DialoguePlanResult:
    cache_path = instance_dir / ".build" / "dialogue_plan.json"
    if not force and cache_path.exists():
        plan = load_dialogue_plan(instance_dir)
        errors, warnings = validate_dialogue_plan(plan, capsule)
        return DialoguePlanResult(ok=not errors, plan=plan, status="cached", warnings=warnings, errors=errors)

    api_calls_made = 0
    call_results: list[Any] = []
    targeted_retry_used = False
    targeted_retry_success = False
    skeleton_result = _safe_run_staged_prompt(
        instance_dir=instance_dir,
        step_name="dialogue_skeleton",
        cache_prefix=f"v2_02_{cache_label}_skeleton",
        prompt_file="dialogue_skeleton_staged.md",
        compact_prompt_file="dialogue_skeleton_staged_compact.md",
        context=staged_skeleton_context(capsule),
        model=model,
        client=client,
        force=force,
        no_api=no_api,
        dry_run=dry_run,
        max_tokens=256,
        llm_params=skeleton_params,
        json_repair_params=json_repair_params,
    )
    api_calls_made += _api_call_count(skeleton_result, no_api=no_api, dry_run=dry_run)
    call_results.extend(skeleton_result.call_results or [])
    if skeleton_result.status == "dry_run":
        return DialoguePlanResult(ok=True, status="dry_run", prompt_result=skeleton_result, source="dry_run", api_calls_made=api_calls_made)
    if skeleton_result.status != "ok" or not isinstance(skeleton_result.parsed, dict):
        failed = _prompt_failure_result(
            step="dialogue_skeleton",
            result=skeleton_result,
            model=model,
            api_calls_made=api_calls_made,
            call_results=call_results,
        )
        failed.step_results = {"dialogue_skeleton": skeleton_result}
        return failed
    if skeleton_result.parsed.get("status") == "manual_review_required":
        reason = str(skeleton_result.parsed.get("reason") or "manual_review_required")
        return DialoguePlanResult(
            ok=False,
            plan=skeleton_result.parsed,
            status="manual_review_required",
            api_call_made=api_calls_made > 0,
            api_calls_made=api_calls_made,
            errors=[reason],
            prompt_result=skeleton_result,
            source=f"llm_staged_skeleton:{model}",
            failure_reason=reason,
            call_results=call_results,
            repair_success=bool(skeleton_result.repair_success),
            retry_success=bool(skeleton_result.retry_success),
            model_used=skeleton_result.model_used,
            step_results={"dialogue_skeleton": skeleton_result},
        )
    skeleton_errors, skeleton_warnings = validate_dialogue_skeleton(skeleton_result.parsed, capsule)
    if skeleton_errors:
        targeted_retry_used = True
        skeleton_retry = _safe_run_staged_prompt(
            instance_dir=instance_dir,
            step_name="dialogue_skeleton",
            cache_prefix=f"v2_02_{cache_label}_skeleton_targeted_retry",
            prompt_file="dialogue_skeleton_staged.md",
            compact_prompt_file="dialogue_skeleton_staged_compact.md",
            context=staged_skeleton_context(capsule),
            model=model,
            client=client,
            force=True,
            no_api=no_api,
            dry_run=dry_run,
            max_tokens=256,
            llm_params=skeleton_params,
            json_repair_params=json_repair_params,
            previous_errors=skeleton_errors,
            previous_output=skeleton_result.parsed,
        )
        api_calls_made += _api_call_count(skeleton_retry, no_api=no_api, dry_run=dry_run)
        call_results.extend(skeleton_retry.call_results or [])
        retry_errors: list[str] = []
        retry_warnings: list[str] = []
        if skeleton_retry.status == "ok" and isinstance(skeleton_retry.parsed, dict):
            retry_errors, retry_warnings = validate_dialogue_skeleton(skeleton_retry.parsed, capsule)
        else:
            retry_errors = [skeleton_retry.error or "dialogue_skeleton targeted retry failed"]
        if not retry_errors and skeleton_retry.status == "ok" and isinstance(skeleton_retry.parsed, dict):
            targeted_retry_success = True
            skeleton_result = skeleton_retry
            skeleton_errors = []
            skeleton_warnings = [*skeleton_warnings, *retry_warnings]
        else:
            return DialoguePlanResult(
                ok=False,
                plan=skeleton_retry.parsed if isinstance(skeleton_retry.parsed, dict) else skeleton_result.parsed,
                status="quality_failed",
                api_call_made=api_calls_made > 0,
                api_calls_made=api_calls_made,
                errors=retry_errors or skeleton_errors,
                warnings=[*skeleton_warnings, *retry_warnings],
                prompt_result=skeleton_retry,
                source=f"llm_staged_skeleton:{model}",
                failure_reason="; ".join(retry_errors or skeleton_errors),
                error_type="schema_invalid",
                call_results=call_results,
                repair_success=bool(skeleton_result.repair_success or skeleton_retry.repair_success),
                retry_success=False,
                model_used=skeleton_retry.model_used or skeleton_result.model_used,
                step_results={"dialogue_skeleton": skeleton_retry},
            )
    _archive_prompt_result(instance_dir, skeleton_result, f"{cache_label}_skeleton")

    utterance_result = _safe_run_staged_prompt(
        instance_dir=instance_dir,
        step_name="utterance_realization",
        cache_prefix=f"v2_02_{cache_label}_utterance",
        prompt_file="utterance_realization_staged.md",
        compact_prompt_file="utterance_realization_staged_compact.md",
        context=staged_utterance_context(skeleton_result.parsed, capsule),
        model=model,
        client=client,
        force=True,
        no_api=no_api,
        dry_run=dry_run,
        max_tokens=512,
        llm_params=utterance_params,
        json_repair_params=json_repair_params,
    )
    api_calls_made += _api_call_count(utterance_result, no_api=no_api, dry_run=dry_run)
    call_results.extend(utterance_result.call_results or [])
    if utterance_result.status != "ok" or not isinstance(utterance_result.parsed, dict):
        failed = _prompt_failure_result(
            step="utterance_realization",
            result=utterance_result,
            model=model,
            api_calls_made=api_calls_made,
            call_results=call_results,
        )
        failed.repair_success = bool(failed.repair_success or skeleton_result.repair_success)
        failed.retry_success = bool(failed.retry_success or skeleton_result.retry_success)
        failed.step_results = {"dialogue_skeleton": skeleton_result, "utterance_realization": utterance_result}
        return failed
    utterance_errors, utterance_warnings = validate_utterance_realization(skeleton_result.parsed, utterance_result.parsed, capsule)
    if utterance_errors:
        targeted_retry_used = True
        utterance_retry = _safe_run_staged_prompt(
            instance_dir=instance_dir,
            step_name="utterance_realization",
            cache_prefix=f"v2_02_{cache_label}_utterance_targeted_retry",
            prompt_file="utterance_realization_staged.md",
            compact_prompt_file="utterance_realization_staged_compact.md",
            context=staged_utterance_context(skeleton_result.parsed, capsule),
            model=model,
            client=client,
            force=True,
            no_api=no_api,
            dry_run=dry_run,
            max_tokens=512,
            llm_params=utterance_params,
            json_repair_params=json_repair_params,
            previous_errors=utterance_errors,
            previous_output=utterance_result.parsed,
        )
        api_calls_made += _api_call_count(utterance_retry, no_api=no_api, dry_run=dry_run)
        call_results.extend(utterance_retry.call_results or [])
        retry_errors: list[str] = []
        retry_warnings: list[str] = []
        if utterance_retry.status == "ok" and isinstance(utterance_retry.parsed, dict):
            retry_errors, retry_warnings = validate_utterance_realization(skeleton_result.parsed, utterance_retry.parsed, capsule)
        else:
            retry_errors = [utterance_retry.error or "utterance_realization targeted retry failed"]
        if not retry_errors and utterance_retry.status == "ok" and isinstance(utterance_retry.parsed, dict):
            targeted_retry_success = True
            utterance_result = utterance_retry
            utterance_errors = []
            utterance_warnings = [*utterance_warnings, *retry_warnings]
        else:
            return DialoguePlanResult(
                ok=False,
                plan=skeleton_result.parsed,
                status="quality_failed",
                api_call_made=api_calls_made > 0,
                api_calls_made=api_calls_made,
                errors=retry_errors or utterance_errors,
                warnings=[*utterance_warnings, *retry_warnings],
                prompt_result=utterance_retry,
                source=f"llm_staged_utterance:{model}",
                failure_reason="; ".join(retry_errors or utterance_errors),
                error_type="schema_invalid",
                call_results=call_results,
                repair_success=bool(skeleton_result.repair_success or utterance_result.repair_success or utterance_retry.repair_success),
                retry_success=False,
                model_used=utterance_retry.model_used or utterance_result.model_used or skeleton_result.model_used,
                step_results={"dialogue_skeleton": skeleton_result, "utterance_realization": utterance_retry},
            )
    _archive_prompt_result(instance_dir, utterance_result, f"{cache_label}_utterance")

    merged = merge_staged_dialogue(skeleton_result.parsed, utterance_result.parsed)
    sanitized: SanitizerResult = sanitize_dialogue_plan(merged)
    plan = sanitized.data if isinstance(sanitized.data, dict) else {"turns": []}
    errors, warnings = validate_dialogue_plan(plan, capsule)
    errors = [*sanitized.hard_failures, *errors]
    warnings = [*sanitized.warnings, *skeleton_warnings, *utterance_warnings, *warnings]
    utterance_retryable_errors = [
        error
        for error in errors
        if "T1" in error
        or "introduced_units do not match utterance content" in error
        or "forbidden term" in error
        or "user_utterance" in error
    ]
    if utterance_retryable_errors:
        targeted_retry_used = True
        utterance_retry = _safe_run_staged_prompt(
            instance_dir=instance_dir,
            step_name="utterance_realization",
            cache_prefix=f"v2_02_{cache_label}_utterance_quality_retry",
            prompt_file="utterance_realization_staged.md",
            compact_prompt_file="utterance_realization_staged_compact.md",
            context=staged_utterance_context(skeleton_result.parsed, capsule),
            model=model,
            client=client,
            force=True,
            no_api=no_api,
            dry_run=dry_run,
            max_tokens=512,
            llm_params=utterance_params,
            json_repair_params=json_repair_params,
            previous_errors=utterance_retryable_errors,
            previous_output=utterance_result.parsed,
        )
        api_calls_made += _api_call_count(utterance_retry, no_api=no_api, dry_run=dry_run)
        call_results.extend(utterance_retry.call_results or [])
        if utterance_retry.status == "ok" and isinstance(utterance_retry.parsed, dict):
            retry_errors, retry_warnings = validate_utterance_realization(skeleton_result.parsed, utterance_retry.parsed, capsule)
            if not retry_errors:
                retry_merged = merge_staged_dialogue(skeleton_result.parsed, utterance_retry.parsed)
                retry_sanitized: SanitizerResult = sanitize_dialogue_plan(retry_merged)
                retry_plan = retry_sanitized.data if isinstance(retry_sanitized.data, dict) else {"turns": []}
                retry_plan_errors, retry_plan_warnings = validate_dialogue_plan(retry_plan, capsule)
                retry_errors = [*retry_sanitized.hard_failures, *retry_plan_errors]
                if not retry_errors:
                    targeted_retry_success = True
                    utterance_result = utterance_retry
                    sanitized = retry_sanitized
                    plan = retry_plan
                    errors = []
                    warnings = [*warnings, *retry_warnings, *retry_sanitized.warnings, *retry_plan_warnings]
                    _archive_prompt_result(instance_dir, utterance_result, f"{cache_label}_utterance_quality_retry")
    if errors:
        _archive_plan_errors(instance_dir, cache_label, errors, warnings)
    else:
        write_dialogue_plan(instance_dir, plan, source=f"llm_staged:{model}")
    return DialoguePlanResult(
        ok=not errors,
        plan=plan,
        status="ok" if not errors else "quality_failed",
        api_call_made=api_calls_made > 0,
        api_calls_made=api_calls_made,
        warnings=warnings,
        errors=errors,
        prompt_result=utterance_result,
        source=f"llm_staged:{model}",
        repaired=bool(skeleton_result.repair_success or utterance_result.repair_success),
        failure_reason=None if not errors else "; ".join(errors),
        error_type=None if not errors else "schema_invalid",
        repair_success=bool(skeleton_result.repair_success or utterance_result.repair_success),
        retry_success=bool(targeted_retry_success or skeleton_result.retry_success or utterance_result.retry_success),
        model_used=utterance_result.model_used or skeleton_result.model_used,
        call_results=call_results,
        step_results={"dialogue_skeleton": skeleton_result, "utterance_realization": utterance_result},
    )
