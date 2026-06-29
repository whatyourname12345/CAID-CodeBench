"""Process-level evaluation interfaces for interactive coding agents."""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.schemas import IntentState, ProcessScore, TraceEvent


class ProcessJudge(ABC):
    """Score engineering process independent of final patch pass/fail."""

    @abstractmethod
    def score(self, trace: list[TraceEvent], gold_intent: IntentState) -> ProcessScore:
        """Return clarification, evidence, testing, robustness, and scope scores."""
        raise NotImplementedError
