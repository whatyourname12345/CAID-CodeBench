from __future__ import annotations

import json
from typing import Any


EVALUATOR_ONLY_EVALUATION_KEYS = {"oracle_intent_prompt"}


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _agent_semantic_capsule(capsule: dict[str, Any]) -> dict[str, Any]:
    clean = _deep_copy(capsule)
    suitability = clean.get("suitability")
    if isinstance(suitability, dict):
        suitability.pop("reason", None)
    units = clean.get("fact_units")
    if isinstance(units, list):
        clean["fact_units"] = [
            unit
            for unit in units
            if isinstance(unit, dict)
            and unit.get("expose_to_user") is not False
            and unit.get("type") != "implementation_hint"
        ]
    return clean


def agent_view_payload(instance: dict[str, Any]) -> dict[str, Any]:
    """Return the public agent view without evaluator-only fields."""

    clean = _deep_copy(instance)
    localization = clean.get("localization_checkpoint")
    if isinstance(localization, dict):
        localization.pop("gold", None)

    modes = clean.get("evaluation_modes")
    if isinstance(modes, dict):
        for key in EVALUATOR_ONLY_EVALUATION_KEYS:
            modes.pop(key, None)

    semantic = clean.get("semantic_capsule")
    if isinstance(semantic, dict):
        clean["semantic_capsule"] = _agent_semantic_capsule(semantic)

    metadata = clean.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("golden_instance", None)

    clean.pop("oracle", None)
    return clean
