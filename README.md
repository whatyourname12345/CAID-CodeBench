# CAIR-CodeBench

CAIR-CodeBench is an interaction-first extension around SWE-bench. It keeps SWE-bench's executable `model_patch` validation, then adds evaluation of how an agent communicates, acquires user intent, resists misleading user guesses, and explores repository context before submitting the final patch.

## Data Layout

All project data artifacts live under `data/`.

- `data/raw/`: downloaded SWE-bench JSONL files from `scripts/download_swebench.py`.
- `data/processed/`: normalized and deduplicated SWE-bench tables.
- `data/candidates/`: screened CAIR v2 candidate CSVs and candidate-selection reports.
- `data/runs/`: per-run CAIR v2 construction outputs; batch grouping is diagnostic provenance, not the final dataset key.
- `data/instances/`: canonical accepted-instance store keyed by `instance_id`.
- `data/release/`: final per-instance release database for downstream agent and evaluator use.

Only lightweight seed candidates are tracked in ordinary git. Large raw,
processed, candidate-pool, run, instance, and release artifacts are local or
external-storage artifacts; see `data/README.md`.

## CAIR v2 Construction Pipeline

This branch also includes a compact CAIR v2 data-construction pipeline under
`cair_v2/`. It turns screened SWE-bench-style candidate issues into compact
`cair_instance.json` records and per-instance agent/evaluator release views.

SWE-bench is the raw corpus, not the benchmark distribution. CAIR v2 must not
default to full SWE-bench conversion. Final CAIR data should come only from a
CAIR suitability-screened subset with enough user-visible functional facts,
real intent-revision potential, and localization gold.

The recommended research path is `--mode staged-llm`, now emitted as
`pipeline_version: v2_noisy_refinement`. It uses a realistic noisy issue
refinement JSON pipeline:

1. `fact_extraction`
2. `intent_revision`
3. `initial_report_plan`
4. `noisy_revision_event_plan`
5. `realistic_utterance_realization`
6. `semantic_reviewer`
7. deterministic local compilation
8. local sanitizer and `quality_gate_v2`
9. localization checkpoint gold extraction
10. agent/evaluator-view export

The conversion target is realistic noisy issue refinement, not progressive
disclosure or ordinary sentence splitting. A compact instance keeps
`semantic_capsule.fact_units`,
`semantic_capsule.revision_support`, `dialogue.turns[].introduced_units`,
`final_intent`, `localization_checkpoint`, `oracle`, and `metadata`.

The default configuration is kept in:

- `configs/model_config.yaml`: v2 model routing only.
- `configs/batch_default.yaml`: conservative smoke defaults, candidate
  selection behavior, quality-gate policy, and release defaults.

The default candidate-selection policy preserves the input CSV order. Use a
domain-diverse input such as `data/candidates/diverse_seed_candidates.csv` for
batch construction. `django__django-14011` remains a golden construction
example, not a default first sample for benchmark distribution.

Set the DeepSeek key only in the environment:

```bash
export DEEPSEEK_API_KEY="..."
```

Run a tiny smoke batch:

```bash
python scripts/run_cair_batch_v2.py \
  --input data/candidates/diverse_seed_candidates.csv \
  --run-id batch_v2_smoke \
  --mode staged-llm \
  --model-generator deepseek-v4-flash \
  --model-critical deepseek-v4-pro \
  --model-reviewer deepseek-v4-pro \
  --limit 1 \
  --max-api-calls 30
```

Export accepted instances:

```bash
python scripts/export_cair_dataset_v2.py \
  --input-dir data/runs/batch_v2_smoke \
  --release-dir data/release \
  --instances-dir data/instances \
  --release-id cair-v2-smoke \
  --source-run-id batch_v2_smoke
```

The release exporter writes `data/release/agent/{instance_id}.json`,
`data/release/evaluator/{instance_id}.json`, `data/release/index.jsonl`,
`data/release/manifest.json`, and `data/release/quality_report.md`. It also
promotes accepted construction artifacts into `data/instances/{instance_id}/`.
The index is merged by `instance_id`, so overlapping exploratory runs do not
create batch-level ambiguity. Different content for an existing `instance_id`
is rejected unless `--force` is explicitly supplied.

Agent-view release files remove localization gold, oracle fields,
evaluator-only oracle prompts, and non-exposed semantic fact units.
Evaluator-view files keep oracle and localization gold. Neither view exports
raw LLM outputs, debug `.build` content, hidden test lists, or the full
reference patch by default.

The current construction path is the staged-LLM pipeline under `cair_v2/staged/`
and `cair_v2/batch/`.

For the step-by-step conversion procedure teammates should follow, see
`docs/cair_v2_conversion_runbook.md`.
