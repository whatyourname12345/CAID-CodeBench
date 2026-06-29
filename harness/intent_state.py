"""Intent-state extraction and judging interfaces."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from harness.schemas import IntentScore, IntentState, TraceEvent


class IntentExtractor(ABC):
    """Extract the agent's current understanding from an interaction trace."""

    @abstractmethod
    def extract(self, trace: list[TraceEvent]) -> IntentState:
        """Return a structured intent snapshot."""
        raise NotImplementedError


class IntentJudge(ABC):
    """Compare a predicted intent state against a gold intent state."""

    @abstractmethod
    def score(self, predicted: IntentState, gold: IntentState) -> IntentScore:
        """Return intent-acquisition scores."""
        raise NotImplementedError


def load_intent_state(path: str | Path) -> IntentState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return IntentState(**data)
