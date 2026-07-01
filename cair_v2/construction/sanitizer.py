from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any


BENCHMARK_METADATA_RE = re.compile(
    r"FAIL_TO_PASS|PASS_TO_PASS|hidden\s+test|reference\s+patch|diff --git|"
    r"\btest_[A-Za-z0-9_]+\b|\btests/[A-Za-z0-9_./-]+",
    re.IGNORECASE,
)

IMPLEMENTATION_HINT_RE = re.compile(
    r"diff --git|apply\s+this\s+patch|exact\s+patch|reference\s+patch|"
    r"^\s*\+\s*(def|class|return|import)\b|^\s*-\s*(def|class|return)\b|```|"
    r"\bcall\s+[A-Za-z_][A-Za-z0-9_.]*\(|\boverride\s+[A-Za-z_][A-Za-z0-9_.]*",
    re.IGNORECASE | re.MULTILINE,
)

PRIVATE_TEST_NAME_RE = re.compile(r"\btest_[A-Za-z0-9_]+\b")

CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID = "U999"
CANONICAL_SOURCE_UNCERTAINTY_TEXT = (
    "User explicitly expresses uncertainty about whether the observed behavior is a bug or intended behavior."
)

SOURCE_UNCERTAINTY_REVISION_PATTERNS = [
    re.compile(r"\b(?:i\s+)?might\s+be\s+missing\s+something\b", re.IGNORECASE),
    re.compile(r"\b(?:i\s+)?may\s+be\s+missing\s+something\b", re.IGNORECASE),
    re.compile(r"\bam\s+i\s+missing\s+something\b", re.IGNORECASE),
    re.compile(r"\bnot\s+sure\s+(?:if|whether)\s+(?:this|that|it)\s+(?:is|was)\s+(?:a\s+)?(?:bug|expected|intended)\b", re.IGNORECASE),
    re.compile(r"\bunsure\s+(?:if|whether)\s+(?:this|that|it)\s+(?:is|was)\s+(?:a\s+)?(?:bug|expected|intended)\b", re.IGNORECASE),
    re.compile(r"\b(?:is|was)\s+(?:this|that|it)\s+(?:expected|intended)\b", re.IGNORECASE),
    re.compile(r"\b(?:this|that|it)\s+(?:feels|looks|seems)\s+like\s+a\s+bug\b", re.IGNORECASE),
]


@dataclass
class SanitizerResult:
    data: Any
    changed: bool = False
    warnings: list[str] = field(default_factory=list)
    hard_failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard_failures


