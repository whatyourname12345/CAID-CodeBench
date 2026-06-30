from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {
    "pending",
    "running",
    "step_failed",
    "needs_revision",
    "review_failed",
    "backtest_failed",
    "validation_failed",
    "accepted",
    "accepted_with_template_dialogue",
    "rejected",
    "manual_review_required",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BatchState:
    path: Path
    data: dict[str, Any]

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        *,
        batch_id: str,
        input_file: Path,
        output_dir: Path,
        model_generator: str,
        model_critical: str,
        model_reviewer: str,
    ) -> "BatchState":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(path=path, data=data)
        data = {
            "batch_id": batch_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "input_file": str(input_file),
            "output_dir": str(output_dir),
            "model_generator": model_generator,
            "model_critical": model_critical,
            "model_reviewer": model_reviewer,
            "api_calls": 0,
            "instances": {},
        }
        state = cls(path=path, data=data)
        state.save()
        return state

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @property
    def api_calls(self) -> int:
        return int(self.data.get("api_calls", 0))

    def increment_api_calls(self, count: int = 1) -> None:
        self.data["api_calls"] = self.api_calls + count

    def ensure_instance(self, instance_id: str, repo: str | None = None, path: str | None = None) -> dict[str, Any]:
        instances = self.data.setdefault("instances", {})
        if instance_id not in instances:
            instances[instance_id] = {
                "status": "pending",
                "current_step": None,
                "attempts": 0,
                "api_calls": 0,
                "models_used": {},
                "escalations": [],
                "last_error": None,
                "failure_reason": None,
                "quality_gate": None,
                "reviewer": None,
                "semantic_capsule": None,
                "dialogue_plan": None,
                "localization_checkpoint_ready": None,
                "gold_files_count": None,
                "gold_functions_count": None,
                "function_gold_available": None,
                "release_candidate": False,
                "repo": repo,
                "path": path,
                "golden_instance": instance_id == "django__django-14011",
            }
        elif path:
            instances[instance_id]["path"] = path
        return instances[instance_id]

    def update_instance(self, instance_id: str, **updates: Any) -> dict[str, Any]:
        item = self.ensure_instance(instance_id)
        if "status" in updates and updates["status"] not in ALLOWED_STATUSES:
            raise ValueError(f"Unknown batch instance status: {updates['status']}")
        item.update(updates)
        return item

    def add_model_used(self, instance_id: str, step: str, model: str) -> None:
        item = self.ensure_instance(instance_id)
        item.setdefault("models_used", {})[step] = model

    def add_escalation(self, instance_id: str, step: str, from_model: str, to_model: str, reason: str) -> None:
        item = self.ensure_instance(instance_id)
        item.setdefault("escalations", []).append(
            {"step": step, "from": from_model, "to": to_model, "reason": reason, "created_at": utc_now()}
        )

    def add_instance_api_call(self, instance_id: str, count: int = 1) -> None:
        item = self.ensure_instance(instance_id)
        item["api_calls"] = int(item.get("api_calls", 0)) + count
        self.increment_api_calls(count)
