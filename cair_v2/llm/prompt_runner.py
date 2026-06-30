from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cair_v2.llm.clients import DeepSeekClient
from cair_v2.llm.json_utils import ParsedModelOutput, parse_model_output, to_yaml_text


SYSTEM_PROMPT = """You construct CAIR-CodeBench instances.
CAIR is intent-revision sharding, not ordinary multi-turn issue splitting.
Return only strict JSON or YAML matching the requested schema.
Never reveal API keys, reference patch code, hidden tests, or implementation diffs."""


@dataclass
class PromptStepResult:
    step_name: str
    status: str
    parsed: Any | None = None
    raw_path: Path | None = None
    parsed_path: Path | None = None
    error: str | None = None


def ensure_cache_dir(instance_dir: Path) -> Path:
    cache_dir = instance_dir / ".llm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def render_user_prompt(prompt_text: str, context: dict[str, Any]) -> str:
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    return f"{prompt_text.rstrip()}\n\n## Actual Input\n\n```json\n{context_json}\n```\n"


def write_parsed_cache(path: Path, parsed: ParsedModelOutput) -> None:
    if parsed.data is None:
        path.write_text("", encoding="utf-8")
        return
    path.write_text(to_yaml_text(parsed.data), encoding="utf-8")


def run_prompt_step(
    *,
    instance_dir: Path,
    step_name: str,
    cache_prefix: str,
    prompt_path: Path,
    context: dict[str, Any],
    client: DeepSeekClient | None,
    model: str = "deepseek-v4-flash",
    temperature: float | None = None,
    max_tokens: int | None = None,
    force: bool = False,
    no_api: bool = False,
) -> PromptStepResult:
    cache_dir = ensure_cache_dir(instance_dir)
    raw_path = cache_dir / f"{cache_prefix}.raw.txt"
    parsed_path = cache_dir / f"{cache_prefix}.parsed.yaml"
    error_path = cache_dir / f"{cache_prefix}.error.txt"

    if parsed_path.exists() and not force:
        return PromptStepResult(step_name=step_name, status="cached", raw_path=raw_path, parsed_path=parsed_path)

    prompt_text = prompt_path.read_text(encoding="utf-8")
    user_prompt = render_user_prompt(prompt_text, context)

    if no_api:
        print(f"\n--- DRY RUN STEP: {step_name} ---")
        print(f"Prompt file: {prompt_path}")
        print(f"Instance: {instance_dir}")
        print(f"Input keys: {', '.join(context.keys())}")
        preview = user_prompt[:4000]
        print(preview)
        if len(user_prompt) > len(preview):
            print(f"\n[Prompt truncated for dry-run preview: {len(user_prompt)} chars total]")
        return PromptStepResult(step_name=step_name, status="dry_run")

    if client is None:
        raise ValueError("client is required when no_api is False")

    response = client.complete(SYSTEM_PROMPT, user_prompt, model=model, temperature=temperature, max_tokens=max_tokens)
    raw_path.write_text(response.content, encoding="utf-8")
    parsed = parse_model_output(response.content)
    if not parsed.ok:
        error_path.write_text(parsed.error or "Unknown parse error", encoding="utf-8")
        return PromptStepResult(
            step_name=step_name,
            status="parse_error",
            raw_path=raw_path,
            parsed_path=parsed_path,
            error=parsed.error,
        )

    write_parsed_cache(parsed_path, parsed)
    if error_path.exists():
        error_path.unlink()
    return PromptStepResult(
        step_name=step_name,
        status="ok",
        parsed=parsed.data,
        raw_path=raw_path,
        parsed_path=parsed_path,
    )
