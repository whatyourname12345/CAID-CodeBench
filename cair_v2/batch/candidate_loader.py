from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any


csv.field_size_limit(sys.maxsize)

SOURCE_PRIORITY = {"verified": 0, "lite": 1, "full": 2}
LABEL_PRIORITY = {"ACCEPT_SEED": 0, "HIGH_QUALITY_POOL": 1, "": 2, "None": 2, "nan": 2}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none"}:
            return default
        return float(text)
    except Exception:
        return default


def _int(value: Any, default: int = 9999) -> int:
    try:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none"}:
            return default
        return int(float(text))
    except Exception:
        return default


def load_candidate_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def sort_candidates(rows: list[dict[str, Any]], *, golden_ids: list[str] | None = None, golden_first: bool = True) -> list[dict[str, Any]]:
    golden = set(golden_ids or [])

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        label = str(row.get("manual_override_label") or "")
        source = str(row.get("source_name") or "")
        is_golden = row.get("instance_id") in golden
        return (
            0 if golden_first and is_golden else 1,
            LABEL_PRIORITY.get(label, 2),
            _float(row.get("quality_risk_score"), 0.0),
            -_float(row.get("cair_rule_score"), 0.0),
            SOURCE_PRIORITY.get(source, 3),
            _int(row.get("patch_files_count"), 9999),
            -_int(row.get("fail_to_pass_count"), 0),
            str(row.get("instance_id") or ""),
        )

    return sorted(rows, key=key)


def select_candidates(
    path: Path,
    *,
    limit: int | None,
    include_rejected: bool = False,
    golden_ids: list[str] | None = None,
    golden_first: bool = True,
) -> list[dict[str, Any]]:
    rows = load_candidate_rows(path)
    if not include_rejected:
        rows = [row for row in rows if str(row.get("manual_override_label") or "") != "REJECT_OR_DOWNRANK"]
    rows = sort_candidates(rows, golden_ids=golden_ids, golden_first=golden_first)
    if limit is not None:
        rows = rows[:limit]
    return rows
