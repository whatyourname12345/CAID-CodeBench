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
            normalized_span = " ".join(source_span.lower().split())
            normalized_haystack = " ".join(haystack.lower().split())
            if normalized_span and normalized_span not in normalized_haystack:
                errors.append(f"fact_units[{index}].source_span is not an exact substring")
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


def validate_dialogue_skeleton(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["dialogue_skeleton must be a JSON object"], warnings
    turns = data.get("turns") if isinstance(data.get("turns"), list) else []
    if not (4 <= len(turns) <= 6):
        errors.append("dialogue_skeleton.turns must contain 4-6 turns")
    fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
    intent_data = context.get("intent_revision") if isinstance(context.get("intent_revision"), dict) else {}
    unit_by_id = _unit_by_id(fact_data)
    exposed_ids = {
        unit_id
        for unit_id, unit in unit_by_id.items()
        if unit.get("expose_to_user") is not False and unit.get("type") != "implementation_hint"
    }
    support = intent_data.get("revision_support") if isinstance(intent_data.get("revision_support"), dict) else {}
    revision_ids = set(_str_list(support.get("revision_unit_ids")))
    has_revision = support.get("has_revision_fact") is True
    revision_bound = False
    operations: set[str] = set()
    for index, raw in enumerate(turns):
        if not isinstance(raw, dict):
            errors.append(f"turns[{index}] must be an object")
            continue
        expected_turn_id = f"T{index + 1}"
        turn_id = str(raw.get("turn_id") or "").strip()
        operation = str(raw.get("operation") or "").strip()
        introduced = _str_list(raw.get("introduced_units"))
        if turn_id != expected_turn_id:
            errors.append(f"turns[{index}].turn_id must be {expected_turn_id}")
        if operation not in ALLOWED_DIALOGUE_OPERATIONS:
            errors.append(f"turns[{index}].operation is invalid: {operation}")
        if index == 0 and operation != "reveal_vague_goal":
            errors.append("T1 operation must be reveal_vague_goal")
        operations.add(operation)
        if not introduced:
            errors.append(f"turns[{index}].introduced_units must be non-empty")
        if len(introduced) > 2:
            errors.append(f"turns[{index}].introduced_units should contain 1-2 ids")
        for unit_id in introduced:
            if unit_id not in exposed_ids:
                errors.append(f"turns[{index}] references unknown or non-exposed unit {unit_id}")
        if "user_utterance" in raw:
            errors.append(f"turns[{index}] must not include user_utterance")
        if "intent_delta" in raw:
            errors.append(f"turns[{index}] must not include intent_delta")
        if operation in REVISION_OPERATIONS:
            if not has_revision:
                errors.append(f"turns[{index}] uses revision operation without supported revision fact")
            if not (set(introduced) & revision_ids):
                errors.append(f"turns[{index}] revision operation must bind revision_support.revision_unit_ids")
            else:
                revision_bound = True
    if has_revision and not (operations & REVISION_OPERATIONS):
        errors.append("dialogue_skeleton must include at least one real revision operation")
    if has_revision and not revision_bound:
        errors.append("dialogue_skeleton revision operation is not bound to revision_support")
    return errors, warnings


def validate_utterance_realization(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["utterance_realization must be a JSON object"], warnings
    skeleton = context.get("dialogue_skeleton") if isinstance(context.get("dialogue_skeleton"), dict) else {}
    fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
    unit_by_id = _unit_by_id(fact_data)
    skeleton_turns = skeleton.get("turns") if isinstance(skeleton.get("turns"), list) else []
    expected_ids = [str(turn.get("turn_id")) for turn in skeleton_turns if isinstance(turn, dict)]
    turn_by_id = {str(turn.get("turn_id")): turn for turn in skeleton_turns if isinstance(turn, dict)}
    utterances = data.get("utterances") if isinstance(data.get("utterances"), list) else []
    if len(utterances) != len(expected_ids):
        errors.append("utterance_realization must contain one utterance per skeleton turn")
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
            if len(text) > 90:
                errors.append("T1 must be <= 90 chars")
            if (
                SNAKE_CASE_IDENTIFIER_RE.search(text)
                or CODE_LIKE_RE.search(text)
                or re.search(r"\b(patch|fix|should|test|gold|oracle)\b", text, re.IGNORECASE)
                or GENERIC_T1_RE.search(text)
            ):
                errors.append("T1 must be vague, specific, and free of code-like identifiers or benchmark wording")
        if TEMPLATE_ARTIFACT_RE.search(text):
            errors.append(f"utterances[{index}] contains template artifact wording")
        if FORBIDDEN_USER_TERMS_RE.search(text) or IMPLEMENTATION_HINT_RE.search(text):
            errors.append(f"utterances[{index}] leaks forbidden implementation/benchmark text")
        turn = turn_by_id.get(turn_id) if isinstance(turn_by_id.get(turn_id), dict) else {}
        if str(turn.get("operation") or "") == "confirm" and len(text) > 120:
            errors.append(f"utterances[{index}] confirm turn is too verbose")
        introduced = _str_list(turn.get("introduced_units"))
        if introduced and not any(_token_overlap(text, unit_by_id.get(unit_id, {}).get("text", "")) > 0 for unit_id in introduced):
            errors.append(f"utterances[{index}] does not align with introduced_units")
    missing = set(expected_ids) - seen
    if missing:
        errors.append(f"utterance_realization is missing turn ids: {', '.join(sorted(missing))}")
    if contains_benchmark_metadata(data):
        errors.append("utterance_realization contains benchmark/private metadata")
    return errors, warnings


def validate_semantic_reviewer(data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, dict):
        return ["semantic_reviewer must be a JSON object"], warnings
    decision = str(data.get("decision") or "").strip()
    if decision not in {"accept", "manual_review_required", "reject"}:
        errors.append(f"semantic_reviewer.decision is invalid: {decision}")
    if not isinstance(data.get("semantic_equivalence"), bool):
        errors.append("semantic_reviewer.semantic_equivalence must be boolean")
    if not isinstance(data.get("revision_is_real"), bool):
        errors.append("semantic_reviewer.revision_is_real must be boolean")
    if str(data.get("dialogue_naturalness") or "") not in {"pass", "weak", "fail"}:
        errors.append("semantic_reviewer.dialogue_naturalness is invalid")
    if str(data.get("leakage_risk") or "") not in {"low", "medium", "high"}:
        errors.append("semantic_reviewer.leakage_risk is invalid")
    if not isinstance(data.get("issues"), list):
        errors.append("semantic_reviewer.issues must be a list")
    if not isinstance(data.get("required_fixes"), list):
        errors.append("semantic_reviewer.required_fixes must be a list")
    if decision == "accept":
        if data.get("semantic_equivalence") is not True:
            errors.append("semantic_reviewer accepted without semantic_equivalence=true")
        if data.get("revision_is_real") is not True:
            errors.append("semantic_reviewer accepted without revision_is_real=true")
        if str(data.get("dialogue_naturalness")) == "fail":
            errors.append("semantic_reviewer accepted failing dialogue_naturalness")
        if str(data.get("leakage_risk")) != "low":
            errors.append("semantic_reviewer accepted non-low leakage_risk")
    return errors, warnings


VALIDATORS = {
    "fact_extraction": validate_fact_extraction,
    "intent_revision": validate_intent_revision,
    "dialogue_skeleton": validate_dialogue_skeleton,
    "utterance_realization": validate_utterance_realization,
    "semantic_reviewer": validate_semantic_reviewer,
}
