"""Shared data schemas for CAIR-CodeBench.

The benchmark keeps SWE-bench's final executable patch check, but adds
dialogue, intent, exploration, and process-level artifacts around it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


def to_jsonable(value: Any) -> Any:
    """Convert dataclass-heavy objects into JSON-serializable structures."""
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(val) for key, val in asdict(value).items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(val) for key, val in value.items()}
    return value


@dataclass(frozen=True)
class Region:
    path: str
    start: int
    end: int

    def as_tuple(self) -> tuple[str, int, int]:
        return (self.path, self.start, self.end)


@dataclass(frozen=True)
class SWEInstance:
    repo: str
    instance_id: str
    base_commit: str
    patch: str
    test_patch: str
    problem_statement: str
    hints_text: str
    created_at: str
    version: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    environment_setup_commit: str


@dataclass(frozen=True)
class UserPersona:
    name: str
    style: Literal["concise", "uncertain", "impatient", "technical", "novice"]
    description: str


@dataclass(frozen=True)
class MisleadingCue:
    trap_type: Literal[
        "wrong_location",
        "wrong_cause",
        "over_fix",
        "underspecified",
        "stale_workaround",
    ]
    user_claim: str
    expected_agent_behavior: str
    severity: Literal["low", "medium", "high"] = "medium"


@dataclass
class IntentState:
    task_id: str
    issue_type: str
    symptom: str
    expected_behavior: str
    observed_behavior: str
    reproduction: str = ""
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    success_tests: list[str] = field(default_factory=list)
    misleading_traps: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class DialogueScenario:
    task_id: str
    repo: str
    base_commit: str
    persona: UserPersona
    initial_user_query: str
    hidden_problem_statement: str
    gold_intent_state_path: str
    misleading_cues: list[MisleadingCue] = field(default_factory=list)
    disclosure_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationSpec:
    task_id: str
    repo: str
    base_commit: str
    test_patch: str
    fail_to_pass: list[str]
    pass_to_pass: list[str]
    patch: str = ""
    environment_setup_commit: str = ""


@dataclass
class TraceEvent:
    event_id: int
    turn_id: int
    actor: Literal["agent", "user", "harness", "tool"]
    event_type: Literal["message", "tool_call", "tool_result", "patch", "score", "state"]
    content: str = ""
    tool: str | None = None
    command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class IntentScore:
    symptom: float = 0.0
    expected_behavior: float = 0.0
    constraints: float = 0.0
    non_goals: float = 0.0
    misleading_resistance: float = 0.0

    @property
    def overall(self) -> float:
        return (
            0.25 * self.symptom
            + 0.25 * self.expected_behavior
            + 0.20 * self.constraints
            + 0.15 * self.non_goals
            + 0.15 * self.misleading_resistance
        )


@dataclass
class ProcessScore:
    clarification: float = 0.0
    evidence_based_debugging: float = 0.0
    misleading_resistance: float = 0.0
    testing_behavior: float = 0.0
    scope_control: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        return (
            self.clarification
            + self.evidence_based_debugging
            + self.misleading_resistance
            + self.testing_behavior
            + self.scope_control
        ) / 5.0


@dataclass
class DialogueQualityScore:
    naturalness: float = 0.0
    coherence: float = 0.0
    information_seeking: float = 0.0
    user_grounding: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        return (
            0.30 * self.naturalness
            + 0.35 * self.coherence
            + 0.20 * self.information_seeking
            + 0.15 * self.user_grounding
        )


@dataclass
class ExplorationScore:
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    hit_file_rate: float = 0.0
    hit_region_rate: float = 0.0
    context_efficiency: float = 0.0
    noise_region_rate: float = 0.0
    first_useful_hit: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def overall(self) -> float:
        return (
            0.20 * self.precision
            + 0.20 * self.hit_file_rate
            + 0.20 * self.hit_region_rate
            + 0.20 * self.context_efficiency
            + 0.10 * self.first_useful_hit
            + 0.10 * (1.0 - self.noise_region_rate)
        )
