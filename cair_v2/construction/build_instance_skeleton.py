from __future__ import annotations

import json
from typing import Any


def _list_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:
            return 0
        return int(value)
    text = str(value or "").strip()
    if not text or text.lower() in {"[]", "none", "nan", "null"}:
        return 0
    if text.isdigit():
        return int(text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return len(parsed)
    except Exception:
        pass
    return text.count(",") + 1 if text.startswith("[") else 1


def _first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return ""


def source_record_from_candidate(record: dict[str, Any]) -> dict[str, Any]:
    """Build the traceable source record used by v2 construction.

    Reference patches and private test lists are intentionally not included in
    this public-facing source record. The batch runner stores a private
    construction copy under `.build/` for evaluator-only localization gold
    extraction and private test-count context.
    """

    return {
        "instance_id": record.get("instance_id"),
        "repo": record.get("repo"),
        "source_name": record.get("source_name"),
        "base_commit": record.get("base_commit"),
        "source_swebench": {
            "problem_statement": record.get("problem_statement"),
            "hints_text": record.get("hints_text"),
            "issue_url": record.get("issue_url"),
            "pr_url": record.get("pr_url"),
        },
        "candidate_metadata": {
            "hf_dataset": record.get("hf_dataset"),
            "private_failing_check_count": _list_count(_first_value(record, "FAIL_TO_PASS", "fail_to_pass")),
            "private_regression_check_count": _list_count(_first_value(record, "PASS_TO_PASS", "pass_to_pass")),
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
