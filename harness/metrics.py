"""Metric helpers for benchmark scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Score:
    task_id: str
    resolved: bool
    tests_passed: bool
    intent_matched: bool | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "resolved": self.resolved,
            "tests_passed": self.tests_passed,
            "intent_matched": self.intent_matched,
        }


def compute_score(task_id: str, tests_passed: bool, intent_matched: bool | None = None) -> Score:
    """Compute the default binary score for a task."""
    resolved = tests_passed if intent_matched is None else tests_passed and intent_matched
    return Score(
        task_id=task_id,
        resolved=resolved,
        tests_passed=tests_passed,
        intent_matched=intent_matched,
    )


def weighted_intermediate_score(scores: dict[str, Any]) -> float:
    """Aggregate non-final interactive metrics.

    The final SWE-bench resolved signal should still be reported separately.
    This score is intended for diagnosing dialogue/process quality.
    """
    weights = {
        "intent": 0.35,
        "process": 0.25,
        "dialogue": 0.20,
        "exploration": 0.20,
    }
    total = 0.0
    used = 0.0
    for key, weight in weights.items():
        value = scores.get(key, {})
        if isinstance(value, dict) and "overall" in value:
            total += weight * float(value["overall"])
            used += weight
    return total / used if used else 0.0


def compute_cair_report(
    task_id: str,
    resolved: bool,
    tests_passed: bool,
    intermediate_scores: dict[str, Any],
) -> dict[str, Any]:
    """Build the standard CAIR-CodeBench report object."""
    return {
        "task_id": task_id,
        "resolved": resolved,
        "tests_passed": tests_passed,
        "intermediate_score": weighted_intermediate_score(intermediate_scores),
        "scores": intermediate_scores,
    }
