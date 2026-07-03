from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cair_v2.construction.semantic_capsule import REVISION_FACT_TYPES
from cair_v2.construction.instance_io import read_json
from cair_v2.construction.sanitizer import (
    BENCHMARK_METADATA_RE,
    IMPLEMENTATION_HINT_RE,
    contains_benchmark_metadata,
    contains_implementation_hint,
    text_blob,
)
from cair_v2.construction.views import agent_view_payload
from cair_v2.staged.schemas import ALLOWED_DIALOGUE_OPERATIONS, REVISION_OPERATIONS


REQUIRED_EVALUATION_KEYS = {
    "final_issue_prompt",
    "concat_dialogue_prompt",
    "multi_turn_cair_script",
    "recap_cair_prompt",
    "oracle_intent_prompt",
    "localization_prompt",
}

REQUIRED_ORACLE_KEYS = {
    "must_satisfy",
    "must_not_satisfy",
    "obsolete_intent_checks",
    "regression_checks",
    "forbidden_checks",
    "clarification_checks",
}

USER_RUNTIME_CLAIM_RE = re.compile(
    r"\b(i|we)\s+(ran|run|tested|verified|opened|inspected)\b|"
    r"\bterminal\b|\bIDE\b|\bafter applying\b|\bthe patch works\b",
    re.IGNORECASE,
)

GENERIC_T1_RE = re.compile(
    r"confusing failure around this behavior|one specific behavior looks wrong|"
    r"something (is|seems) wrong|this behavior (is|seems)|not working$",
    re.IGNORECASE,
)

TEMPLATE_ARTIFACT_RE = re.compile(
    r"\bUser initially\b|\bthat call\b|\bWhat I need is\b|"
    r"\bCorrection:\s*my earlier read may be off here:\s*User\b|"
    r"\bseems off\.?$",
    re.IGNORECASE,
)

SNAKE_CASE_IDENTIFIER_RE = re.compile(r"\b[A-Za-z]+_[A-Za-z0-9_]*\b")

AGENT_VIEW_FORBIDDEN_KEY_RE = re.compile(
    r"(^|[._])(oracle|gold)([._]|$)|FAIL_TO_PASS|PASS_TO_PASS|test_patch|reference_patch|hidden_test",
    re.IGNORECASE,
)

AGENT_VIEW_FORBIDDEN_TEXT_RE = re.compile(
    r"\boracle\b|localization\s+gold|reference\s+patch|hidden\s+test|"
    r"private\s+tests?\s+(confirm|validate|verify|show)|"
    r"FAIL_TO_PASS|PASS_TO_PASS|test_patch|diff --git|\btest_[A-Za-z0-9_]+\b",
    re.IGNORECASE,
)

REVISION_SUPPORT_TYPES = set(REVISION_FACT_TYPES)
OLD_PROGRESSIVE_OPERATIONS = {"reveal_vague_goal", "refine", "add_information", "correct"}
NON_MONOTONIC_OPERATIONS = {
    "speculative_hypothesis",
    "mistaken_clarification",
    "incorrect_reproduction_detail",
    "correct_previous_claim",
    "retract_previous_claim",
    "replace_previous_claim",
    "resolve_conflict",
    "narrow_scope",
    "broaden_scope",
    "add_regression_constraint",
    "confirm_final_active_intent",
}
WRONG_OR_SPECULATIVE_OPERATIONS = {
    "speculative_hypothesis",
    "mistaken_clarification",
    "incorrect_reproduction_detail",
}
RESOLUTION_OPERATIONS = {
    "correct_previous_claim",
    "retract_previous_claim",
    "replace_previous_claim",
    "resolve_conflict",
    "narrow_scope",
    "broaden_scope",
    "confirm_final_active_intent",
}
CORE_INITIAL_FACT_TYPES = {
    "symptom",
    "observed_behavior",
    "expected_behavior",
    "reproduction",
    "ambiguity_or_correction",
    "affected_component",
}

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
    "tied",
}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else ([] if value in (None, "") else [value])


