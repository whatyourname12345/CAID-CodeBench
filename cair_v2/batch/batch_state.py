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
        reset: bool = False,
    ) -> "BatchState":
        if path.exists() and not reset:
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
                "llm_stats": {},
                "escalations": [],
                "last_error": None,
                "failure_reason": None,
                "quality_gate": None,
                "reviewer": None,
                "semantic_capsule": None,
                "dialogue_plan": None,
                "mode": "staged-llm",
                "dialogue_strategy": "staged",
                "dialogue_plan_llm_success": False,
                "dialogue_plan_repaired": False,
                "dialogue_plan_quality_retry_used": False,
                "dialogue_plan_template_fallback_used": False,
                "dialogue_plan_failure_reason": None,
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
        if instance_id in instances:
            item = instances[instance_id]
            item.setdefault("dialogue_plan_llm_success", False)
            item.setdefault("mode", "staged-llm")
            item.setdefault("dialogue_strategy", "staged")
            item.setdefault("dialogue_plan_repaired", False)
            item.setdefault("dialogue_plan_quality_retry_used", False)
            item.setdefault("dialogue_plan_template_fallback_used", False)
            item.setdefault("dialogue_plan_failure_reason", None)
            item.setdefault("llm_stats", {})
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

    def record_llm_step(
        self,
        instance_id: str,
        step: str,
        *,
        calls: int = 0,
        success: bool = False,
        error_type: str | None = None,
        error_types: list[str] | None = None,
        repair_success: bool = False,
        retry_success: bool = False,
        fallback_used: bool = False,
        model_used: str | list[str] | None = None,
        failure_reason: str | None = None,
        thinking: Any | None = None,
        response_format: Any | None = None,
        content_len: int | None = None,
        reasoning_content_len: int | None = None,
    ) -> None:
        item = self.ensure_instance(instance_id)
        all_stats = item.setdefault("llm_stats", {})
        stats = all_stats.setdefault(
            step,
            {
                "calls": 0,
                "success": 0,
                "empty_response": 0,
                "timeout": 0,
                "invalid_json": 0,
                "truncated_json": 0,
                "schema_invalid": 0,
                "rate_limited": 0,
                "server_error": 0,
                "client_exception": 0,
                "unknown": 0,
                "repair_success": 0,
                "retry_success": 0,
                "fallback_used": 0,
                "model_used": [],
                "model": [],
                "thinking": None,
                "response_format": None,
                "content_len": 0,
                "reasoning_content_len": 0,
                "failure_reason": None,
            },
        )
        stats["calls"] = int(stats.get("calls", 0)) + int(calls)
        if success:
            stats["success"] = int(stats.get("success", 0)) + 1
        for error_type in ([error_type] if error_type else []) + list(error_types or []):
            key = error_type if error_type in stats else "unknown"
            stats[key] = int(stats.get(key, 0)) + 1
        if repair_success:
            stats["repair_success"] = int(stats.get("repair_success", 0)) + 1
        if retry_success:
            stats["retry_success"] = int(stats.get("retry_success", 0)) + 1
        if fallback_used:
            stats["fallback_used"] = int(stats.get("fallback_used", 0)) + 1
        model_values = model_used if isinstance(model_used, list) else ([model_used] if model_used else [])
        for model_value in model_values:
            models = stats.setdefault("model_used", [])
            if model_value not in models:
                models.append(model_value)
            model_alias = stats.setdefault("model", [])
            if model_value not in model_alias:
                model_alias.append(model_value)
        if thinking is not None:
            stats["thinking"] = thinking
        if response_format is not None:
            stats["response_format"] = response_format
        if content_len is not None:
            stats["content_len"] = int(stats.get("content_len", 0)) + int(content_len)
        if reasoning_content_len is not None:
            stats["reasoning_content_len"] = int(stats.get("reasoning_content_len", 0)) + int(reasoning_content_len)
        if failure_reason:
            stats["failure_reason"] = failure_reason
