# CAIR-CodeBench

CAIR-CodeBench is an interaction-first extension around SWE-bench. It keeps SWE-bench's executable `model_patch` validation, then adds evaluation of how an agent communicates, acquires user intent, resists misleading user guesses, and explores repository context before submitting the final patch.

## Legacy Prototype Task Builder

This section is the older prototype path. It is kept for historical
experiments only and is not the CAIR v2 data-construction contract.

```bash
.venv/bin/python scripts/build_cair_tasks.py \
  --swe-jsonl datasets/swe-bench/dev.jsonl \
  --limit 5 \
  --tasks-out datasets/tasks.jsonl
```

This creates:

- `datasets/dialogues/{task_id}.json`
- `datasets/gold_intent_states/{task_id}.json`
- `datasets/evaluation_specs/{task_id}.json`
- `datasets/tasks.jsonl`

## Score Axes

Primary:

- `resolved`: final SWE-style patch success using hidden tests.

Diagnostic:

- `intent`: whether the agent understood the real user need.
- `dialogue`: naturalness, coherence, and information seeking.
- `process`: evidence-based debugging, testing, scope control, and resistance to misleading cues.
- `exploration`: ranked region quality when SWE-Explore-style line-level ground truth is available.

The expensive pieces, such as LLM-backed user simulation and LLM-as-judge scoring, are exposed as interfaces in `harness/` and intentionally left replaceable.

## CAIR v2 Construction Pipeline

This branch also includes a compact CAIR v2 data-construction pipeline under
`cair_v2/`. It is separate from the existing `harness/` execution code and is
intended to turn screened SWE-bench-style candidate issues into compact
`cair_instance.json` records.

SWE-bench is the raw corpus, not the benchmark distribution. CAIR v2 must not
default to full SWE-bench conversion. Final CAIR data should come only from a
CAIR suitability-screened subset with enough user-visible functional facts,
real intent-revision potential, and localization gold.

The recommended research path is `--mode staged-llm`, a five-stage JSON
pipeline:

1. `fact_extraction`
2. `intent_revision`
3. `dialogue_skeleton`
4. `utterance_realization`
5. `semantic_reviewer`
6. deterministic local compilation
7. local sanitizer and `quality_gate_v2`
8. localization checkpoint gold extraction
9. agent/evaluator-view export

The older `--mode minimal-robust` path is retained for compatibility. It still
uses `semantic_capsule` and `dialogue_plan` internally, but is no longer the
preferred construction path.

The conversion target is intent-revision sharding, not ordinary sentence
splitting. A compact instance keeps `semantic_capsule.fact_units`,
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
  --output-dir data/cair_instances/batch_v2_smoke \
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
  --input-dir data/cair_instances/batch_v2_smoke \
  --output data/releases/cair_batch_v2_agent.jsonl \
  --agent-view

python scripts/export_cair_dataset_v2.py \
  --input-dir data/cair_instances/batch_v2_smoke \
  --output data/releases/cair_batch_v2_evaluator.jsonl \
  --evaluator-view
```

Agent-view export removes localization gold, oracle fields, evaluator-only
oracle prompts, and non-exposed semantic fact units. Evaluator-view keeps oracle
and localization gold. Neither view exports raw LLM outputs, debug `.build`
content, hidden test lists, or the full reference patch by default.

Release artifacts should be the compact JSONL view plus construction
`quality_report.json` summaries. Instance directories are construction outputs;
their `.build/` contents are debug/private material and should not be treated as
agent-facing data.

See `docs/cair_pipeline_v2_minimal_robust.md` for the construction contract,
legacy compatibility path, quality gate, template fallback, and localization
checkpoint details.
