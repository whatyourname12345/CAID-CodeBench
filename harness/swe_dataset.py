"""Helpers for reading SWE-bench JSONL data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from harness.schemas import SWEInstance


def parse_json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        if not value:
            return []
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError(f"expected JSON list, got {type(parsed).__name__}")
        return [str(item) for item in parsed]
    raise TypeError(f"cannot parse list from {type(value).__name__}")


def swe_instance_from_row(row: dict[str, object]) -> SWEInstance:
    return SWEInstance(
        repo=str(row["repo"]),
        instance_id=str(row["instance_id"]),
        base_commit=str(row["base_commit"]),
        patch=str(row.get("patch") or ""),
        test_patch=str(row.get("test_patch") or ""),
        problem_statement=str(row.get("problem_statement") or ""),
        hints_text=str(row.get("hints_text") or ""),
        created_at=str(row.get("created_at") or ""),
        version=str(row.get("version") or ""),
        fail_to_pass=parse_json_list(row.get("FAIL_TO_PASS")),
        pass_to_pass=parse_json_list(row.get("PASS_TO_PASS")),
        environment_setup_commit=str(row.get("environment_setup_commit") or ""),
    )


def load_swe_instances(path: str | Path, limit: int | None = None) -> list[SWEInstance]:
    instances: list[SWEInstance] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            instances.append(swe_instance_from_row(json.loads(line)))
            if limit is not None and len(instances) >= limit:
                break
    return instances


def iter_jsonl(path: str | Path) -> Iterable[dict[str, object]]:
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path: str | Path, rows: Iterable[dict[str, object]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def issue_title(problem_statement: str) -> str:
    for line in problem_statement.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("<!--"):
            return stripped.strip("# ")
    return "Repository issue"
