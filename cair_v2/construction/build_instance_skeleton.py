from __future__ import annotations

from typing import Any


def source_record_from_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """Build the traceable source record used by v2 construction.

    The full reference patch is intentionally not included in this public-facing
    source record. The batch runner stores a private construction copy under
    `.build/` for evaluator-only localization gold extraction.
    """

    return {
        "instance_id": record.get("instance_id"),
        "repo": record.get("repo"),
        "source_name": record.get("source_name"),
        "base_commit": record.get("base_commit"),
        "source_swebench": {
            "problem_statement": record.get("problem_statement"),
            "hints_text": record.get("hints_text"),
            "fail_to_pass": record.get("fail_to_pass"),
            "pass_to_pass": record.get("pass_to_pass"),
            "issue_url": record.get("issue_url"),
            "pr_url": record.get("pr_url"),
        },
        "candidate_metadata": {
            "hf_dataset": record.get("hf_dataset"),
            "cair_rule_score": record.get("cair_rule_score"),
            "quality_risk_score": record.get("quality_risk_score"),
            "quality_risk_reasons": record.get("quality_risk_reasons"),
            "manual_override_label": record.get("manual_override_label"),
            "manual_recommended_categories": record.get("manual_recommended_categories"),
            "manual_review_tier": record.get("manual_review_tier"),
        },
    }


def patch_metadata_from_candidate(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "patch_files_count": record.get("patch_files_count"),
        "test_files_count": record.get("test_files_count"),
        "source_files_touched": record.get("source_files_touched"),
        "test_files_touched": record.get("test_files_touched"),
        "patch_summary": "TODO: summarize changed areas without revealing reference patch implementation.",
        "test_patch_summary": "TODO: summarize test intent without exposing hidden tests or exact assertions.",
    }
