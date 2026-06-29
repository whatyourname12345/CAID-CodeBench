"""Dialogue-quality judging interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.schemas import DialogueQualityScore, TraceEvent


class DialogueJudge(ABC):
    """Score the user-facing quality of an agent dialogue."""

    @abstractmethod
    def score(self, trace: list[TraceEvent]) -> DialogueQualityScore:
        """Return naturalness, coherence, information-seeking, and grounding scores."""
        raise NotImplementedError
