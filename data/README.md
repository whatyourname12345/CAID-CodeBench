# Data Directory

This directory is the single home for project data artifacts.

## Current CAIR v2 Layout

- `raw/`: raw SWE-bench JSONL downloads from `scripts/download_swebench.py`.
  These files are source corpus inputs, not benchmark artifacts.

- `processed/`: normalized and deduplicated SWE-bench-derived tables from
  `scripts/dedup_swebench.py`. These are intermediate corpus-preparation
  artifacts.

- `candidates/`: screened CAIR v2 candidate pools and screening reports. This
  is the active selection layer used by the current noisy-refinement
  construction chain.

- `runs/`: per-run construction outputs from `scripts/run_cair_batch_v2.py`.
  A run directory contains `batch_state.json`, run-local instance directories,
  quality reports, and `.build/` debug material. This layer is for diagnostics,
  cost accounting, retry analysis, and provenance. Batch names are not final
  dataset identifiers.

- `instances/`: canonical accepted-instance store keyed by `instance_id`. Each
  directory keeps the compact `cair_instance.json`, `quality_report.json`,
  `source_record.json` when available, and `provenance.json` copied from the
  source run. If two runs produce different content for the same `instance_id`,
  the exporter refuses to overwrite it unless `--force` is explicitly supplied.

- `release/`: final per-instance release database for downstream agents and
  evaluators. This is the consumer-facing dataset layer, not a batch archive.
  Its basic unit is one `instance_id`.

## Release Shape

`scripts/export_cair_dataset_v2.py` writes:

- `release/agent/{instance_id}.json`: agent-facing payload with oracle,
  localization gold, raw LLM output, hidden tests, debug data, and reference
  patch details removed.
- `release/evaluator/{instance_id}.json`: evaluator-facing payload that keeps
  oracle and localization gold needed for scoring.
- `release/index.jsonl`: one row per released `instance_id`, including paths,
  source run provenance, normalized status, quality status, and localization
  status.
- `release/manifest.json`: release-level metadata, source run IDs, visibility
  policy, and aggregate counts.
- `release/quality_report.md`: human-readable release summary.

For CAIR v2, batch/run grouping is diagnostic provenance only. Release consumers
should use `release/index.jsonl` plus the per-instance `agent/` or `evaluator/`
views.

## Git Tracking Policy

Track in this repository:

- `data/README.md`: data layout and visibility contract.
- `data/candidates/diverse_seed_candidates.csv`: lightweight smoke/debug
  candidate input used by documented examples.
- `data/candidates/diverse_seed_report.md`: report for the lightweight seed
  candidate input.

Do not track in ordinary git:

- `data/raw/`: downloaded source corpora.
- `data/processed/`: large derived corpus tables.
- `data/candidates/cair_candidate_pool.csv`: full candidate pool.
- `data/candidates/manual_review_priority.csv`: large ranked intermediate.
- `data/candidates/reject_or_risky_candidates.csv`: large ranked intermediate.
- `data/candidates/noisy_refinement_prefilter_candidates.csv`: current
  prefilter input; this file is larger than GitHub's ordinary 100MB file limit.
- `data/runs/`: local run/debug outputs.
- `data/instances/`: generated accepted-instance construction store.
- `data/release/`: generated agent/evaluator release database.

Large candidate and release artifacts should be regenerated with the scripts or
shared through external storage/Git LFS when the team decides to version a
specific dataset snapshot.
