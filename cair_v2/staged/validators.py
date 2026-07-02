from __future__ import annotations

import re
from typing import Any

from cair_v2.construction.sanitizer import (
    BENCHMARK_METADATA_RE,
    IMPLEMENTATION_HINT_RE,
    contains_benchmark_metadata,
    text_blob,
)
from cair_v2.staged.schemas import (
    ALLOWED_DIALOGUE_OPERATIONS,
    FACT_UNIT_TYPES,
    REVISION_FACT_TYPES,
    REVISION_OPERATIONS,
)
from cair_v2.staged.source_spans import (
    SOURCE_MATCH_OK,
    find_source_span_match,
    is_critical_fact_unit,
)


SNAKE_CASE_IDENTIFIER_RE = re.compile(r"\b[A-Za-z]+_[A-Za-z0-9_]*\b")
CODE_LIKE_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*\([^)]*\)|\b[a-zA-Z0-9_./-]+\.py\b")
TEMPLATE_ARTIFACT_RE = re.compile(
    r"\bUser initially\b|\bWhat I need is\b|\bthat call\b|\bseems off\b|"
    r"\bPlease preserve this existing behavior\b",
    re.IGNORECASE,
)
GENERIC_T1_RE = re.compile(
    r"confusing failure around this behavior|one specific behavior looks wrong|"
    r"something (is|seems) wrong|this behavior (is|seems)|not working$",
    re.IGNORECASE,
)
FORBIDDEN_USER_TERMS_RE = re.compile(
    r"\bpatch\b|\bdiff\b|\btests?\b|\bbenchmark\b|\bgold\b|\boracle\b|"
    r"FAIL_TO_PASS|PASS_TO_PASS|hidden\s+test|reference\s+patch",
    re.IGNORECASE,
)
OLD_PROGRESSIVE_OPERATIONS = {"reveal_vague_goal", "refine", "add_information", "correct"}
CLAIM_STATUSES = {
    "active",
    "partially_active_with_uncertainty",
    "speculative",
    "mistaken",
    "correction",
    "retraction",
    "replacement",
    "confirmation",
}
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
    "uncertainty",
    "affected_component",
    "ambiguity_or_correction",
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
    "issue",
}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _str_list(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _list(value):
        text = str(item or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _unit_by_id(fact_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    units = fact_data.get("fact_units") if isinstance(fact_data.get("fact_units"), list) else []
    return {str(unit.get("unit_id")): unit for unit in units if isinstance(unit, dict) and unit.get("unit_id")}


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
        "verbosity": "verbosity",
        "layout": "alignment",
        "aligned": "alignment",
        "align": "alignment",
        "classes": "type",
        "class": "type",
    }
    raw = re.sub(r"[-_/().:=]+", " ", text_blob(text).lower())
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


def validate_fact_extraction(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["fact_extraction must be a JSON object"], warnings
    if contains_benchmark_metadata(data):
        errors.append("fact_extraction contains benchmark/private metadata")
    if not str(data.get("issue_summary") or "").strip():
        errors.append("issue_summary is empty")
    units = data.get("fact_units")
    if not isinstance(units, list) or not units:
        errors.append("fact_units must be a non-empty list")
        return errors, warnings
    ids: set[str] = set()
    covered: set[str] = set()
    problem = str(context.get("problem_statement") or "")
    hints = str(context.get("hints_text") or "")
    for index, raw in enumerate(units):
        if not isinstance(raw, dict):
            errors.append(f"fact_units[{index}] must be an object")
            continue
        unit_id = str(raw.get("unit_id") or "").strip()
        unit_type = str(raw.get("type") or "").strip()
        text = str(raw.get("text") or "").strip()
        source = str(raw.get("source") or "").strip()
        source_span = str(raw.get("source_span") or "").strip()
        if not unit_id:
            errors.append(f"fact_units[{index}].unit_id is missing")
        elif unit_id in ids:
            errors.append(f"duplicate fact unit id: {unit_id}")
        ids.add(unit_id)
        if unit_type not in FACT_UNIT_TYPES:
            errors.append(f"fact_units[{index}].type is invalid: {unit_type}")
        covered.add(unit_type)
        if not text:
            errors.append(f"fact_units[{index}].text is empty")
        if source not in {"problem_statement", "hints_text"}:
            errors.append(f"fact_units[{index}].source is invalid: {source}")
        if not source_span:
            errors.append(f"fact_units[{index}].source_span is empty")
        else:
            haystack = problem if source == "problem_statement" else hints
            status = str(raw.get("source_match_status") or "").strip()
            if status in SOURCE_MATCH_OK:
                if status == "fuzzy":
                    warnings.append(f"fact_units[{index}].source_span matched by conservative fuzzy repair")
            else:
                match = find_source_span_match(source_span, haystack, fact_text=text)
                if match.get("status") in SOURCE_MATCH_OK:
                    if match.get("status") == "fuzzy":
                        warnings.append(f"fact_units[{index}].source_span matched by conservative fuzzy repair")
                elif is_critical_fact_unit(raw):
                    errors.append(f"fact_units[{index}].source_span is not aligned to source")
                else:
                    warnings.append(f"fact_units[{index}].source_span is not aligned to source")
        if unit_type == "implementation_hint" and raw.get("expose_to_user") is not False:
            errors.append(f"implementation_hint {unit_id} must expose_to_user=false")
        if unit_type == "implementation_hint" and raw.get("active_by_default") is not False:
            errors.append(f"implementation_hint {unit_id} must active_by_default=false")
        if BENCHMARK_METADATA_RE.search(text):
            errors.append(f"fact_units[{index}] contains benchmark/private metadata")
    if len(covered & {"symptom", "observed_behavior", "expected_behavior"}) < 2:
        errors.append("fact_units must cover at least two of symptom/observed_behavior/expected_behavior")
    return errors, warnings


def validate_intent_revision(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["intent_revision must be a JSON object"], warnings
    if contains_benchmark_metadata(data):
        errors.append("intent_revision contains benchmark/private metadata")
    fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
    unit_by_id = _unit_by_id(fact_data)
    final_intent = data.get("final_intent") if isinstance(data.get("final_intent"), dict) else {}
    if not str(final_intent.get("objective") or "").strip():
        errors.append("final_intent.objective is empty")
    if not _str_list(final_intent.get("must_satisfy")):
        errors.append("final_intent.must_satisfy is empty")
    support = data.get("revision_support") if isinstance(data.get("revision_support"), dict) else {}
    if not isinstance(data.get("revision_support"), dict):
        errors.append("revision_support is missing")
    has_revision = support.get("has_revision_fact")
    if not isinstance(has_revision, bool):
        errors.append("revision_support.has_revision_fact must be boolean")
    revision_type = str(support.get("revision_type") or "").strip()
    if has_revision is True and revision_type not in REVISION_FACT_TYPES:
        errors.append(f"revision_support.revision_type is invalid: {revision_type}")
    revision_ids = _str_list(support.get("revision_unit_ids"))
    if has_revision is True and not revision_ids:
        errors.append("revision_support.has_revision_fact=true requires revision_unit_ids")
    if has_revision is False:
        warnings.append("no supported revision fact; instance requires manual review before dialogue construction")
    for unit_id in revision_ids:
        unit = unit_by_id.get(unit_id)
        if not unit:
            errors.append(f"revision_support references unknown unit {unit_id}")
        elif unit.get("type") not in REVISION_FACT_TYPES:
            errors.append(f"revision_support unit {unit_id} has non-revision type {unit.get('type')}")
    if not str(support.get("reason") or "").strip():
        errors.append("revision_support.reason is empty")
    inactive_texts = [
        str(unit.get("text") or "").strip().lower()
        for unit in unit_by_id.values()
        if unit.get("type") in {"obsolete_candidate", "rejected_solution", "non_goal", "implementation_hint"}
    ]
    must_surface = " ".join(_str_list(final_intent.get("must_satisfy"))).lower()
    for inactive in inactive_texts:
        if inactive and inactive in must_surface:
            errors.append("obsolete/rejected/non_goal/implementation fact appears in final_intent.must_satisfy")
            break
    oracle = data.get("oracle") if isinstance(data.get("oracle"), dict) else {}
    for key in ["must_satisfy", "must_not_satisfy", "obsolete_intent_checks", "regression_checks", "forbidden_checks", "clarification_checks"]:
        if key not in oracle:
            errors.append(f"oracle.{key} is missing")
    if oracle and not _str_list(oracle.get("must_satisfy")):
        errors.append("oracle.must_satisfy is empty")
    return errors, warnings


def _manual_review_payload(data: dict[str, Any]) -> tuple[bool, list[str]]:
    if str(data.get("status") or "").strip() != "manual_review_required":
        return False, []
    reason = str(data.get("reason") or "").strip()
    return True, ([] if reason else ["manual_review_required output must include reason"])


def _exposed_ids_and_types(fact_data: dict[str, Any]) -> tuple[set[str], dict[str, str]]:
    unit_by_id = _unit_by_id(fact_data)
    exposed_ids = {
        unit_id
        for unit_id, unit in unit_by_id.items()
        if unit.get("expose_to_user") is not False and unit.get("type") != "implementation_hint"
    }
    return exposed_ids, {unit_id: str(unit.get("type") or "") for unit_id, unit in unit_by_id.items()}


def _core_type_count(unit_ids: list[str], unit_type_by_id: dict[str, str]) -> int:
    types = {unit_type_by_id.get(unit_id, "") for unit_id in unit_ids}
    return len(types & CORE_INITIAL_FACT_TYPES)


def _validate_unit_refs(
    *,
    errors: list[str],
    field_name: str,
    values: Any,
    exposed_ids: set[str],
    required: bool = False,
) -> list[str]:
    unit_ids = _str_list(values)
    if required and not unit_ids:
        errors.append(f"{field_name} must be non-empty")
    for unit_id in unit_ids:
        if unit_id not in exposed_ids:
            errors.append(f"{field_name} references unknown or non-exposed unit {unit_id}")
    return unit_ids


def validate_initial_report_plan(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["initial_report_plan must be a JSON object"], warnings
    is_manual, manual_errors = _manual_review_payload(data)
    if is_manual:
        return manual_errors, warnings
    fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
    exposed_ids, unit_type_by_id = _exposed_ids_and_types(fact_data)
    report = data.get("initial_report") if isinstance(data.get("initial_report"), dict) else {}
    if not report:
        errors.append("initial_report_plan.initial_report is missing")
        return errors, warnings
    introduced = _validate_unit_refs(
        errors=errors,
        field_name="initial_report.introduced_units",
        values=report.get("introduced_units"),
        exposed_ids=exposed_ids,
        required=True,
    )
    if not (3 <= len(introduced) <= 7):
        errors.append("initial_report.introduced_units must contain 3-7 ids")
    if _core_type_count(introduced, unit_type_by_id) < 2:
        errors.append("initial_report must cover at least two core fact types")
    imperfection_types = _str_list(report.get("imperfection_types"))
    if not imperfection_types:
        errors.append("initial_report.imperfection_types must be non-empty")
    _validate_unit_refs(
        errors=errors,
        field_name="initial_report.noisy_or_imperfect_units",
        values=report.get("noisy_or_imperfect_units"),
        exposed_ids=exposed_ids,
        required=False,
    )
    withheld = _validate_unit_refs(
        errors=errors,
        field_name="initial_report.withheld_units_for_later",
        values=report.get("withheld_units_for_later"),
        exposed_ids=exposed_ids,
        required=True,
    )
    if not set(withheld) - set(introduced):
        errors.append("initial_report must withhold at least one later source-grounded unit")
    if len(introduced) >= len(exposed_ids) and len(exposed_ids) > 3:
        errors.append("initial_report must not introduce every user-exposable fact unit")
    if not str(report.get("rationale") or "").strip():
        errors.append("initial_report.rationale is empty")
    if contains_benchmark_metadata(data):
        errors.append("initial_report_plan contains benchmark/private metadata")
    return errors, warnings


def validate_noisy_revision_event_plan(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["noisy_revision_event_plan must be a JSON object"], warnings
    is_manual, manual_errors = _manual_review_payload(data)
    if is_manual:
        return manual_errors, warnings
    fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
    intent_data = context.get("intent_revision") if isinstance(context.get("intent_revision"), dict) else {}
    initial_plan = context.get("initial_report_plan") if isinstance(context.get("initial_report_plan"), dict) else {}
    exposed_ids, unit_type_by_id = _exposed_ids_and_types(fact_data)
    support = intent_data.get("revision_support") if isinstance(intent_data.get("revision_support"), dict) else {}
    revision_ids = set(_str_list(support.get("revision_unit_ids")))
    has_revision = support.get("has_revision_fact") is True
    initial_report = initial_plan.get("initial_report") if isinstance(initial_plan.get("initial_report"), dict) else {}
    initial_units = set(_str_list(initial_report.get("introduced_units")))
    turns = data.get("turns") if isinstance(data.get("turns"), list) else []
    if not (3 <= len(turns) <= 6):
        errors.append("noisy_revision_event_plan.turns must contain 3-6 turns")
    operations: set[str] = set()
    revision_bound = False
    wrong_turns: list[tuple[int, str]] = []
    resolved_wrong_turns: set[str] = set()
    for index, raw in enumerate(turns):
        if not isinstance(raw, dict):
            errors.append(f"turns[{index}] must be an object")
            continue
        expected_turn_id = f"T{index + 1}"
        turn_id = str(raw.get("turn_id") or "").strip()
        operation = str(raw.get("operation") or "").strip()
        introduced = _validate_unit_refs(
            errors=errors,
            field_name=f"turns[{index}].introduced_units",
            values=raw.get("introduced_units"),
            exposed_ids=exposed_ids,
            required=index == 0,
        )
        if turn_id != expected_turn_id:
            errors.append(f"turns[{index}].turn_id must be {expected_turn_id}")
        if operation in OLD_PROGRESSIVE_OPERATIONS:
            errors.append(f"turns[{index}].operation uses deprecated progressive operation: {operation}")
        if operation not in ALLOWED_DIALOGUE_OPERATIONS:
            errors.append(f"turns[{index}].operation is invalid: {operation}")
        if index == 0 and operation != "initial_imperfect_report":
            errors.append("T1 operation must be initial_imperfect_report")
        if index == 0:
            if not (3 <= len(introduced) <= 7):
                errors.append("T1 introduced_units must contain 3-7 ids")
            if initial_units and set(introduced) != initial_units:
                errors.append("T1 introduced_units must match initial_report_plan.initial_report.introduced_units")
            if _core_type_count(introduced, unit_type_by_id) < 2:
                errors.append("T1 must cover at least two core fact types")
        elif operation == "initial_imperfect_report":
            errors.append("Only T1 may use initial_imperfect_report")
        operations.add(operation)
        claim_status = str(raw.get("claim_status") or "").strip()
        if claim_status not in CLAIM_STATUSES:
            errors.append(f"turns[{index}].claim_status is invalid: {claim_status}")
        revises_units = _validate_unit_refs(
            errors=errors,
            field_name=f"turns[{index}].revises_units",
            values=raw.get("revises_units"),
            exposed_ids=exposed_ids,
            required=False,
        )
        has_lifecycle_payload = bool(
            introduced
            or revises_units
            or _str_list(raw.get("deactivates_claims"))
            or _str_list(raw.get("activates_claims"))
        )
        if index > 0 and not has_lifecycle_payload and operation != "confirm_final_active_intent":
            errors.append(f"turns[{index}] must introduce, revise, deactivate, or activate at least one source-grounded claim")
        if "user_utterance" in raw:
            errors.append(f"turns[{index}] must not include user_utterance")
        if "intent_delta" in raw:
            errors.append(f"turns[{index}] must not include intent_delta")
        for list_field in ["revises_turns", "deactivates_claims", "activates_claims", "active_after_turn", "inactive_after_turn"]:
            if list_field in raw and not isinstance(raw.get(list_field), list):
                errors.append(f"turns[{index}].{list_field} must be a list")
        if operation in REVISION_OPERATIONS or claim_status in {"correction", "retraction", "replacement"}:
            if not has_revision:
                errors.append(f"turns[{index}] uses revision operation without supported revision fact")
            if not (set(introduced) | set(revises_units)):
                errors.append(f"turns[{index}] revision operation must bind source fact units")
            elif (set(introduced) | set(revises_units)) & revision_ids:
                revision_bound = True
        if operation in {"speculative_hypothesis", "mistaken_clarification", "incorrect_reproduction_detail"} or claim_status in {"speculative", "mistaken"}:
            wrong_turns.append((index, turn_id))
        if index > 0 and operation in RESOLUTION_OPERATIONS:
            for revised_turn in _str_list(raw.get("revises_turns")):
                resolved_wrong_turns.add(revised_turn)
            if raw.get("deactivates_claims") or raw.get("activates_claims"):
                for wrong_index, wrong_turn_id in wrong_turns:
                    if wrong_index < index:
                        resolved_wrong_turns.add(wrong_turn_id)
        must_resolve = raw.get("must_be_resolved_later")
        if index == len(turns) - 1 and must_resolve not in (False, [], None, ""):
            errors.append("final turn must not leave must_be_resolved_later unresolved")
    if not (operations & NON_MONOTONIC_OPERATIONS):
        errors.append("noisy_revision_event_plan must include at least one noisy/non-monotonic refinement operation")
    unresolved = [turn_id for _, turn_id in wrong_turns if turn_id not in resolved_wrong_turns]
    if unresolved:
        errors.append(f"wrong or speculative turns are unresolved: {', '.join(unresolved)}")
    if has_revision and not (operations & REVISION_OPERATIONS):
        errors.append("noisy_revision_event_plan must include at least one revision/final-active operation")
    if has_revision and not revision_bound:
        warnings.append("noisy_revision_event_plan revision operation is not bound to revision_support")
    if contains_benchmark_metadata(data):
        errors.append("noisy_revision_event_plan contains benchmark/private metadata")
    return errors, warnings


def validate_realistic_utterance_realization(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["realistic_utterance_realization must be a JSON object"], warnings
    event_plan = context.get("noisy_revision_event_plan") if isinstance(context.get("noisy_revision_event_plan"), dict) else {}
    fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
    unit_by_id = _unit_by_id(fact_data)
    event_turns = event_plan.get("turns") if isinstance(event_plan.get("turns"), list) else []
    expected_ids = [str(turn.get("turn_id")) for turn in event_turns if isinstance(turn, dict)]
    turn_by_id = {str(turn.get("turn_id")): turn for turn in event_turns if isinstance(turn, dict)}
    utterances = data.get("utterances") if isinstance(data.get("utterances"), list) else []
    if len(utterances) != len(expected_ids):
        errors.append("realistic_utterance_realization must contain one utterance per event-plan turn")
    seen: set[str] = set()
    for index, raw in enumerate(utterances):
        if not isinstance(raw, dict):
            errors.append(f"utterances[{index}] must be an object")
            continue
        turn_id = str(raw.get("turn_id") or "").strip()
        text = str(raw.get("user_utterance") or "").strip()
        if turn_id not in expected_ids:
            errors.append(f"utterances[{index}].turn_id is unknown: {turn_id}")
        if turn_id in seen:
            errors.append(f"duplicate utterance turn_id: {turn_id}")
        seen.add(turn_id)
        if not text:
            errors.append(f"utterances[{index}].user_utterance is empty")
        if index == 0:
            if not (120 <= len(text) <= 900):
                errors.append("T1 must be a realistic initial issue report of 120-900 chars")
            if (
                re.search(r"\b(patch|diff|tests?|gold|oracle|reference patch|hidden test)\b", text, re.IGNORECASE)
                or GENERIC_T1_RE.search(text)
            ):
                errors.append("T1 must be an imperfect issue report without benchmark/private wording")
        if TEMPLATE_ARTIFACT_RE.search(text):
            errors.append(f"utterances[{index}] contains template artifact wording")
        if FORBIDDEN_USER_TERMS_RE.search(text) or IMPLEMENTATION_HINT_RE.search(text):
            errors.append(f"utterances[{index}] leaks forbidden implementation/benchmark text")
        turn = turn_by_id.get(turn_id) if isinstance(turn_by_id.get(turn_id), dict) else {}
        if str(turn.get("operation") or "") == "confirm_final_active_intent" and len(text) > 420:
            warnings.append(f"utterances[{index}] final confirmation turn is verbose")
        introduced = _str_list(turn.get("introduced_units"))
        if introduced and not any(_token_overlap(text, unit_by_id.get(unit_id, {}).get("text", "")) > 0 for unit_id in introduced):
            errors.append(f"utterances[{index}] does not align with introduced_units")
    missing = set(expected_ids) - seen
    if missing:
        errors.append(f"realistic_utterance_realization is missing turn ids: {', '.join(sorted(missing))}")
    if contains_benchmark_metadata(data):
        errors.append("realistic_utterance_realization contains benchmark/private metadata")
    return errors, warnings


def validate_semantic_reviewer(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["semantic_reviewer must be a JSON object"], warnings
    decision = str(data.get("decision") or "").strip()
    if decision not in {"accept", "manual_review_required", "reject"}:
        errors.append(f"semantic_reviewer.decision is invalid: {decision}")
    for key in ["scenario_fit", "initial_report_quality", "noisy_refinement_quality"]:
        if str(data.get(key) or "") not in {"pass", "weak", "fail"}:
            errors.append(f"semantic_reviewer.{key} is invalid")
    for key in [
        "has_non_monotonic_claim_evolution",
        "wrong_or_speculative_claims_resolved",
        "old_progressive_disclosure_pattern",
        "final_intent_consistent",
        "revision_grounded",
    ]:
        if not isinstance(data.get(key), bool):
            errors.append(f"semantic_reviewer.{key} must be boolean")
    if str(data.get("leakage_risk") or "") not in {"low", "medium", "high"}:
        errors.append("semantic_reviewer.leakage_risk is invalid")
    if not isinstance(data.get("issues"), list):
        errors.append("semantic_reviewer.issues must be a list")
    if not isinstance(data.get("required_fixes"), list):
        errors.append("semantic_reviewer.required_fixes must be a list")
    if "unresolved_wrong_claims" in data:
        try:
            unresolved_wrong_claims = int(data.get("unresolved_wrong_claims") or 0)
        except (TypeError, ValueError):
            errors.append("semantic_reviewer.unresolved_wrong_claims must be an integer")
            unresolved_wrong_claims = 1
    else:
        unresolved_wrong_claims = 0
    if decision == "accept":
        if str(data.get("scenario_fit")) != "pass":
            errors.append("semantic_reviewer accepted without scenario_fit=pass")
        if str(data.get("initial_report_quality")) == "fail":
            errors.append("semantic_reviewer accepted failing initial_report_quality")
        if str(data.get("noisy_refinement_quality")) == "fail":
            errors.append("semantic_reviewer accepted failing noisy_refinement_quality")
        if data.get("has_non_monotonic_claim_evolution") is not True:
            errors.append("semantic_reviewer accepted without non-monotonic claim evolution")
        if data.get("wrong_or_speculative_claims_resolved") is not True:
            errors.append("semantic_reviewer accepted with unresolved wrong/speculative claims")
        if unresolved_wrong_claims != 0:
            errors.append("semantic_reviewer accepted with unresolved_wrong_claims != 0")
        if data.get("old_progressive_disclosure_pattern") is not False:
            errors.append("semantic_reviewer accepted old progressive disclosure pattern")
        if data.get("final_intent_consistent") is not True:
            errors.append("semantic_reviewer accepted without final_intent_consistent=true")
        if data.get("revision_grounded") is not True:
            errors.append("semantic_reviewer accepted without revision_grounded=true")
        if str(data.get("leakage_risk")) != "low":
            errors.append("semantic_reviewer accepted non-low leakage_risk")
    return errors, warnings


VALIDATORS = {
    "fact_extraction": validate_fact_extraction,
    "intent_revision": validate_intent_revision,
    "initial_report_plan": validate_initial_report_plan,
    "noisy_revision_event_plan": validate_noisy_revision_event_plan,
    "realistic_utterance_realization": validate_realistic_utterance_realization,
    "semantic_reviewer": validate_semantic_reviewer,
}
