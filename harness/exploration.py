"""Repository exploration evaluation interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from harness.schemas import ExplorationScore, Region, TraceEvent


class ExplorationExtractor(ABC):
    """Extract code regions inspected by an agent from a trace."""

    @abstractmethod
    def extract(self, trace: list[TraceEvent], repo_root: str | Path | None = None) -> list[Region]:
        """Return repository-relative regions in first-seen order."""
        raise NotImplementedError


class ExplorationEvaluator(ABC):
    """Score predicted exploration regions against SWE-Explore-style ground truth."""

    @abstractmethod
    def score(self, predicted: list[Region], ground_truth: dict[str, Any]) -> ExplorationScore:
        """Return line/file/region exploration metrics."""
        raise NotImplementedError
