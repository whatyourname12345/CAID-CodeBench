from __future__ import annotations

import re
from typing import Any


REVISION_UNIT_TYPES = {
    "rejected_solution",
    "obsolete_candidate",
    "negative_constraint",
    "regression_expectation",
    "ambiguity_or_correction",
    "design_suggestion_non_goal",
    "workaround_to_reject",
    "conflict_or_tension",
}
USER_STOP_PHRASES = [
    "likely in",
    "implementation",
    "reference patch",
    "diff",
]

DANGLING_END_RE = re.compile(
    r"\b(?:when using|that|when|and|or|with|in|of|for|to|from|private|desired|expected|indicating|including|because|where)$",
    re.IGNORECASE,
)


def _units(capsule: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    wanted = set(types)
    return [
        unit
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict)
        and unit.get("type") in wanted
        and unit.get("expose_to_user") is not False
        and unit.get("type") != "implementation_hint"
    ]


def _revision_units(capsule: dict[str, Any]) -> list[dict[str, Any]]:
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    revision_ids = [str(unit_id) for unit_id in revision_support.get("revision_unit_ids", []) if str(unit_id).strip()]
    units = {
        str(unit.get("unit_id")): unit
        for unit in capsule.get("fact_units", [])
        if isinstance(unit, dict)
        and unit.get("expose_to_user") is not False
        and unit.get("type") in REVISION_UNIT_TYPES
    }
    return [units[unit_id] for unit_id in revision_ids if unit_id in units]


def _first_text(units: list[dict[str, Any]], fallback: str, *, max_chars: int | None = None) -> str:
    text = _clean_text(str(units[0].get("text") if units else fallback))
    if max_chars:
        text = _shorten(text, max_chars)
    return text


def _ids(units: list[dict[str, Any]], limit: int = 2) -> list[str]:
    return [str(unit.get("unit_id")) for unit in units[:limit] if str(unit.get("unit_id") or "").strip()]


