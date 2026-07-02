# CAIR v2 Conversion Runbook

This document is the teammate-facing operating guide for the current CAIR v2
conversion chain. It covers the supported `v2_noisy_refinement` path only.

## Scope

The supported conversion path is:

```text
candidate CSV
  -> data/runs/{run_id}/
  -> data/instances/{instance_id}/
  -> data/release/{agent,evaluator,index.jsonl,manifest.json}
```

Batch/run directories are diagnostic provenance. The final dataset is keyed by
`instance_id` in `data/release/index.jsonl`.

## Data Inputs

Tracked in git:

- `data/candidates/diverse_seed_candidates.csv`: lightweight smoke/debug input.
- `data/candidates/diverse_seed_report.md`: report for the smoke/debug input.

Not tracked in ordinary git:

- `data/candidates/noisy_refinement_prefilter_candidates.csv`
- `data/candidates/cair_candidate_pool.csv`
- `data/candidates/manual_review_priority.csv`
- `data/candidates/reject_or_risky_candidates.csv`
- `data/raw/`
- `data/processed/`

Large inputs must be regenerated locally or shared through external storage/Git
LFS when the team pins a dataset snapshot.

## Prerequisites

Install dependencies and set the API key in the shell environment:

```bash
export DEEPSEEK_API_KEY="..."
```

Optional health check:

```bash
.venv/bin/python scripts/check_llm_health.py
```

## Smoke Check Without API

Use this before spending API budget. It checks that the runner, config, input
CSV parsing, and output directory plumbing are intact.

```bash
.venv/bin/python scripts/run_cair_batch_v2.py \
  --input data/candidates/diverse_seed_candidates.csv \
  --run-id smoke_noapi \
  --mode staged-llm \
  --limit 1 \
  --max-api-calls 1 \
  --no-api \
  --force
```

Expected behavior:

- Writes `data/runs/smoke_noapi/`.
- Does not call the LLM API.
- Prints `pipeline_version: v2_noisy_refinement`.

## Run Conversion

For a small API-backed smoke:

```bash
.venv/bin/python scripts/run_cair_batch_v2.py \
  --input data/candidates/diverse_seed_candidates.csv \
  --run-id batch_v2_smoke \
  --mode staged-llm \
  --model-generator deepseek-v4-flash \
  --model-critical deepseek-v4-pro \
  --model-reviewer deepseek-v4-pro \
  --limit 1 \
  --max-api-calls 30 \
  --force
```

For the current prefilter input:

```bash
.venv/bin/python scripts/run_cair_batch_v2.py \
  --input data/candidates/noisy_refinement_prefilter_candidates.csv \
  --run-id batch_v2_noisy_refinement_prefilter100 \
  --mode staged-llm \
  --limit 100 \
  --max-api-calls 7000 \
  --force
```

The runner writes:

- `data/runs/{run_id}/batch_state.json`
- `data/runs/{run_id}/manual_review_queue.csv`
- `data/runs/{run_id}/{instance_id}/cair_instance.json`
- `data/runs/{run_id}/{instance_id}/quality_report.json`
- `data/runs/{run_id}/{instance_id}/source_record.json`
- private debug data under `.build/`

Do not commit `data/runs/`.

## Export Release Views

After a run finishes, export accepted instances into the per-instance release
database:

```bash
.venv/bin/python scripts/export_cair_dataset_v2.py \
  --input-dir data/runs/batch_v2_noisy_refinement_prefilter100 \
  --release-dir data/release \
  --instances-dir data/instances \
  --release-id cair-v2-prefilter100 \
  --source-run-id batch_v2_noisy_refinement_prefilter100
```

This writes:

- `data/release/agent/{instance_id}.json`
- `data/release/evaluator/{instance_id}.json`
- `data/release/index.jsonl`
- `data/release/manifest.json`
- `data/release/quality_report.md`
- `data/instances/{instance_id}/`

Do not commit `data/release/` or `data/instances/` unless the team explicitly
decides to version a release snapshot through the appropriate storage path.

## View Boundary

Agent view removes evaluator-only information:

- top-level `oracle`
- `localization_checkpoint.gold`
- `evaluation_modes.oracle_intent_prompt`
- non-exposed semantic facts
- implementation hints
- debug `.build` data
- raw LLM outputs
- hidden/private test details
- full reference patch

Evaluator view keeps scoring information:

- `oracle`
- localization gold files/functions
- `file_hit_at_k`
- `function_hit_at_k`
- quality report summary

## Post-Export Checks

Count exported rows:

```bash
wc -l data/release/index.jsonl
find data/release/agent -maxdepth 1 -type f | wc -l
find data/release/evaluator -maxdepth 1 -type f | wc -l
find data/instances -mindepth 1 -maxdepth 1 -type d | wc -l
```

Agent-view leakage scan:

```bash
rg -n -i \
  -e "oracle" \
  -e "gold" \
  -e "FAIL_TO_PASS" \
  -e "PASS_TO_PASS" \
  -e "test_patch" \
  -e "reference patch" \
  -e "diff --git" \
  -e "hidden test" \
  -e "raw_llm_outputs" \
  -e "\\btest_[A-Za-z0-9_]*" \
  data/release/agent
```

Expected result: no matches.

## Git Hygiene

Before committing:

```bash
git status --short --ignored data
```

Expected:

- `data/README.md` is visible to git.
- `data/candidates/diverse_seed_candidates.csv` is visible to git.
- `data/candidates/diverse_seed_report.md` is visible to git.
- large candidate files, `data/raw/`, `data/processed/`, `data/runs/`,
  `data/instances/`, and `data/release/` are ignored.

## Current Non-Goals

Do not run these as part of conversion:

- SWE-bench execution
- repository cloning
- Docker image building
- raw 21k conversion
- unbounded retries
- manual edits to accepted outputs
