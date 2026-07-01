from __future__ import annotations

from pathlib import Path
from typing import Any

from cair_v2.llm.clients import DeepSeekClient
from cair_v2.staged.pipeline import run_staged_pipeline
from cair_v2.staged.schemas import StagedConstructionResult
from cair_v2.staged.step_runner import StepConfig, StagedStepRunner


def staged_step_configs(
    *,
    model_generator: str,
    model_critical: str,
    llm_params: dict[str, Any],
) -> dict[str, StepConfig]:
    return {
        "fact_extraction": StepConfig(model=model_generator, params=dict(llm_params.get("fact_extraction") or {})),
        "intent_revision": StepConfig(model=model_critical, params=dict(llm_params.get("intent_revision") or {})),
        "dialogue_skeleton": StepConfig(model=model_generator, params=dict(llm_params.get("dialogue_skeleton") or {})),
        "utterance_realization": StepConfig(model=model_generator, params=dict(llm_params.get("utterance_realization") or {})),
        "semantic_reviewer": StepConfig(model=model_critical, params=dict(llm_params.get("semantic_reviewer") or {})),
        "json_repair": StepConfig(model=model_generator, params=dict(llm_params.get("json_repair") or {})),
    }


def model_config_summary(configs: dict[str, StepConfig]) -> dict[str, Any]:
    return {
        name: {
            "model": config.model,
            "temperature": config.params.get("temperature"),
            "max_tokens": config.params.get("max_tokens"),
            "thinking": config.params.get("thinking"),
            "response_format": config.params.get("response_format"),
        }
        for name, config in configs.items()
        if name != "json_repair"
    }


def run_staged_instance_pipeline(
    *,
    instance_dir: Path,
    step_configs: dict[str, StepConfig],
    client_factory,
    no_api: bool,
    dry_run: bool,
) -> StagedConstructionResult:
    runner = StagedStepRunner(
        instance_dir=instance_dir,
        step_configs=step_configs,
        client_factory=client_factory,
        no_api=no_api,
        dry_run=dry_run,
    )
    return run_staged_pipeline(instance_dir=instance_dir, runner=runner)


def deepseek_client_factory(*, no_api: bool, dry_run: bool, client_max_retries: int, client_timeout: int):
    def factory(model: str) -> DeepSeekClient | None:
        if no_api or dry_run:
            return None
        return DeepSeekClient(model=model, max_retries=client_max_retries, timeout=client_timeout)

    return factory

