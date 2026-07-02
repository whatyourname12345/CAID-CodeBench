from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from cair_v2.batch.batch_config import BatchConfig
from cair_v2.batch.batch_runner import write_manual_review_queue
from cair_v2.batch.batch_state import BatchState
from cair_v2.batch.candidate_loader import load_candidate_rows, select_candidates
from cair_v2.batch.instance_runner import BatchV2Options, run_one_instance_v2


def _step_params(options: BatchV2Options, step: str) -> dict[str, Any]:
    params = dict((options.llm_params or {}).get(step) or {})
    allowed = {"temperature", "max_tokens", "thinking", "response_format"}
    return {key: value for key, value in params.items() if key in allowed}


def _json_route(options: BatchV2Options) -> dict[str, dict[str, Any]]:
    if options.mode == "staged-llm":
        steps = [
            "fact_extraction",
            "intent_revision",
            "initial_report_plan",
            "noisy_revision_event_plan",
            "realistic_utterance_realization",
            "semantic_reviewer",
            "json_repair",
        ]
    else:
        steps = ["semantic_capsule", "dialogue_plan", "dialogue_skeleton", "utterance_realization", "json_repair"]
    return {step: _step_params(options, step) for step in steps}


def select_rows_for_v2(
    input_file: Path,
    *,
    limit: int | None,
    include_rejected: bool,
    config: BatchConfig,
) -> list[dict[str, Any]]:
    selection = config.candidate_selection
    include_rejected = include_rejected or selection.include_rejected
    if not selection.preserve_input_order:
        return select_candidates(
            input_file,
            limit=limit,
            include_rejected=include_rejected,
            golden_ids=selection.golden_instance_ids,
            golden_first=selection.golden_first,
        )
    rows = load_candidate_rows(input_file)
    skip_labels = set(selection.skip_manual_labels)
    if not include_rejected and skip_labels:
        rows = [row for row in rows if str(row.get("manual_override_label") or "") not in skip_labels]
    return rows[:limit] if limit is not None else rows


def run_batch_v2(options: BatchV2Options, config: BatchConfig) -> BatchState:
    output_dir = options.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    state = BatchState.load_or_create(
        output_dir / "batch_state.json",
        batch_id=output_dir.name,
        input_file=options.input_file,
        output_dir=output_dir,
        model_generator=options.model_generator,
        model_critical=options.model_critical,
        model_reviewer=options.model_reviewer,
        reset=options.force and not options.resume,
    )
    state.data["pipeline_version"] = "v2_noisy_refinement" if options.mode == "staged-llm" else "v2_minimal_robust"
    state.data["mode"] = options.mode
    state.data["dialogue_strategy"] = options.dialogue_strategy
    state.data["json_route"] = _json_route(options)
    rows = select_rows_for_v2(
        options.input_file,
        limit=options.limit,
        include_rejected=options.include_rejected,
        config=config,
    )
    for record in rows:
        if state.api_calls >= options.max_api_calls and not (options.dry_run or options.no_api):
            break
        run_one_instance_v2(record=record, output_dir=output_dir, state=state, options=options)
        write_manual_review_queue(output_dir, state)
        if config.defaults.sleep_between_instances:
            time.sleep(config.defaults.sleep_between_instances)
    write_manual_review_queue(output_dir, state)
    state.save()
    return state