def text_blob(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_blob(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(text_blob(item) for item in value.values())
    return str(value or "")


def contains_benchmark_metadata(value: Any) -> bool:
    return bool(BENCHMARK_METADATA_RE.search(text_blob(value)))


def contains_implementation_hint(value: Any) -> bool:
    return bool(IMPLEMENTATION_HINT_RE.search(text_blob(value)))


def scrub_text(value: Any, warnings: list[str], *, location: str) -> str:
    text = str(value or "")
    original = text
    replacements = [
        (re.compile(r"FAIL_TO_PASS", re.IGNORECASE), "private failing-test metadata"),
        (re.compile(r"PASS_TO_PASS", re.IGNORECASE), "private regression-test metadata"),
        (re.compile(r"reference\s+patch", re.IGNORECASE), "implementation details"),
        (re.compile(r"hidden\s+test", re.IGNORECASE), "private test"),
        (re.compile(r"diff --git", re.IGNORECASE), "implementation diff"),
    ]
    for pattern, repl in replacements:
        text = pattern.sub(repl, text)
    text = PRIVATE_TEST_NAME_RE.sub("private_test", text)
    text = re.sub(r"\btests/[A-Za-z0-9_./-]+", "private test file", text, flags=re.IGNORECASE)
    if text != original:
        warnings.append(f"scrubbed benchmark/private metadata at {location}")
    return text.strip()


def scrub_structure(value: Any, warnings: list[str], *, location: str = "$") -> Any:
    if isinstance(value, str):
        return scrub_text(value, warnings, location=location)
    if isinstance(value, list):
        return [scrub_structure(item, warnings, location=f"{location}[{idx}]") for idx, item in enumerate(value)]
    if isinstance(value, dict):
        return {key: scrub_structure(item, warnings, location=f"{location}.{key}") for key, item in value.items()}
    return value


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return default


def _dedupe_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _list(values):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _has_source_uncertainty_revision(source_text: str | None) -> bool:
    text = " ".join(str(source_text or "").split())
    if not text:
        return False
    return any(pattern.search(text) for pattern in SOURCE_UNCERTAINTY_REVISION_PATTERNS)


def _next_unit_id(units: list[dict[str, Any]]) -> str:
    used = {str(unit.get("unit_id") or "").strip() for unit in units}
    highest = 0
    for unit_id in used:
        match = re.fullmatch(r"U(\d+)", unit_id)
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = highest + 1
    while f"U{candidate}" in used:
        candidate += 1
    return f"U{candidate}"


def _remap_unit_id_refs(value: Any, remap: dict[str, str]) -> Any:
    if not remap:
        return value
    if isinstance(value, str):
        return remap.get(value, value)
    if isinstance(value, list):
        return [_remap_unit_id_refs(item, remap) for item in value]
    if isinstance(value, dict):
        return {key: _remap_unit_id_refs(item, remap) for key, item in value.items()}
    return value


def _ensure_source_uncertainty_revision(
    units: list[dict[str, Any]],
    source_text: str | None,
    warnings: list[str],
) -> tuple[list[str], dict[str, str]]:
    if not _has_source_uncertainty_revision(source_text):
        return [], {}

    canonical_unit = None
    ambiguity_unit = None
    used_ids = {str(unit.get("unit_id") or "").strip() for unit in units}
    for unit in units:
        unit_id = str(unit.get("unit_id") or "").strip()
        if unit_id == CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID:
            canonical_unit = unit
        if unit.get("type") == "ambiguity_or_correction" and ambiguity_unit is None:
            ambiguity_unit = unit

    remap: dict[str, str] = {}
    if canonical_unit is not None and canonical_unit.get("type") != "ambiguity_or_correction":
        new_id = _next_unit_id(units)
        canonical_unit["unit_id"] = new_id
        remap[CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID] = new_id
        used_ids.discard(CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID)
        used_ids.add(new_id)
        canonical_unit = None
        warnings.append(f"moved non-ambiguity fact from {CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID} to {new_id}")

    if canonical_unit is not None:
        canonical_unit["type"] = "ambiguity_or_correction"
        canonical_unit["text"] = CANONICAL_SOURCE_UNCERTAINTY_TEXT
        canonical_unit["source"] = "problem_statement"
        canonical_unit["active_by_default"] = True
        canonical_unit["expose_to_user"] = True
        canonical_unit["risk"] = None
        warnings.append("stabilized revision_support from canonical source uncertainty unit")
        return [CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID], remap

    if ambiguity_unit is not None and CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID not in used_ids:
        old_id = str(ambiguity_unit.get("unit_id") or "").strip()
        ambiguity_unit["unit_id"] = CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID
        ambiguity_unit["text"] = CANONICAL_SOURCE_UNCERTAINTY_TEXT
        ambiguity_unit["source"] = "problem_statement"
        ambiguity_unit["active_by_default"] = True
        ambiguity_unit["expose_to_user"] = True
        ambiguity_unit["risk"] = None
        if old_id:
            remap[old_id] = CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID
        warnings.append("canonicalized source uncertainty ambiguity unit")
        return [CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID], remap

    unit_id = CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID if CANONICAL_SOURCE_UNCERTAINTY_UNIT_ID not in used_ids else _next_unit_id(units)
    units.append(
        {
            "unit_id": unit_id,
            "type": "ambiguity_or_correction",
            "text": CANONICAL_SOURCE_UNCERTAINTY_TEXT,
            "source": "problem_statement",
            "active_by_default": True,
            "expose_to_user": True,
            "risk": None,
        }
    )
    warnings.append("added source-grounded ambiguity_or_correction revision support")
    return [unit_id], remap


def sanitize_semantic_capsule(capsule: dict[str, Any], *, source_text: str | None = None) -> SanitizerResult:
    warnings: list[str] = []
    hard: list[str] = []
    data = scrub_structure(copy.deepcopy(capsule), warnings)
    if not isinstance(data, dict):
        return SanitizerResult(data={}, warnings=warnings, hard_failures=["semantic_capsule is not a JSON object"])

    fact_units = data.get("fact_units")
    if not isinstance(fact_units, list):
        data["fact_units"] = []
        hard.append("semantic_capsule.fact_units must be a list")
        fact_units = []

    revision_fact_types = {
        "rejected_solution",
        "obsolete_candidate",
        "negative_constraint",
        "regression_expectation",
        "ambiguity_or_correction",
        "design_suggestion_non_goal",
        "workaround_to_reject",
        "conflict_or_tension",
    }
    normalized_units: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(fact_units, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"dropped non-object fact unit at index {index}")
            continue
        unit = dict(raw)
        unit_id = str(unit.get("unit_id") or f"U{index}").strip()
        if unit_id in seen_ids:
            unit_id = f"U{index}"
        seen_ids.add(unit_id)
        unit["unit_id"] = unit_id
        unit_type = str(unit.get("type") or "").strip()
        unit["type"] = unit_type
        unit["text"] = str(unit.get("text") or "").strip()
        unit["source"] = str(unit.get("source") or "derived").strip()
        unit["active_by_default"] = _as_bool(unit.get("active_by_default"), True)
        unit["expose_to_user"] = _as_bool(unit.get("expose_to_user"), True)
        risk = unit.get("risk")
        unit["risk"] = None if risk in (None, "", "null") else str(risk).strip()
        if unit_type == "implementation_hint":
            unit["active_by_default"] = False
            unit["expose_to_user"] = False
            if not unit["risk"]:
                unit["risk"] = "implementation hint; do not expose to user dialogue or final active intent"
            warnings.append(f"implementation_hint {unit_id} forced inactive and non-exposed")
        if unit_type in {"non_goal", "design_suggestion_non_goal", "obsolete_candidate", "rejected_solution", "workaround_to_reject"}:
            unit["active_by_default"] = False
        if unit_type in {"design_suggestion_non_goal", "workaround_to_reject"}:
            unit["expose_to_user"] = True
        if unit["text"]:
            normalized_units.append(unit)
        else:
            warnings.append(f"dropped empty fact unit {unit_id}")
    data["fact_units"] = normalized_units

    final_intent = data.get("final_intent") if isinstance(data.get("final_intent"), dict) else {}
    oracle = data.get("oracle") if isinstance(data.get("oracle"), dict) else {}
    source_uncertainty_revision_ids, unit_id_remap = _ensure_source_uncertainty_revision(normalized_units, source_text, warnings)
    if unit_id_remap:
        data["dialogue_guidance"] = _remap_unit_id_refs(data.get("dialogue_guidance"), unit_id_remap)
        data["revision_support"] = _remap_unit_id_refs(data.get("revision_support"), unit_id_remap)
    unit_type_by_id = {str(unit.get("unit_id")): str(unit.get("type")) for unit in normalized_units}
    inferred_revision_ids = [
        str(unit.get("unit_id"))
        for unit in normalized_units
        if unit.get("type") in revision_fact_types and str(unit.get("unit_id") or "").strip()
    ]
    revision_support = data.get("revision_support") if isinstance(data.get("revision_support"), dict) else {}
    revision_unit_ids = _dedupe_strings(revision_support.get("revision_unit_ids"))
    revision_unit_ids = [
        unit_id
        for unit_id in revision_unit_ids
        if unit_type_by_id.get(unit_id) in revision_fact_types
    ]
    has_revision_fact = _as_bool(revision_support.get("has_revision_fact"), bool(revision_unit_ids or inferred_revision_ids))
    if source_uncertainty_revision_ids:
        has_revision_fact = True
        revision_unit_ids = source_uncertainty_revision_ids[:1]
    elif has_revision_fact and not revision_unit_ids:
        revision_unit_ids = inferred_revision_ids[:4]
    if not has_revision_fact:
        revision_unit_ids = []
    revision_types = []
    revision_unit_id_set = set(revision_unit_ids)
    for unit in normalized_units:
        if str(unit.get("unit_id")) in revision_unit_id_set and unit.get("type") != "implementation_hint":
            if unit.get("expose_to_user") is False:
                warnings.append(f"revision_support unit {unit.get('unit_id')} forced exposable for dialogue planning")
            unit["expose_to_user"] = True
    for unit_id in revision_unit_ids:
        unit_type = unit_type_by_id.get(unit_id)
        if unit_type and unit_type not in revision_types:
            revision_types.append(unit_type)
    data["revision_support"] = {
        "has_revision_fact": bool(has_revision_fact and revision_unit_ids),
        "revision_unit_ids": revision_unit_ids,
        "revision_types": revision_types,
        "reason": (
            "Explicit source uncertainty supports an ambiguity/correction revision."
            if source_uncertainty_revision_ids
            else str(revision_support.get("reason") or ("Supported revision facts found." if revision_unit_ids else "No supported revision fact found in the issue.")).strip()
        ),
    }

    guidance = data.get("dialogue_guidance") if isinstance(data.get("dialogue_guidance"), dict) else {}
    guidance["revision_units"] = revision_unit_ids
    data["dialogue_guidance"] = guidance

    non_active_texts = [
        str(unit.get("text") or "").strip()
        for unit in normalized_units
        if unit.get("type") in {"implementation_hint", "non_goal", "design_suggestion_non_goal", "obsolete_candidate", "rejected_solution", "workaround_to_reject"}
    ]
    for key in ["must_satisfy", "must_not_satisfy", "non_goals", "regression_expectations"]:
        final_intent[key] = _dedupe_strings(final_intent.get(key))
    for key in ["must_satisfy", "must_not_satisfy", "obsolete_intent_checks", "regression_checks", "forbidden_checks", "clarification_checks"]:
        oracle[key] = _dedupe_strings(oracle.get(key))
    final_intent["objective"] = str(final_intent.get("objective") or "").strip()
    active_must = []
    for item in final_intent.get("must_satisfy", []):
        if any(non_active and non_active.lower() in item.lower() for non_active in non_active_texts):
            warnings.append("removed non-active or implementation hint from final_intent.must_satisfy")
            continue
        active_must.append(item)
    final_intent["must_satisfy"] = active_must
    data["final_intent"] = final_intent
    data["oracle"] = oracle

    if contains_benchmark_metadata(data):
        hard.append("semantic_capsule still contains benchmark/private test metadata after sanitization")
    return SanitizerResult(data=data, changed=bool(warnings), warnings=warnings, hard_failures=hard)


def sanitize_dialogue_plan(plan: dict[str, Any]) -> SanitizerResult:
    warnings: list[str] = []
    hard: list[str] = []
    data = scrub_structure(copy.deepcopy(plan), warnings)
    if not isinstance(data, dict):
        return SanitizerResult(data={"turns": []}, warnings=warnings, hard_failures=["dialogue_plan is not a JSON object"])
    turns = data.get("turns")
    if not isinstance(turns, list):
        hard.append("dialogue_plan.turns must be a list")
        turns = []
    clean_turns: list[dict[str, Any]] = []
    for index, raw in enumerate(turns, start=1):
        if not isinstance(raw, dict):
            warnings.append(f"dropped non-object dialogue turn at index {index}")
            continue
        turn = {
            "operation": str(raw.get("operation") or "").strip(),
            "user_utterance": str(raw.get("user_utterance") or "").strip(),
            "introduced_units": _dedupe_strings(raw.get("introduced_units")),
            "intent_delta": str(raw.get("intent_delta") or "").strip(),
        }
        clean_turns.append(turn)
    data["turns"] = clean_turns
    if contains_benchmark_metadata(data):
        hard.append("dialogue_plan still contains benchmark/private test metadata after sanitization")
    if contains_implementation_hint([turn.get("user_utterance") for turn in clean_turns]):
        hard.append("dialogue_plan.user_utterance contains implementation or patch-like detail")
    return SanitizerResult(data=data, changed=bool(warnings), warnings=warnings, hard_failures=hard)


def sanitize_compact_instance(instance: dict[str, Any]) -> SanitizerResult:
    warnings: list[str] = []
    data = scrub_structure(copy.deepcopy(instance), warnings)
    hard: list[str] = []
    if contains_benchmark_metadata(
        {
            "final_intent": data.get("final_intent"),
            "semantic_capsule": data.get("semantic_capsule"),
            "dialogue": data.get("dialogue"),
            "evaluation_modes": data.get("evaluation_modes"),
            "oracle": data.get("oracle"),
        }
    ):
        hard.append("formal compact instance contains benchmark/private test metadata after sanitization")
    if contains_implementation_hint(
        {
            "dialogue": data.get("dialogue"),
            "evaluation_modes": data.get("evaluation_modes"),
            "oracle_must_satisfy": (data.get("oracle") or {}).get("must_satisfy") if isinstance(data.get("oracle"), dict) else None,
        }
    ):
        hard.append("formal compact instance contains implementation hint in active/user-facing surface")
    return SanitizerResult(data=data, changed=bool(warnings), warnings=warnings, hard_failures=hard)


def safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
