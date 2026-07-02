"""Normalized status semantics and bounded retry policy for v2_noisy_refinement.

This module is a *semantic layer* on top of the legacy construction ``status``
field. It does not change how construction routes instances; it only re-labels
the terminal outcome so that downstream funnel statistics and CAIR-Core
selection can be computed without implying that any sample was inspected by a
human.

Key ideas
---------
- ``manual_review_required`` is a legacy compatibility status. Semantically it
  means ``auto_filtered``: construction finished, an automatic quality gate
  decided the sample is unsuitable for CAIR-Core, and **no human review is
  expected** (``human_review_expected=False``).
- CAIR-Core = ``normalized_status == "accepted"`` only.
- Bounded retry never re-samples indefinitely. Only a small set of
  generation-boundary auto_filter subclasses and *transient* technical
  failures are retry-eligible, and each is capped at a single full-chain
  re-execution (``max_full_chain_attempts == 2``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ACCEPTED_STATUSES = {"accepted", "accepted_with_template_dialogue"}

# Canonical auto_filter reasons (see docs/noisy_refinement_status_taxonomy.md).
AUTO_FILTER_REASONS = {
    "insufficient_source_facts_for_noisy_refinement",
    "no_withheld_units_for_later_refinement",
    "would_degenerate_into_progressive_disclosure",
    "invalid_noisy_revision_event_plan",
    "unsupported_noisy_or_wrong_claim",
    "weak_or_missing_revision_evidence",
    "fact_grounding_validation_failed",
    "source_span_unmatched_after_retry",
    "realization_leakage_manual_review",
    "utterance_template_artifact",
    "benchmarkish_or_user_facing_implementation_hint",
    "quality_gate_manual_review",
    "localization_not_ready",
    "semantic_reviewer_not_pass",
}

# auto_filter subclasses that reflect generation-boundary instability. These
# get ONE bounded full-chain re-execution. Stage-level targeted retry has
# already been consumed inside the staged step_runner during the attempt.
AUTO_FILTER_RETRYABLE = {
    "utterance_template_artifact",
    "benchmarkish_or_user_facing_implementation_hint",
    "realization_leakage_manual_review",  # manual-review only; private leakage is rejected
    "invalid_noisy_revision_event_plan",
    "semantic_reviewer_not_pass",
}

# auto_filter subclasses that reflect genuine content unsuitability. These are
# never retried; re-sampling would only be gaming the accept rate.
AUTO_FILTER_NON_RETRYABLE = {
    "insufficient_source_facts_for_noisy_refinement",
    "no_withheld_units_for_later_refinement",
    "would_degenerate_into_progressive_disclosure",
    "weak_or_missing_revision_evidence",
    "fact_grounding_validation_failed",
    "source_span_unmatched_after_retry",
    "unsupported_noisy_or_wrong_claim",
    "localization_not_ready",
    "quality_gate_manual_review",
}

# Transient technical error signatures eligible for a single re-execution.
TRANSIENT_ERROR_KEYS = {
    "timeout",
    "empty_response",
    "rate_limited",
    "server_error",
    "invalid_json",
    "truncated_json",
}

MAX_FULL_CHAIN_ATTEMPTS = 2


@dataclass
class RetryPlan:
    eligible: bool
    policy: str  # "none" | "stage_retry_once" | "full_chain_retry_once"
    reason: str
    max_full_chain_attempts: int = 1


def normalize_status(status: str) -> str:
    """Map a legacy construction status to a normalized semantic status."""
    if status in ACCEPTED_STATUSES:
        return "accepted"
    if status == "rejected":
        return "rejected"
    if status == "step_failed":
        return "step_failed"
    if status in {"pending", "running"}:
        return status
    # manual_review_required and every other non-accepted, non-technical
    # terminal state is an automatic filter, never a human queue.
    return "auto_filtered"


def _text(*values: Any) -> str:
    return " ".join(str(value) for value in values if value).lower()


def resolve_filter_reason(item: dict[str, Any], quality_report: dict[str, Any]) -> str:
    """Resolve a canonical auto_filter reason from state item + quality report."""
    failure_reason = str(item.get("failure_reason") or "")
    manual_reason = str(item.get("manual_review_reason") or "")
    summary = str(item.get("quality_gate_summary") or "")
    blob = _text(failure_reason, manual_reason, summary)

    noisy = quality_report.get("noisy_refinement") if isinstance(quality_report.get("noisy_refinement"), dict) else {}
    localization = quality_report.get("localization") if isinstance(quality_report.get("localization"), dict) else {}
    old_pattern = item.get("old_progressive_disclosure_pattern")
    if old_pattern in (None, "not_generated"):
        old_pattern = noisy.get("old_progressive_disclosure_pattern")

    # 1. Grounding / source-span alignment (fact_extraction).
    if "fact_grounding_validation_failed" in blob or "aligned to source" in blob:
        if "source_span" in blob or "aligned to source" in blob:
            return "source_span_unmatched_after_retry"
        return "fact_grounding_validation_failed"
    # 2. Realization-stage artifacts / leakage.
    if "utterance_template_artifact" in blob or "template artifact" in blob:
        return "utterance_template_artifact"
    if "benchmark" in blob and ("user-facing" in blob or "user facing" in blob or "implementation hint" in blob):
        return "benchmarkish_or_user_facing_implementation_hint"
    if (
        "realization_leakage_manual_review" in blob
        or "leaks forbidden" in blob
        or "benchmark/private wording" in blob
    ):
        return "realization_leakage_manual_review"
    # 3. Event-plan structural failure.
    if "invalid_noisy_revision_event_plan" in blob:
        return "invalid_noisy_revision_event_plan"
    # 4. semantic_reviewer boundary decision (checked before generic revision
    #    text so a reviewer verdict that merely mentions "revision" is not
    #    misrouted to the non-retryable weak-evidence bucket).
    if failure_reason == "semantic_reviewer" or item.get("review_stage") == "semantic_reviewer" or item.get("failed_stage") == "semantic_reviewer":
        return "semantic_reviewer_not_pass"
    # 5. Withheld-unit / progressive-disclosure degeneration.
    if "no_withheld_units_for_later_refinement" in blob:
        return "no_withheld_units_for_later_refinement"
    if "would_degenerate_into_progressive_disclosure" in blob or old_pattern is True:
        return "would_degenerate_into_progressive_disclosure"
    # 6. Explicit missing revision evidence (specific phrases only).
    if (
        "no_supported_revision_fact" in blob
        or "no supported revision fact" in blob
        or "no source-grounded revision" in blob
    ):
        return "weak_or_missing_revision_evidence"
    if "insufficient_source_facts_for_noisy_refinement" in blob:
        return "insufficient_source_facts_for_noisy_refinement"
    # 7. Localization readiness.
    if str(localization.get("status") or "") == "not_ready" or item.get("localization_checkpoint_ready") is False:
        if "localization" in blob:
            return "localization_not_ready"
    # 8. Remaining automatic quality-gate manual-review outcomes.
    return "quality_gate_manual_review"


def _is_transient_step_failure(item: dict[str, Any]) -> bool:
    failure_reason = str(item.get("failure_reason") or "").lower()
    if "exception" in failure_reason or "schema" in failure_reason:
        return False
    stats = item.get("llm_stats") if isinstance(item.get("llm_stats"), dict) else {}
    saw_transient = False
    for step_stats in stats.values():
        if not isinstance(step_stats, dict):
            continue
        if int(step_stats.get("client_exception", 0) or 0) > 0:
            return False
        for key in TRANSIENT_ERROR_KEYS:
            if int(step_stats.get(key, 0) or 0) > 0:
                saw_transient = True
    return saw_transient


def decide_retry(normalized_status: str, filter_reason: str, item: dict[str, Any]) -> RetryPlan:
    """Decide the bounded retry policy for a terminal attempt outcome."""
    if normalized_status == "auto_filtered":
        if filter_reason in AUTO_FILTER_RETRYABLE:
            return RetryPlan(
                eligible=True,
                policy="full_chain_retry_once",
                reason="generation_boundary_instability",
                max_full_chain_attempts=MAX_FULL_CHAIN_ATTEMPTS,
            )
        return RetryPlan(eligible=False, policy="none", reason="not_retryable_content_unsuitability")
    if normalized_status == "step_failed":
        if _is_transient_step_failure(item):
            return RetryPlan(
                eligible=True,
                policy="full_chain_retry_once",
                reason="transient_technical_failure",
                max_full_chain_attempts=MAX_FULL_CHAIN_ATTEMPTS,
            )
        return RetryPlan(eligible=False, policy="none", reason="deterministic_technical_failure")
    # accepted / rejected / pending / running
    return RetryPlan(eligible=False, policy="none", reason="not_applicable")


def build_normalized_fields(
    *,
    status: str,
    filter_reason: str | None,
    filter_stage: str | None,
    retry_plan: RetryPlan,
) -> dict[str, Any]:
    """Build the normalized-status field bundle for one terminal outcome."""
    normalized = normalize_status(status)
    fields: dict[str, Any] = {
        "normalized_status": normalized,
        "review_mode": "auto_filter" if normalized == "auto_filtered" else "none",
        "human_review_expected": False,
    }
    if normalized == "auto_filtered":
        fields["filter_reason"] = filter_reason or "quality_gate_manual_review"
        fields["filter_stage"] = filter_stage or "quality_gate"
        fields["retry_eligible"] = retry_plan.eligible
        fields["retry_policy"] = retry_plan.policy
        fields["retry_reason"] = retry_plan.reason
        if retry_plan.policy == "full_chain_retry_once":
            fields["max_full_chain_attempts"] = retry_plan.max_full_chain_attempts
    elif normalized == "step_failed":
        fields["retry_eligible"] = retry_plan.eligible
        fields["retry_policy"] = retry_plan.policy
        fields["retry_reason"] = retry_plan.reason
    return fields


def build_attempt_record(
    *,
    attempt_id: int,
    status: str,
    item: dict[str, Any],
    quality_report: dict[str, Any],
    filter_reason: str | None,
    api_calls: int,
    agent_view_leakage_scan: str = "not_run",
) -> dict[str, Any]:
    """Build a single bounded-retry attempt record."""
    noisy = quality_report.get("noisy_refinement") if isinstance(quality_report.get("noisy_refinement"), dict) else {}
    old_pattern = item.get("old_progressive_disclosure_pattern")
    if old_pattern in (None,):
        old_pattern = noisy.get("old_progressive_disclosure_pattern")
    unresolved = item.get("unresolved_wrong_claims")
    if unresolved in (None,):
        unresolved = noisy.get("unresolved_wrong_claims")
    scenario_fit = item.get("scenario_fit") or noisy.get("scenario_fit") or "not_generated"
    return {
        "attempt_id": attempt_id,
        "status": status,
        "normalized_status": normalize_status(status),
        "stage": item.get("failed_stage") or item.get("review_stage") or item.get("current_step"),
        "failure_reason": item.get("failure_reason"),
        "filter_reason": filter_reason,
        "api_calls": int(api_calls),
        "quality_gate_summary": item.get("quality_gate_summary"),
        "old_progressive_disclosure_pattern": old_pattern,
        "unresolved_wrong_claims": unresolved,
        "scenario_fit": scenario_fit,
        "agent_view_leakage_scan": agent_view_leakage_scan,
    }
