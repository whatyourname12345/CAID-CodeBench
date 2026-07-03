from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cair_v2.llm.prompt_runner import PromptStepResult


FACT_UNIT_TYPES = {
    "symptom",
    "observed_behavior",
    "expected_behavior",
    "reproduction",
    "error_message",
    "environment",
    "affected_component",
    "active_constraint",
    "negative_constraint",
    "regression_expectation",
    "obsolete_candidate",
    "rejected_solution",
    "non_goal",
    "ambiguity_or_correction",
    "conflict_or_tension",
    "implementation_hint",
}

REVISION_FACT_TYPES = {
    "ambiguity_or_correction",
    "rejected_solution",
    "obsolete_candidate",
    "negative_constraint",
    "regression_expectation",
    "conflict_or_tension",
}

REVISION_OPERATIONS = {
    "correct_previous_claim",
    "retract_previous_claim",
    "replace_previous_claim",
    "resolve_conflict",
    "narrow_scope",
    "broaden_scope",
    "add_regression_constraint",
}

ALLOWED_DIALOGUE_OPERATIONS = {
    "initial_imperfect_report",
    "add_detail",
    "speculative_hypothesis",
    "mistaken_clarification",
    "incorrect_reproduction_detail",
    "correct_previous_claim",
    "retract_previous_claim",
    "replace_previous_claim",
    "resolve_conflict",
    "narrow_scope",
    "broaden_scope",
    "add_missing_detail",
    "add_reproduction_detail",
    "add_regression_constraint",
    "confirm_final_active_intent",
}

STAGED_STEPS = [
    "fact_extraction",
    "intent_revision",
    "initial_report_plan",
    "noisy_revision_event_plan",
    "realistic_utterance_realization",
    "semantic_reviewer",
]


@dataclass
class FactUnit:
    unit_id: str
    type: str
    text: str
    source: str
    source_span: str
    active_by_default: bool
    expose_to_user: bool


@dataclass
class FinalIntent:
    objective: str
    must_satisfy: list[str] = field(default_factory=list)
    must_not_satisfy: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    regression_expectations: list[str] = field(default_factory=list)


@dataclass
class RevisionSupport:
    has_revision_fact: bool
    revision_type: str
    revision_unit_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Oracle:
    must_satisfy: list[str] = field(default_factory=list)
    must_not_satisfy: list[str] = field(default_factory=list)
    obsolete_intent_checks: list[str] = field(default_factory=list)
    regression_checks: list[str] = field(default_factory=list)
    forbidden_checks: list[str] = field(default_factory=list)
    clarification_checks: list[str] = field(default_factory=list)


@dataclass
class StageStepResult:
    step_name: str
    ok: bool
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    prompt_result: PromptStepResult | None = None
    retry_prompt_result: PromptStepResult | None = None
    retry_used: bool = False
    retry_success: bool = False
    repair_success: bool = False
    fallback_used: bool = False
    model_used: str | None = None
    error_type: str | None = None

    @property
    def call_results(self) -> list[Any]:
        results: list[Any] = []
        if self.prompt_result and self.prompt_result.call_results:
            results.extend(self.prompt_result.call_results)
        if self.retry_prompt_result and self.retry_prompt_result.call_results:
            results.extend(self.retry_prompt_result.call_results)
        return results

    @property
    def api_calls_made(self) -> int:
        return len(self.call_results)


@dataclass
class StagedConstructionResult:
    ok: bool
    status: str
    semantic_capsule: dict[str, Any] = field(default_factory=dict)
    dialogue_plan: dict[str, Any] = field(default_factory=dict)
    semantic_review: dict[str, Any] = field(default_factory=dict)
    step_results: dict[str, StageStepResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dialogue_source: str = "llm_staged"
    fallback_used: bool = False
    failed_step: str | None = None

    @property
    def api_calls_made(self) -> int:
        return sum(result.api_calls_made for result in self.step_results.values())
