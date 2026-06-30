from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from cair_v2.batch.batch_state import BatchState


REVIEW_STATUSES = {
    "manual_review_required",
    "needs_revision",
    "review_failed",
    "step_failed",
    "backtest_failed",
    "validation_failed",
}


def write_manual_review_queue(output_dir: Path, state: BatchState) -> None:
    rows: list[dict[str, Any]] = []
    for instance_id, item in state.data.get("instances", {}).items():
        status = str(item.get("status") or "")
        if status not in REVIEW_STATUSES:
            continue
        rows.append(
            {
                "instance_id": instance_id,
                "repo": item.get("repo"),
                "status": status,
                "failure_reason": item.get("failure_reason") or item.get("last_error"),
                "quality_gate_summary": item.get("quality_gate_summary"),
                "suggested_fix": item.get("suggested_fix"),
                "path": item.get("path"),
                "models_used": json.dumps(item.get("models_used") or {}, ensure_ascii=False),
                "escalated_steps": json.dumps(item.get("escalations") or [], ensure_ascii=False),
            }
        )
    path = output_dir / "manual_review_queue.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "instance_id",
                "repo",
                "status",
                "failure_reason",
                "quality_gate_summary",
                "suggested_fix",
                "path",
                "models_used",
                "escalated_steps",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
