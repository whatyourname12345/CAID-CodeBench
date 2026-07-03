from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_CONFIG = PROJECT_ROOT / "configs/batch_default.yaml"
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs/model_config.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@dataclass
class BatchDefaults:
    limit: int = 1
    max_api_calls: int = 30
    client_max_retries: int = 1
    client_timeout: int = 90
    sleep_between_instances: float = 0.0


@dataclass
class CandidateSelectionConfig:
    preserve_input_order: bool = True
    golden_first: bool = False
    golden_instance_ids: list[str] = field(default_factory=lambda: ["django__django-14011"])
    include_rejected: bool = False
    skip_manual_labels: list[str] = field(default_factory=lambda: ["REJECT_OR_DOWNRANK"])


@dataclass
class EscalationConfig:
    enabled: bool = True
    semantic_capsule_flash_to_pro: bool = True
    dialogue_plan_pro_to_flash_on_empty_or_parse_failure: bool = True
    flash_to_pro_on_parse_failure: bool = False
    max_escalations_per_instance: int = 2


@dataclass
class ModelConfig:
    models: dict[str, str]
    routing: dict[str, Any]
    llm_params: dict[str, Any]
    escalation: EscalationConfig


@dataclass
class BatchConfig:
    defaults: BatchDefaults
    candidate_selection: CandidateSelectionConfig
    model_config: ModelConfig
    quality_gate: dict[str, Any] = field(default_factory=dict)
    release: dict[str, Any] = field(default_factory=dict)


def load_model_config(path: Path = DEFAULT_MODEL_CONFIG) -> ModelConfig:
    data = _read_yaml(path)
    models = dict(data.get("models") or {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"})
    routing = dict(data.get("routing") or {})
    escalation_data = dict(data.get("escalation") or {})
    escalation = EscalationConfig(
        enabled=bool(escalation_data.get("enabled", True)),
        semantic_capsule_flash_to_pro=bool(escalation_data.get("semantic_capsule_flash_to_pro", True)),
        dialogue_plan_pro_to_flash_on_empty_or_parse_failure=bool(
            escalation_data.get("dialogue_plan_pro_to_flash_on_empty_or_parse_failure", True)
        ),
        flash_to_pro_on_parse_failure=bool(escalation_data.get("flash_to_pro_on_parse_failure", False)),
        max_escalations_per_instance=int(escalation_data.get("max_escalations_per_instance", 2)),
    )
    return ModelConfig(models=models, routing=routing, llm_params=dict(data.get("llm_params") or {}), escalation=escalation)


def load_batch_config(
    batch_config_path: Path = DEFAULT_BATCH_CONFIG,
    model_config_path: Path = DEFAULT_MODEL_CONFIG,
) -> BatchConfig:
    data = _read_yaml(batch_config_path)
    defaults_data = dict(data.get("defaults") or {})
    selection_data = dict(data.get("candidate_selection") or {})
    defaults = BatchDefaults(
        limit=int(defaults_data.get("limit", 1)),
        max_api_calls=int(defaults_data.get("max_api_calls", 30)),
        client_max_retries=int(defaults_data.get("client_max_retries", 1)),
        client_timeout=int(defaults_data.get("client_timeout", 90)),
        sleep_between_instances=float(defaults_data.get("sleep_between_instances", 0.0)),
    )
    selection = CandidateSelectionConfig(
        preserve_input_order=bool(selection_data.get("preserve_input_order", True)),
        golden_first=bool(selection_data.get("golden_first", False)),
        golden_instance_ids=[str(item) for item in selection_data.get("golden_instance_ids", ["django__django-14011"])],
        include_rejected=bool(selection_data.get("include_rejected", False)),
        skip_manual_labels=[str(item) for item in selection_data.get("skip_manual_labels", ["REJECT_OR_DOWNRANK"])],
    )
    return BatchConfig(
        defaults=defaults,
        candidate_selection=selection,
        model_config=load_model_config(model_config_path),
        quality_gate=dict(data.get("quality_gate") or {}),
        release=dict(data.get("release") or {}),
    )
