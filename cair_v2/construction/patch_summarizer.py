from __future__ import annotations

import ast
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from cair_v2.construction.instance_io import read_json, write_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATE_CSV = PROJECT_ROOT / "data/candidates/cair_candidate_pool.csv"
csv.field_size_limit(sys.maxsize)


def _parse_list_like(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "[]"}:
        return []
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return [text]


def split_paths(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[;\n,]+", text) if part.strip()]


def load_candidate_record(instance_id: str, candidate_csv: Path = DEFAULT_CANDIDATE_CSV) -> dict[str, Any] | None:
    if not candidate_csv.exists():
        return None
    with candidate_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("instance_id") == instance_id:
                return dict(row)
    return None


def diff_stats(diff_text: str) -> dict[str, int]:
    added = 0
    removed = 0
    hunks = 0
    for line in str(diff_text or "").splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"added_lines": added, "removed_lines": removed, "hunks": hunks}


def component_names(paths: list[str]) -> list[str]:
    components: list[str] = []
    for path in paths:
        clean = path.strip()
        if not clean:
            continue
        parts = [part for part in clean.split("/") if part]
        if len(parts) >= 2:
            components.append("/".join(parts[:2]))
        elif parts:
            components.append(parts[0])
    return sorted(set(components))


def summarize_patch_record(record: dict[str, Any], existing_metadata: dict[str, Any]) -> dict[str, Any]:
    source_files = split_paths(record.get("source_files_touched") or existing_metadata.get("source_files_touched"))
    test_files = split_paths(record.get("test_files_touched") or existing_metadata.get("test_files_touched"))
    patch_stats = diff_stats(record.get("patch", ""))
    test_stats = diff_stats(record.get("test_patch", ""))
    fail_to_pass = _parse_list_like(record.get("fail_to_pass"))
    pass_to_pass = _parse_list_like(record.get("pass_to_pass"))

    change_size = patch_stats["added_lines"] + patch_stats["removed_lines"]
    if change_size <= 40:
        size_label = "small"
    elif change_size <= 160:
        size_label = "medium"
    else:
        size_label = "large"

    leakage_level = "low"
    if change_size > 160 or len(source_files) > 3:
        leakage_level = "medium"
    if len(source_files) > 8 or change_size > 500:
        leakage_level = "high"

    return {
        "patch_files_count": existing_metadata.get("patch_files_count") or record.get("patch_files_count"),
        "test_files_count": existing_metadata.get("test_files_count") or record.get("test_files_count"),
        "source_files_touched": existing_metadata.get("source_files_touched") or ";".join(source_files),
        "test_files_touched": existing_metadata.get("test_files_touched") or ";".join(test_files),
        "patch_summary": {
            "touched_source_files": source_files,
            "touched_test_files": test_files,
            "change_size": {
                "label": size_label,
                **patch_stats,
            },
            "likely_affected_components": component_names(source_files),
            "patch_risk_notes": [
                "Safe summary only; full reference diff is intentionally excluded.",
                "Use file/component scope for oracle feasibility, not for user dialogue implementation hints.",
            ],
            "implementation_leakage_level": leakage_level,
        },
        "test_patch_summary": {
            "added_or_modified_test_files": test_files,
            "test_behavior_hints": [
                f"FAIL_TO_PASS count: {len(fail_to_pass)}",
                f"PASS_TO_PASS count: {len(pass_to_pass)}",
                "Detailed test function names and assertions are excluded from dialogue generation.",
            ],
            "oracle_relevance": "Use public issue behavior plus FAIL_TO_PASS/PASS_TO_PASS metadata to build intent oracle.",
            "hidden_test_leakage_risk": "medium" if fail_to_pass else "low",
            "change_size": {
                "label": "small" if test_stats["added_lines"] + test_stats["removed_lines"] <= 80 else "medium",
                **test_stats,
            },
        },
    }


def metadata_has_todo(metadata: dict[str, Any]) -> bool:
    text = json.dumps(metadata, ensure_ascii=False)
    return "TODO" in text


def update_patch_metadata(instance_dir: Path, candidate_csv: Path = DEFAULT_CANDIDATE_CSV, force: bool = False) -> bool:
    source_record = read_json(instance_dir / "source_record.json")
    metadata_path = instance_dir / "patch_metadata.json"
    existing = read_json(metadata_path)
    if not force and not metadata_has_todo(existing):
        return False

    instance_id = str(source_record.get("instance_id"))
    record = load_candidate_record(instance_id, candidate_csv)
    if record is None:
        return False

    summarized = summarize_patch_record(record, existing)
    write_json(metadata_path, summarized)
    return True
