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
    max_attempts_per_step: int = 3
    max_instance_attempts: int = 2
    sleep_between_instances: float = 0.0


@dataclass
class CandidateSelectionConfig:
    golden_first: bool = True
    golden_instance_ids: list[str] = field(default_factory=lambda: ["django__django-14011"])
    include_rejected: bool = False
    skip_manual_labels: list[str] = field(default_factory=lambda: ["REJECT_OR_DOWNRANK"])


@dataclass
class EscalationConfig:
    enabled: bool = True
    flash_to_pro_on_quality_warning: bool = True
    flash_to_pro_on_parse_failure: bool = False
    max_escalations_per_instance: int = 4


@dataclass
class ModelConfig:
    models: dict[str, str]
    routing: dict[str, str]
    escalation: EscalationConfig


@dataclass
class BatchConfig:
    defaults: BatchDefaults
    candidate_selection: CandidateSelectionConfig
    model_config: ModelConfig


def load_model_config(path: Path = DEFAULT_MODEL_CONFIG) -> ModelConfig:
    data = _read_yaml(path)
    models = dict(data.get("models") or {"flash": "deepseek-v4-flash", "pro": "deepseek-v4-pro"})
    routing = dict(data.get("routing") or {})
    escalation_data = dict(data.get("escalation") or {})
    escalation = EscalationConfig(
        enabled=bool(escalation_data.get("enabled", True)),
        flash_to_pro_on_quality_warning=bool(escalation_data.get("flash_to_pro_on_quality_warning", True)),
        flash_to_pro_on_parse_failure=bool(escalation_data.get("flash_to_pro_on_parse_failure", False)),
        max_escalations_per_instance=int(escalation_data.get("max_escalations_per_instance", 4)),
    )
    return ModelConfig(models=models, routing=routing, escalation=escalation)


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
        max_attempts_per_step=int(defaults_data.get("max_attempts_per_step", 3)),
        max_instance_attempts=int(defaults_data.get("max_instance_attempts", 2)),
        sleep_between_instances=float(defaults_data.get("sleep_between_instances", 0.0)),
    )
    selection = CandidateSelectionConfig(
        golden_first=bool(selection_data.get("golden_first", True)),
        golden_instance_ids=[str(item) for item in selection_data.get("golden_instance_ids", ["django__django-14011"])],
        include_rejected=bool(selection_data.get("include_rejected", False)),
        skip_manual_labels=[str(item) for item in selection_data.get("skip_manual_labels", ["REJECT_OR_DOWNRANK"])],
    )
    return BatchConfig(defaults=defaults, candidate_selection=selection, model_config=load_model_config(model_config_path))