def _str_list(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def default_checks() -> dict[str, bool]:
    return {
        "semantic_capsule_suitable": False,
        "fact_units_cover_basic_behavior": False,
        "compact_semantic_capsule_present": False,
        "final_intent_objective_nonempty": False,
        "must_satisfy_nonempty": False,
        "dialogue_turn_count_valid": False,
        "first_turn_initial_imperfect_report": False,
        "initial_report_not_oracle": False,
        "noisy_refinement_present": False,
        "unresolved_wrong_claims_absent": False,
        "no_old_progressive_disclosure": False,
        "final_active_intent_consistency": False,
        "has_revision_operation": False,
        "introduced_units_valid": False,
        "compact_dialogue_introduced_units_present": False,
        "non_exposed_units_not_in_dialogue": False,
        "implementation_hint_not_user_facing": False,
        "non_goal_not_in_must_satisfy": False,
        "evaluation_modes_complete": False,
        "localization_checkpoint_ready": False,
        "keep_localization_metrics": False,
        "oracle_complete": False,
        "agent_view_no_gold_or_leakage": False,
        "template_dialogue_specific": False,
        "introduced_units_content_aligned": False,
        "revision_operation_fact_supported": False,
        "revision_support_declared": False,
        "revision_unit_ids_valid": False,
        "revision_operation_bound_to_revision_support": False,
        "confirm_turn_not_full_prompt": False,
    }


def _contains_item_text(surface: Any, item: str) -> bool:
    text = text_blob(surface).lower()
    needle = str(item or "").strip().lower()
    return bool(needle) and needle in text


def _agent_view(compact: dict[str, Any]) -> dict[str, Any]:
    return agent_view_payload(compact)


def _key_paths(value: Any, prefix: str = "$") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, item in value.items():
            child = f"{prefix}.{key}"
            paths.append(child)
            paths.extend(_key_paths(item, child))
        return paths
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            paths.extend(_key_paths(item, f"{prefix}[{index}]"))
        return paths
    return []


def _tokens(text: Any) -> set[str]:
    result = set()
    aliases = {
        "cloning": "clone",
        "cloned": "clone",
        "fails": "fail",
        "failing": "fail",
        "failed": "fail",
        "warnings": "warning",
        "plugins": "plugin",
        "loading": "load",
        "loaded": "load",
        "arguments": "argument",
        "abbreviations": "abbreviation",
        "verbose": "verbosity",
        "verbosity": "verbosity",
        "layout": "alignment",
        "aligned": "alignment",
        "align": "alignment",
        "class": "type",
        "classes": "type",
    }
    raw = re.sub(r"[-_/().:=]+", " ", text_blob(text).lower())
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


def _normalized_text(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text_blob(text).lower()).strip()


def _confirm_repeats_final_intent(utterance: str, objective: str, final_issue_prompt: str) -> bool:
    clean_utt = _normalized_text(utterance)
    clean_obj = _normalized_text(objective)
    if clean_obj and clean_obj in clean_utt:
        return True
    obj_tokens = _tokens(objective)
    if obj_tokens:
        overlap = len(obj_tokens & _tokens(utterance)) / max(len(obj_tokens), 1)
        if overlap >= 0.8 and len(utterance) > 100:
            return True
    prompt_tokens = _tokens(final_issue_prompt)
    if prompt_tokens:
        overlap = len(prompt_tokens & _tokens(utterance)) / max(len(prompt_tokens), 1)
        return overlap >= 0.65 and len(utterance) > 120
    return False


def evaluate_v2_quality(
    *,
    compact: dict[str, Any],
    semantic_capsule: dict[str, Any],
    dialogue_plan: dict[str, Any],
) -> dict[str, Any]:
    hard: list[str] = []
    soft: list[str] = []
    checks = default_checks()

    suitability = semantic_capsule.get("suitability") if isinstance(semantic_capsule.get("suitability"), dict) else {}
    if suitability.get("is_cair_suitable") is True:
        checks["semantic_capsule_suitable"] = True
    else:
        hard.append("semantic_capsule.suitability.is_cair_suitable is not true")

    units = semantic_capsule.get("fact_units") if isinstance(semantic_capsule.get("fact_units"), list) else []
    unit_by_id = {str(unit.get("unit_id")): unit for unit in units if isinstance(unit, dict) and unit.get("unit_id")}
    unit_ids = set(unit_by_id)
    revision_support = semantic_capsule.get("revision_support") if isinstance(semantic_capsule.get("revision_support"), dict) else {}
    has_revision_fact = revision_support.get("has_revision_fact") is True
    revision_unit_ids = {str(unit_id) for unit_id in revision_support.get("revision_unit_ids", []) if str(unit_id).strip()}
    revision_unit_ids_valid = True
    if isinstance(semantic_capsule.get("revision_support"), dict):
        checks["revision_support_declared"] = True
    else:
        hard.append("semantic_capsule.revision_support is missing")
    exposed_units = [
        unit for unit in units
        if isinstance(unit, dict) and unit.get("expose_to_user") is not False and unit.get("type") != "implementation_hint"
    ]
    non_exposed_units = [
        unit for unit in units
        if isinstance(unit, dict) and (unit.get("expose_to_user") is False or unit.get("type") == "implementation_hint")
    ]
    type_set = {str(unit.get("type")) for unit in units if isinstance(unit, dict)}
    if len(type_set & {"symptom", "observed_behavior", "expected_behavior"}) >= 2:
        checks["fact_units_cover_basic_behavior"] = True
    else:
        hard.append("semantic_capsule.fact_units must cover at least two of symptom/observed_behavior/expected_behavior")
    compact_capsule = compact.get("semantic_capsule") if isinstance(compact.get("semantic_capsule"), dict) else {}
    compact_units = compact_capsule.get("fact_units") if isinstance(compact_capsule.get("fact_units"), list) else []
    compact_revision_support = compact_capsule.get("revision_support") if isinstance(compact_capsule.get("revision_support"), dict) else {}
    compact_unit_ids = {
        str(unit.get("unit_id"))
        for unit in compact_units
        if isinstance(unit, dict) and str(unit.get("unit_id") or "").strip()
    }
    if compact_units and compact_revision_support:
        checks["compact_semantic_capsule_present"] = True
        if compact_unit_ids != unit_ids:
            hard.append("compact semantic_capsule.fact_units do not match construction semantic_capsule.fact_units")
        for unit in compact_units:
            if not isinstance(unit, dict) or not str(unit.get("text") or "").strip():
                hard.append("compact semantic_capsule.fact_units contain an empty or invalid unit")
                break
    else:
        hard.append("compact semantic_capsule.fact_units and revision_support are required")
    if has_revision_fact:
        if not revision_unit_ids:
            revision_unit_ids_valid = False
            hard.append("semantic_capsule.revision_support.has_revision_fact=true but revision_unit_ids is empty")
        for unit_id in revision_unit_ids:
            unit = unit_by_id.get(unit_id)
            if not unit:
                revision_unit_ids_valid = False
                hard.append(f"semantic_capsule.revision_support references unknown unit {unit_id}")
            elif unit.get("type") not in REVISION_SUPPORT_TYPES:
                revision_unit_ids_valid = False
                hard.append(f"semantic_capsule.revision_support unit {unit_id} has invalid revision type {unit.get('type')}")
    else:
        hard.append("semantic_capsule.revision_support.has_revision_fact is false; CAIR revision requires manual review")
    checks["revision_unit_ids_valid"] = revision_unit_ids_valid

    final_intent = compact.get("final_intent") if isinstance(compact.get("final_intent"), dict) else {}
    if str(final_intent.get("objective") or "").strip():
        checks["final_intent_objective_nonempty"] = True
    else:
        hard.append("final_intent.objective is empty")
    if _str_list(final_intent.get("must_satisfy")):
        checks["must_satisfy_nonempty"] = True
    else:
        hard.append("final_intent.must_satisfy is empty")

    dialogue = compact.get("dialogue") if isinstance(compact.get("dialogue"), dict) else {}
    turns = dialogue.get("turns") if isinstance(dialogue.get("turns"), list) else []
    if 3 <= len(turns) <= 6:
        checks["dialogue_turn_count_valid"] = True
    else:
        hard.append("dialogue.turns must contain 3-6 turns")

    operations: set[str] = set()
    user_surface: list[str] = []
    introduced_ok = True
    compact_introduced_ok = True
    compact_introduced_alignment_ok = True
    compact_revision_support_ok = True
    compact_revision_bound_ok = False
    confirm_ok = True
    final_issue_for_confirm = ""
    first_turn_ok = False
    initial_not_oracle_ok = False
    no_old_pattern_ok = True
    final_active_consistency_ok = True
    wrong_turns: list[tuple[int, str]] = []
    resolved_wrong_turns: set[str] = set()
    unresolved_wrong_claims = 0
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            hard.append(f"dialogue.turns[{index}] must be an object")
            continue
        operation = str(turn.get("operation") or "")
        operations.add(operation)
        if operation in OLD_PROGRESSIVE_OPERATIONS:
            no_old_pattern_ok = False
            hard.append(f"dialogue.turns[{index}].operation uses deprecated progressive operation: {operation}")
        if operation not in ALLOWED_DIALOGUE_OPERATIONS:
            hard.append(f"dialogue.turns[{index}].operation is invalid: {operation}")
        utterance = str(turn.get("user_utterance") or "")
        user_surface.append(utterance)
        introduced_raw = turn.get("introduced_units")
        introduced_ids = _str_list(introduced_raw) if isinstance(introduced_raw, list) else []
        if index == 0:
            t1_types = {str(unit_by_id.get(unit_id, {}).get("type") or "") for unit_id in introduced_ids}
            first_turn_ok = (
                operation == "initial_imperfect_report"
                and 3 <= len(introduced_ids) <= 7
                and len(t1_types & CORE_INITIAL_FACT_TYPES) >= 2
                and 120 <= len(utterance) <= 900
                and not BENCHMARK_METADATA_RE.search(utterance)
                and not IMPLEMENTATION_HINT_RE.search(utterance)
            )
            if not first_turn_ok:
                hard.append("T1 must be initial_imperfect_report with 3-7 units, at least two core fact types, and 120-900 chars")
            oracle_must = _str_list((compact.get("oracle") if isinstance(compact.get("oracle"), dict) else {}).get("must_satisfy"))
            exact_oracle_cover = bool(oracle_must) and all(_contains_item_text(utterance, item) for item in oracle_must)
            objective = str(final_intent.get("objective") or "")
            objective_repeat = bool(objective) and _normalized_text(objective) == _normalized_text(utterance)
            all_units_revealed = bool(exposed_units) and len(set(introduced_ids)) >= len(exposed_units)
            if not exact_oracle_cover and not objective_repeat and not all_units_revealed:
                initial_not_oracle_ok = True
            else:
                hard.append("T1 must not equal the final intent, reveal every user-facing unit, or cover all oracle checks")
            if len(utterance) < 120 or operation != "initial_imperfect_report":
                no_old_pattern_ok = False
        if TEMPLATE_ARTIFACT_RE.search(utterance):
            hard.append(f"dialogue.turns[{index}].user_utterance contains template/meta wording")
        if operation == "confirm_final_active_intent":
            final_issue_for_confirm = str((compact.get("evaluation_modes") or {}).get("final_issue_prompt") or "")
            if _confirm_repeats_final_intent(utterance, str(final_intent.get("objective") or ""), final_issue_for_confirm) and len(utterance) > 260:
                confirm_ok = False
                hard.append(f"dialogue.turns[{index}].confirm repeats the full final intent instead of a concise confirmation")
        if BENCHMARK_METADATA_RE.search(utterance) or IMPLEMENTATION_HINT_RE.search(utterance):
            hard.append(f"dialogue.turns[{index}].user_utterance leaks benchmark metadata or implementation detail")
        if USER_RUNTIME_CLAIM_RE.search(utterance):
            hard.append(f"dialogue.turns[{index}].user_utterance makes unfair runtime/repo access claim")
        if not isinstance(introduced_raw, list):
            compact_introduced_ok = False
            hard.append(f"dialogue.turns[{index}].introduced_units must be a list in compact output")
        elif not introduced_ids:
            has_lifecycle_payload = bool(
                _str_list(turn.get("revises_units"))
                or _str_list(turn.get("deactivates_claims"))
                or _str_list(turn.get("activates_claims"))
                or operation == "confirm_final_active_intent"
            )
            if not has_lifecycle_payload:
                compact_introduced_ok = False
                hard.append(f"dialogue.turns[{index}].introduced_units must not be empty unless the turn revises, deactivates, activates, or confirms")
        for unit_id in introduced_ids:
            unit = unit_by_id.get(unit_id)
            if not unit:
                compact_introduced_ok = False
                hard.append(f"dialogue.turns[{index}] references unknown introduced unit {unit_id}")
            elif unit.get("expose_to_user") is False or unit.get("type") == "implementation_hint":
                compact_introduced_ok = False
                hard.append(f"dialogue.turns[{index}] references non-exposable introduced unit {unit_id}")
        if introduced_ids and not any(_token_overlap(utterance, unit_by_id.get(unit_id, {}).get("text", "")) > 0 for unit_id in introduced_ids):
            compact_introduced_alignment_ok = False
            hard.append(f"dialogue.turns[{index}] introduced_units do not match utterance content")
        claim_status = str(turn.get("claim_status") or "")
        if operation in WRONG_OR_SPECULATIVE_OPERATIONS or claim_status in {"speculative", "mistaken"}:
            wrong_turns.append((index, str(turn.get("turn_id") or f"T{index + 1}")))
        if index > 0 and operation in RESOLUTION_OPERATIONS:
            for revised_turn in _str_list(turn.get("revises_turns")):
                resolved_wrong_turns.add(revised_turn)
            if _str_list(turn.get("deactivates_claims")) or _str_list(turn.get("activates_claims")):
                for wrong_index, wrong_turn_id in wrong_turns:
                    if wrong_index < index:
                        resolved_wrong_turns.add(wrong_turn_id)
        if operation in REVISION_OPERATIONS:
            introduced_revision_ids = set(introduced_ids) & revision_unit_ids
            revises_revision_ids = set(_str_list(turn.get("revises_units"))) & revision_unit_ids
            revises_any_ids = set(_str_list(turn.get("revises_units"))) & unit_ids
            support_units = [unit_by_id.get(unit_id, {}) for unit_id in introduced_ids]
            if not has_revision_fact:
                compact_revision_support_ok = False
            elif not (introduced_ids or revises_any_ids):
                compact_revision_support_ok = False
                hard.append(f"dialogue.turns[{index}] revision operation is not bound to source fact units")
            elif any(unit.get("type") in REVISION_SUPPORT_TYPES for unit in support_units) or revises_revision_ids:
                compact_revision_bound_ok = True
            else:
                soft.append(f"dialogue.turns[{index}] revision operation is fact-bound but not tied to revision_support.revision_unit_ids")
        delta = turn.get("state_delta")
        if not isinstance(delta, dict):
            hard.append(f"dialogue.turns[{index}].state_delta must be an object")
        elif any(isinstance(value, list) and not value for value in delta.values()):
            soft.append(f"dialogue.turns[{index}].state_delta contains empty arrays; compact output should be sparse")
    if turns and compact_introduced_ok:
        checks["compact_dialogue_introduced_units_present"] = True
    checks["first_turn_initial_imperfect_report"] = first_turn_ok
    checks["initial_report_not_oracle"] = initial_not_oracle_ok
    if operations & NON_MONOTONIC_OPERATIONS:
        checks["noisy_refinement_present"] = True
    else:
        hard.append("dialogue must contain at least one noisy/non-monotonic refinement operation")
    unresolved = [turn_id for _, turn_id in wrong_turns if turn_id not in resolved_wrong_turns]
    unresolved_wrong_claims = len(unresolved)
    if unresolved_wrong_claims == 0:
        checks["unresolved_wrong_claims_absent"] = True
    else:
        hard.append(f"wrong or speculative claims are unresolved: {', '.join(unresolved)}")
    if no_old_pattern_ok:
        checks["no_old_progressive_disclosure"] = True
    else:
        hard.append("dialogue matches or uses an old progressive disclosure pattern")

    if operations & REVISION_OPERATIONS:
        checks["has_revision_operation"] = True
        if not has_revision_fact:
            hard.append("dialogue contains revision operation but semantic_capsule.revision_support.has_revision_fact=false")
    else:
        hard.append("dialogue must contain at least one revision operation")

    plan_turns = dialogue_plan.get("turns") if isinstance(dialogue_plan.get("turns"), list) else []
    introduced_alignment_ok = True
    revision_support_ok = True
    revision_bound_ok = False
    for index, turn in enumerate(plan_turns):
        if not isinstance(turn, dict):
            introduced_ok = False
            continue
        operation = str(turn.get("operation") or "")
        if operation in OLD_PROGRESSIVE_OPERATIONS:
            introduced_ok = False
            hard.append(f"dialogue_plan.turns[{index}] uses deprecated progressive operation: {operation}")
        introduced_ids = _str_list(turn.get("introduced_units"))
        for unit_id in _str_list(turn.get("introduced_units")):
            if unit_id not in unit_ids:
                introduced_ok = False
                hard.append(f"dialogue_plan.turns[{index}] references unknown unit {unit_id}")
        if introduced_ids:
            utterance = str(turn.get("user_utterance") or "")
            if not any(_token_overlap(utterance, unit_by_id.get(unit_id, {}).get("text", "")) > 0 for unit_id in introduced_ids):
                introduced_alignment_ok = False
                hard.append(f"dialogue_plan.turns[{index}] introduced_units do not match utterance content")
        if operation in REVISION_OPERATIONS:
            support_units = [unit_by_id.get(unit_id, {}) for unit_id in introduced_ids]
            introduced_revision_ids = set(introduced_ids) & revision_unit_ids
            revises_revision_ids = set(_str_list(turn.get("revises_units"))) & revision_unit_ids
            revises_any_ids = set(_str_list(turn.get("revises_units"))) & unit_ids
            if not has_revision_fact:
                revision_support_ok = False
                hard.append(f"dialogue_plan.turns[{index}] contains revision operation but revision_support.has_revision_fact=false")
            if not (introduced_ids or revises_any_ids):
                revision_support_ok = False
                hard.append(f"dialogue_plan.turns[{index}] revision operation is not bound to source fact units")
            elif not any(unit.get("type") in REVISION_SUPPORT_TYPES for unit in support_units) and not revises_revision_ids:
                soft.append(f"dialogue_plan.turns[{index}] revision operation is fact-bound but not tied to revision_support.revision_unit_ids")
            else:
                revision_bound_ok = True
    if has_revision_fact and not (operations & REVISION_OPERATIONS):
        revision_support_ok = False
        hard.append("semantic_capsule has revision_support but dialogue has no revision operation")
    checks["introduced_units_valid"] = introduced_ok and compact_introduced_ok
    checks["introduced_units_content_aligned"] = introduced_alignment_ok and compact_introduced_alignment_ok
    checks["revision_operation_fact_supported"] = revision_support_ok and compact_revision_support_ok
    checks["revision_operation_bound_to_revision_support"] = revision_bound_ok and compact_revision_bound_ok
    checks["confirm_turn_not_full_prompt"] = confirm_ok

    user_text = "\n".join(user_surface)
    hidden_leaks = []
    for unit in non_exposed_units:
        text = str(unit.get("text") or "").strip()
        if text and _contains_item_text(user_text, text):
            hidden_leaks.append(str(unit.get("unit_id")))
    if hidden_leaks:
        hard.append(f"non-exposed fact units appear in user dialogue: {', '.join(hidden_leaks)}")
    else:
        checks["non_exposed_units_not_in_dialogue"] = True

    user_facing_surface = {
        "dialogue": compact.get("dialogue"),
        "evaluation_modes": compact.get("evaluation_modes"),
        "final_intent_must_satisfy": final_intent.get("must_satisfy"),
    }
    if contains_implementation_hint(user_facing_surface):
        hard.append("implementation hint appears in prompt/user-facing surfaces")
    else:
        checks["implementation_hint_not_user_facing"] = True

    inactive_texts = [
        str(unit.get("text") or "").strip()
        for unit in units
        if isinstance(unit, dict) and unit.get("type") in {"non_goal", "rejected_solution", "obsolete_candidate", "implementation_hint"}
    ]
    must_surface = final_intent.get("must_satisfy")
    leaked_in_must = [text for text in inactive_texts if _contains_item_text(must_surface, text)]
    deactivated_claims = []
    for turn in turns:
        if isinstance(turn, dict):
            deactivated_claims.extend(_str_list(turn.get("deactivates_claims")))
    leaked_deactivated = [text for text in deactivated_claims if _contains_item_text(must_surface, text)]
    if leaked_in_must:
        hard.append("non_goal/rejected/implementation unit appears in final_intent.must_satisfy")
        final_active_consistency_ok = False
    elif leaked_deactivated:
        hard.append("deactivated/withdrawn claim appears in final_intent.must_satisfy")
        final_active_consistency_ok = False
    else:
        checks["non_goal_not_in_must_satisfy"] = True
    if final_active_consistency_ok and _str_list(final_intent.get("must_satisfy")):
        checks["final_active_intent_consistency"] = True

    modes = compact.get("evaluation_modes") if isinstance(compact.get("evaluation_modes"), dict) else {}
    if REQUIRED_EVALUATION_KEYS.issubset(modes) and all(str(modes.get(key) or "").strip() or key == "multi_turn_cair_script" for key in REQUIRED_EVALUATION_KEYS):
        if isinstance(modes.get("multi_turn_cair_script"), list) and len(modes.get("multi_turn_cair_script")) == len(turns):
            checks["evaluation_modes_complete"] = True
        else:
            hard.append("evaluation_modes.multi_turn_cair_script must match dialogue turn count")
    else:
        hard.append("evaluation_modes missing or empty required fields")
    if contains_benchmark_metadata(modes):
        hard.append("evaluation_modes contains benchmark/private test metadata")

    localization = compact.get("localization_checkpoint") if isinstance(compact.get("localization_checkpoint"), dict) else {}
    prompt = str(localization.get("prompt") or "")
    gold = localization.get("gold") if isinstance(localization.get("gold"), dict) else {}
    metrics = localization.get("metrics") if isinstance(localization.get("metrics"), dict) else {}
    gold_files = _str_list(gold.get("files"))
    gold_functions = _str_list(gold.get("functions"))
    if prompt and isinstance(localization.get("expected_output_schema"), dict) and isinstance(metrics, dict) and gold_files:
        checks["localization_checkpoint_ready"] = True
    else:
        soft.append("localization checkpoint is incomplete or lacks file gold")
    if gold_files and "file_hit_at_k" in metrics and "function_hit_at_k" in metrics and isinstance(gold.get("files"), list) and isinstance(gold.get("functions"), list):
        checks["keep_localization_metrics"] = True
    else:
        hard.append("localization_checkpoint must keep gold.files, gold.functions, file_hit_at_k, and function_hit_at_k")
    for gold_item in gold_files + gold_functions:
        if gold_item and gold_item in prompt:
            hard.append("localization prompt leaks evaluator-only gold")
            break
    if contains_benchmark_metadata(prompt):
        hard.append("localization prompt contains benchmark/private metadata")

    oracle = compact.get("oracle") if isinstance(compact.get("oracle"), dict) else {}
    if REQUIRED_ORACLE_KEYS.issubset(oracle) and _str_list(oracle.get("must_satisfy")):
        checks["oracle_complete"] = True
    else:
        hard.append("oracle is missing required keys or must_satisfy")
    if contains_benchmark_metadata(oracle):
        hard.append("oracle contains benchmark/private test metadata")

    agent_view = _agent_view(compact)
    forbidden_agent_keys = [path for path in _key_paths(agent_view) if AGENT_VIEW_FORBIDDEN_KEY_RE.search(path)]
    if forbidden_agent_keys:
        hard.append(f"agent view contains evaluator-only/private key: {forbidden_agent_keys[0]}")
    elif AGENT_VIEW_FORBIDDEN_TEXT_RE.search(text_blob(agent_view)):
        hard.append("agent view contains evaluator-only/private text")
    elif contains_benchmark_metadata(agent_view):
        hard.append("agent view contains benchmark/private test metadata")
    else:
        checks["agent_view_no_gold_or_leakage"] = True

    metadata = compact.get("metadata") if isinstance(compact.get("metadata"), dict) else {}
    template_used = metadata.get("dialogue_source") == "template" or dialogue_plan.get("_source") == "template"
    template_quality = dialogue_plan.get("_template_quality") if isinstance(dialogue_plan.get("_template_quality"), dict) else {}
    template_issues: list[str] = []
    if template_used:
        if turns and isinstance(turns[0], dict) and GENERIC_T1_RE.search(str(turns[0].get("user_utterance") or "")):
            template_issues.append("template T1 is generic")
        if any(TEMPLATE_ARTIFACT_RE.search(str(turn.get("user_utterance") or "")) for turn in turns if isinstance(turn, dict)):
            template_issues.append("template contains mechanical or meta wording")
        if turns and isinstance(turns[0], dict) and SNAKE_CASE_IDENTIFIER_RE.search(str(turns[0].get("user_utterance") or "")):
            template_issues.append("template T1 exposes code-like identifier")
        if template_quality.get("revision_fact_supported") is not True:
            template_issues.append("template lacks explicit revision fact support")
        if revision_support_ok and introduced_alignment_ok and not template_issues:
            checks["template_dialogue_specific"] = True
        else:
            hard.extend(f"template_quality: {issue}" for issue in template_issues)
    else:
        checks["template_dialogue_specific"] = True

    if hard:
        if any("leak" in item.lower() or "benchmark" in item.lower() for item in hard):
            status = "rejected"
            action = "manual_review"
        else:
            status = "manual_review_required"
            action = "manual_review"
    elif soft:
        status = "accepted"
        action = "accept_with_warnings"
    else:
        status = "accepted"
        action = "accept"
    return {
        "passed": not hard,
        "status": status,
        "hard_failures": hard,
        "soft_warnings": soft,
        "checks": checks,
        "localization": {
            "gold_files_count": len(gold_files),
            "gold_functions_count": len(gold_functions),
            "function_gold_available": bool(gold_functions),
            "status": "ready" if gold_files else "not_ready",
        },
        "template": {
            "template_fallback_used": bool(template_used),
            "template_quality_status": "not_used" if not template_used else ("pass" if not template_issues else "fail"),
            "issues": template_issues,
        },
        "noisy_refinement": {
            "old_progressive_disclosure_pattern": not no_old_pattern_ok,
            "unresolved_wrong_claims": unresolved_wrong_claims,
            "scenario_fit": "pass"
            if checks["first_turn_initial_imperfect_report"]
            and checks["noisy_refinement_present"]
            and checks["no_old_progressive_disclosure"]
            and checks["unresolved_wrong_claims_absent"]
            else "fail",
        },
        "recommended_action": action,
    }


def evaluate_v2_instance_dir(instance_dir: Path) -> dict[str, Any]:
    try:
        compact = read_json(instance_dir / "cair_instance.json")
    except Exception as exc:
        return {
            "passed": False,
            "status": "manual_review_required",
            "hard_failures": [f"cair_instance.json missing or invalid: {exc}"],
            "soft_warnings": [],
            "checks": default_checks(),
            "recommended_action": "manual_review",
        }
    build = instance_dir / ".build"
    try:
        capsule = json.loads((build / "semantic_capsule.json").read_text(encoding="utf-8"))
    except Exception:
        capsule = {}
    try:
        plan = json.loads((build / "dialogue_plan.json").read_text(encoding="utf-8"))
    except Exception:
        plan = {}
    if isinstance(plan, dict):
        plan.pop("_source", None)
    return evaluate_v2_quality(compact=compact, semantic_capsule=capsule, dialogue_plan=plan)
