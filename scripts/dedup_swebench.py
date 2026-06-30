from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_FILES = {
    "verified": "swebench_verified.jsonl",
    "lite": "swebench_lite.jsonl",
    "full": "swebench_full.jsonl",
}

SOURCE_PRIORITY = {"verified": 0, "lite": 1, "full": 2}

OUTPUT_COLUMNS = [
    "instance_id",
    "repo",
    "source_name",
    "hf_dataset",
    "hf_split",
    "base_commit",
    "problem_statement",
    "hints_text",
    "created_at",
    "version",
    "patch",
    "test_patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "fail_to_pass",
    "pass_to_pass",
    "issue_url",
    "pr_url",
]


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} line {line_no}: {exc}") from exc
    return rows


def first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return ""


def serialize(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_record(record: dict[str, Any], source_name: str) -> dict[str, Any]:
    fail_to_pass = first_value(record, "FAIL_TO_PASS", "fail_to_pass")
    pass_to_pass = first_value(record, "PASS_TO_PASS", "pass_to_pass")
    return {
        "instance_id": str(first_value(record, "instance_id")).strip(),
        "repo": first_value(record, "repo"),
        "source_name": source_name,
        "hf_dataset": first_value(record, "_hf_dataset", "hf_dataset"),
        "hf_split": first_value(record, "_hf_split", "split"),
        "base_commit": first_value(record, "base_commit"),
        "problem_statement": first_value(record, "problem_statement"),
        "hints_text": first_value(record, "hints_text"),
        "created_at": first_value(record, "created_at"),
        "version": first_value(record, "version"),
        "patch": first_value(record, "patch"),
        "test_patch": first_value(record, "test_patch"),
        "FAIL_TO_PASS": serialize(fail_to_pass),
        "PASS_TO_PASS": serialize(pass_to_pass),
        "fail_to_pass": serialize(fail_to_pass),
        "pass_to_pass": serialize(pass_to_pass),
        "issue_url": first_value(record, "issue_url"),
        "pr_url": first_value(record, "pr_url"),
    }


def load_raw_records(raw_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    records: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for source_name in ["full", "lite", "verified"]:
        path = raw_dir / RAW_FILES[source_name]
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file: {path}. Run scripts/download_swebench.py first.")
        raw_rows = read_jsonl(path)
        counts[source_name] = len(raw_rows)
        for row in raw_rows:
            normalized = normalize_record(row, source_name)
            if normalized["instance_id"]:
                records.append(normalized)
    return records, counts


def dedup_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_rows = sorted(
        records,
        key=lambda row: (
            SOURCE_PRIORITY.get(str(row.get("source_name")), 99),
            str(row.get("instance_id") or ""),
        ),
    )
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in sorted_rows:
        instance_id = str(row.get("instance_id") or "")
        if instance_id in seen:
            continue
        seen.add(instance_id)
        deduped.append(row)
    return deduped


def write_report(path: Path, *, raw_counts: dict[str, int], original_count: int, deduped: list[dict[str, Any]]) -> None:
    source_counts = pd.Series([row["source_name"] for row in deduped]).value_counts().to_dict()
    repos = pd.Series([row["repo"] for row in deduped]).nunique()
    lines = [
        "# SWE-bench Dedup Report",
        "",
        "Priority: `Verified > Lite > Full`.",
        "",
        "## Raw Counts",
        "",
        f"- Full: {raw_counts.get('full', 0)}",
        f"- Lite: {raw_counts.get('lite', 0)}",
        f"- Verified: {raw_counts.get('verified', 0)}",
        "",
        "## Merge",
        "",
        f"- Original merged rows: {original_count}",
        f"- Deduped rows: {len(deduped)}",
        f"- Unique repos after dedup: {repos}",
        "",
        "## Retained Source Counts",
        "",
    ]
    for source_name in ["verified", "lite", "full"]:
        lines.append(f"- {source_name}: {source_counts.get(source_name, 0)}")
    lines.extend(
        [
            "",
            "## Coverage Notes",
            "",
            "- Records duplicated across datasets are retained from the highest-priority source.",
            "- `source_name` records which dataset supplied the retained row.",
            "- The output keeps both uppercase and lowercase FAIL/PASS fields for downstream compatibility.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deduplicate SWE-bench Full/Lite/Verified by instance_id.")
    parser.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data/processed/all_swebench_issues_dedup.csv")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "data/processed/dedup_report.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_dir = resolve(args.raw_dir)
    output = resolve(args.output)
    report = resolve(args.report)
    records, raw_counts = load_raw_records(raw_dir)
    deduped = dedup_records(records)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(deduped, columns=OUTPUT_COLUMNS).to_csv(output, index=False)
    write_report(report, raw_counts=raw_counts, original_count=len(records), deduped=deduped)
    print(f"Original merged rows: {len(records)}")
    print(f"Deduped rows: {len(deduped)}")
    for source_name in ["verified", "lite", "full"]:
        retained = sum(1 for row in deduped if row["source_name"] == source_name)
        print(f"{source_name}: raw={raw_counts.get(source_name, 0)} retained={retained}")
    print(f"Wrote {output}")
    print(f"Wrote {report}")


if __name__ == "__main__":
    main()