def _clean_text(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", str(text or ""))
    text = re.sub(r"\b[a-zA-Z0-9_./-]+\.py\b", "the relevant code", text)
    text = re.sub(r"\b[A-Za-z_][A-Za-z0-9_.]*\([^)]*\)", "that call", text)
    for phrase in USER_STOP_PHRASES:
        text = re.sub(re.escape(phrase) + r".*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .;:")
    return text


def _shorten(text: str, max_chars: int) -> str:
    text = str(text or "").strip(" .;:")
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    cut = cut.rstrip(" ,.;:")
    while DANGLING_END_RE.search(cut):
        cut = DANGLING_END_RE.sub("", cut).rstrip(" ,.;:")
    return cut + "."


def _contains(text: str, *needles: str) -> bool:
    lowered = text.lower()
    return any(needle in lowered for needle in needles)


def _vague_t1(symptom: list[dict[str, Any]], observed: list[dict[str, Any]], final_intent: dict[str, Any]) -> str:
    source = _first_text(symptom + observed, str(final_intent.get("objective") or "this behavior is wrong"), max_chars=160)
    lowered = source.lower()
    if _contains(lowered, "clone"):
        return "Clone is failing in one estimator-parameter case."
    if _contains(lowered, "dataset", "repr", "unit"):
        return "The dataset display is missing some unit information."
    if _contains(lowered, "pylint", "plugin"):
        return "A pylint plugin option seems to be ignored."
    if _contains(lowered, "warning"):
        return "A warning-related case isn't behaving as expected."
    if _contains(lowered, "error", "exception", "typeerror", "traceback"):
        return "I'm hitting an error in one specific case."
    if _contains(lowered, "fails", "failure", "failed"):
        return "One specific case is failing unexpectedly."
    words = source.split()
    fragment = " ".join(words[:9]).strip(" ,.;:")
    if not fragment:
        return "One specific behavior looks wrong."
    utterance = f"{fragment} seems off."
    return utterance[:89].rstrip(" ,.;:") + ("." if not utterance.endswith(".") else "")


def _context_utterance(context: list[dict[str, Any]]) -> str:
    text = _first_text(context, "a specific component", max_chars=100)
    if _contains(text, "Dataset", "repr", "unit"):
        return "It seems tied to how Dataset repr shows variables and coordinates."
    if _contains(text, "clone", "estimator"):
        return "It happens when cloning with an estimator class used as a parameter."
    if _contains(text, "pylint", "plugin", "--load"):
        return "It happens around the plugin-loading command-line option."
    return f"It seems tied to {text}."


def _refine_utterance(observed: list[dict[str, Any]], expected: list[dict[str, Any]]) -> str:
    obs = _first_text(observed, "", max_chars=70)
    exp = _first_text(expected, "", max_chars=62)
    if obs and exp:
        return _shorten(f"I see {obs}; I expected {exp}.", 125)
    if obs:
        return _shorten(f"The concrete symptom is: {obs}.", 115)
    if exp:
        return _shorten(f"What I need is: {exp}.", 115)
    return "The observed behavior doesn't match what I expected."


def _revision_utterance(revision: list[dict[str, Any]]) -> str:
    unit = revision[0]
    text = _first_text([unit], "that workaround", max_chars=105)
    unit_type = str(unit.get("type") or "")
    if unit_type == "negative_constraint":
        return _shorten(f"Please don't solve it in a way that violates this: {text}.", 125)
    if unit_type == "design_suggestion_non_goal":
        return _shorten(f"That related idea isn't the core requirement: {text}.", 120)
    if unit_type == "workaround_to_reject":
        return _shorten(f"I don't want to treat this workaround as the real fix: {text}.", 125)
    if unit_type == "ambiguity_or_correction":
        return _shorten(f"Correction: my earlier read may be off here: {text}.", 120)
    if unit_type == "conflict_or_tension":
        return _shorten(f"There's a compatibility concern too: {text}.", 120)
    if unit_type == "regression_expectation":
        return _shorten(f"Also, please preserve the old behavior here: {text}.", 120)
    if unit_type in {"rejected_solution", "obsolete_candidate"}:
        return _shorten(f"I don't want the solution to just be: {text}.", 120)
    return _shorten(f"To clarify, this part should stay inactive: {text}.", 120)


def _regression_utterance(regression: list[dict[str, Any]]) -> str:
    text = _first_text(regression, "the existing behavior", max_chars=75)
    return _shorten(f"Please preserve this existing behavior: {text}.", 115)


def _confirm_utterance(final_intent: dict[str, Any], expected: list[dict[str, Any]]) -> str:
    must = final_intent.get("must_satisfy")
    if isinstance(must, list) and must:
        text = _clean_text(str(must[0]))
        return _shorten(f"Yes, that's the target behavior: {text}.", 115)
    text = _first_text(expected, "that final behavior", max_chars=95)
    return _shorten(f"Yes, that's the target behavior: {text}.", 115)


def build_template_dialogue_plan(capsule: dict[str, Any]) -> dict[str, Any]:
    symptom = _units(capsule, "symptom")
    context = _units(capsule, "affected_component", "reproduction", "boundary_case")
    observed = _units(capsule, "observed_behavior", "error_message")
    expected = _units(capsule, "expected_behavior", "active_constraint", "negative_constraint")
    revision = _revision_units(capsule)
    regression = _units(capsule, "regression_expectation")
    final_intent = capsule.get("final_intent") if isinstance(capsule.get("final_intent"), dict) else {}
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    revision_supported = revision_support.get("has_revision_fact") is True and bool(revision)

    turns: list[dict[str, Any]] = [
        {
            "operation": "reveal_vague_goal",
            "user_utterance": _vague_t1(symptom, observed, final_intent),
            "introduced_units": _ids(symptom, 1),
            "intent_delta": "Reveal a vague symptom without full context.",
        }
    ]
    if context:
        turns.append(
            {
                "operation": "add_information",
                "user_utterance": _context_utterance(context),
                "introduced_units": _ids(context, 2),
                "intent_delta": "Add affected area or reproduction context.",
            }
        )
    if observed or expected:
        turns.append(
            {
                "operation": "refine",
                "user_utterance": _refine_utterance(observed, expected),
                "introduced_units": _ids(observed + expected, 3),
                "intent_delta": "Refine the symptom into observed and expected behavior.",
            }
        )
    if revision:
        turns.append(
            {
                "operation": "reject" if revision[0].get("type") not in {"regression_expectation", "conflict_or_tension", "ambiguity_or_correction"} else "correct",
                "user_utterance": _revision_utterance(revision),
                "introduced_units": _ids(revision, 2),
                "intent_delta": "Reject or obsolete a tempting but non-final interpretation.",
            }
        )
    if regression:
        turns.append(
            {
                "operation": "add_regression_constraint",
                "user_utterance": _regression_utterance(regression),
                "introduced_units": _ids(regression, 2),
                "intent_delta": "Add regression preservation constraint.",
            }
        )
    turns.append(
        {
            "operation": "confirm",
            "user_utterance": _confirm_utterance(final_intent, expected),
            "introduced_units": _ids(expected, 2),
            "intent_delta": "Confirm the final active intent.",
        }
    )

    # Keep the template compact. If all optional turns exist, this is at most 6 turns.
    return {
        "turns": turns[:6],
        "_source": "template",
        "_template_quality": {
            "revision_fact_supported": revision_supported,
            "status": "candidate" if revision_supported else "manual_review_required",
            "revision_unit_ids": [str(unit.get("unit_id")) for unit in revision],
            "notes": [] if revision_supported else ["No revision_support.revision_unit_ids fact supports a revision turn."],
        },
    }
