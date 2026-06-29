"""Intent-state judging primitives."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_gold_intent_state(path: str | Path) -> dict[str, Any]:
    """Load a gold intent state JSON file."""
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def exact_match_intent(predicted: dict[str, Any], gold: dict[str, Any]) -> bool:
    """Default deterministic intent judge."""
    return predicted == gold
