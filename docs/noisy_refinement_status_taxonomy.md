# Noisy Refinement Status Taxonomy

This document defines the normalized status semantics for the
`v2_noisy_refinement` construction pipeline and the bounded retry policy that
sits on top of it. It supersedes the human-queue interpretation of
`manual_review_required`.

The pipeline is unchanged:

```
fact_extraction
  -> intent_revision
  -> initial_report_plan
  -> noisy_revision_event_plan
  -> realistic_utterance_realization
  -> semantic_reviewer
  -> compiler
  -> quality_gate_v2
  -> export
```

No scenario definition, dialogue main prompt, source-span threshold, quality
gate, File/Function Hit@k, model provider, or agent/evaluator view isolation
rule was loosened to introduce these semantics.

## 1. Normalized status layer

Every terminal construction outcome now carries a `normalized_status` in
addition to the legacy `status`. The mapping is implemented in
`cair_v2/batch/status_semantics.py`.

| legacy `status` | `normalized_status` | `human_review_expected` |
|---|---|---|
| `accepted`, `accepted_with_template_dialogue` | `accepted` | `false` |
| `manual_review_required` | `auto_filtered` | `false` |
| `rejected` | `rejected` | `false` |
| `step_failed` | `step_failed` | `false` |

`human_review_expected` is **always `false`**. Nothing in this pipeline routes
to a real human queue. `manual_review_required` is retained only as a
backward-compatible legacy string; it must be read as `auto_filtered` in every
report and in the paper.

Accepted instances additionally record:

```json
{ "normalized_status": "accepted", "review_mode": "none", "human_review_expected": false }
```

auto_filtered instances record:

```json
{
  "status": "manual_review_required",
  "normalized_status": "auto_filtered",
  "review_mode": "auto_filter",
  "human_review_expected": false,
  "filter_reason": "...",
  "filter_stage": "...",
  "retry_eligible": true,
  "retry_policy": "none | stage_retry_once | full_chain_retry_once",
  "retry_reason": "..."
}
```

rejected and step_failed instances record `normalized_status` and
`human_review_expected=false`; step_failed additionally records the retry
decision fields.

These fields are written into `batch_state.json` (per instance),
`quality_report.json`, and `cair_instance.json` `metadata` (when a compact
instance exists). `manual_review_queue.csv` gains `normalized_status`,
`review_mode`, `human_review_expected`, `filter_reason`, `retry_eligible`, and
`retry_policy` columns.

## 2. CAIR-Core membership

**CAIR-Core = `normalized_status == "accepted"` only.**

- `auto_filtered` samples never enter CAIR-Core and never enter the main
  experiment. They are used only for funnel statistics and reason analysis.
- We never claim that `auto_filtered` samples were manually reviewed.

## 3. `auto_filtered` definition

`auto_filtered` means: construction finished normally, but the automatic
quality gate judged the sample unsuitable for CAIR-Core. No human triage is
implied. Canonical `filter_reason` values:

- `insufficient_source_facts_for_noisy_refinement`
- `no_withheld_units_for_later_refinement`
- `would_degenerate_into_progressive_disclosure`
- `invalid_noisy_revision_event_plan`
- `unsupported_noisy_or_wrong_claim`
- `weak_or_missing_revision_evidence`
- `fact_grounding_validation_failed`
- `source_span_unmatched_after_retry`
- `realization_leakage_manual_review`
- `utterance_template_artifact`
- `benchmarkish_or_user_facing_implementation_hint`
- `quality_gate_manual_review`
- `localization_not_ready`
- `semantic_reviewer_not_pass`

## 4. `accepted` / `rejected` / `step_failed`

**accepted** — automatic construction succeeded and the sample enters
CAIR-Core. It continues to satisfy: `old_progressive_disclosure_pattern=false`,
`unresolved_wrong_claims=0`, `scenario_fit=pass`, final active-intent
consistency, no leakage, localization checkpoint ready, File/Function Hit@k
preserved, and an agent view free of oracle/gold/private information.

**rejected** — must not enter the dataset. Hard failures: oracle / gold /
private tests / reference patch / FAIL_TO_PASS / PASS_TO_PASS / test_patch /
diff leakage; agent view cannot be safely desensitized; final intent clearly
wrong and unfixable; unresolved wrong claims enter the final intent; or the
gate judged the sample unacceptable and unsuitable for auto-filter.

**step_failed** — reserved for true technical failure only: API/transport
failure, empty response after retries, unrecoverable JSON parse, schema/code
exception, compiler/export/runtime internal bug, unexpected exception, or a
failure in the retry mechanism itself.

## 5. Bounded retry policy

Retry is typed, bounded, and tracked. It never re-samples indefinitely and is
never used to inflate the accept rate.

Two retry layers exist:

1. **Stage-level targeted retry** — already performed *inside* the staged
   `step_runner` during a single attempt (fact-grounding retry, template-artifact
   retry, realization-leakage retry). This is the `stage_retry_once` budget and
   is consumed before an attempt reports its terminal status.
2. **Instance-level bounded re-execution** — the new `full_chain_retry_once`
   layer in `run_one_instance_staged_v2`. Attempt 1 always runs; a single
   additional full-chain re-execution runs only for retry-eligible outcomes
   (`max_full_chain_attempts = 2`). Re-execution forces a clean rebuild, so the
   retry uses fresh generations rather than cached artifacts.

Default policy by normalized status:

| normalized_status | retry |
|---|---|
| `accepted` | never |
| `rejected` | never |
| `auto_filtered` | default **never**; only the retryable subclasses below |
| `step_failed` | only if transient; deterministic failures are never retried |

### Retry-eligible `auto_filtered` subclasses

These reflect generation-boundary instability, not content unsuitability:

- `utterance_template_artifact`
- `benchmarkish_or_user_facing_implementation_hint`
- `realization_leakage_manual_review` (manual-review only; private leakage is
  `rejected`, not retried)
- `invalid_noisy_revision_event_plan`
- `semantic_reviewer_not_pass` (boundary decision, no leakage)

They are recorded as:

```json
{ "retry_eligible": true, "retry_policy": "full_chain_retry_once",
  "retry_reason": "generation_boundary_instability", "max_full_chain_attempts": 2 }
```

### Non-retryable `auto_filtered` subclasses

These reflect genuine content unsuitability:

- `insufficient_source_facts_for_noisy_refinement`
- `no_withheld_units_for_later_refinement`
- `would_degenerate_into_progressive_disclosure`
- `weak_or_missing_revision_evidence`
- `fact_grounding_validation_failed`
- `source_span_unmatched_after_retry`
- `unsupported_noisy_or_wrong_claim`
- `localization_not_ready`
- `quality_gate_manual_review` (treated as non-retryable to avoid gaming the
  accept rate)

Recorded as:

```json
{ "retry_eligible": false, "retry_policy": "none",
  "retry_reason": "not_retryable_content_unsuitability" }
```

### step_failed retry

Transient signatures (`timeout`, `empty_response`, `rate_limited`,
`server_error`, `invalid_json`, `truncated_json` on the failed stage, with no
client exception) are retried once (`transient_technical_failure`).
Deterministic failures (code/schema exceptions, `staged_pipeline_exception`)
are never re-executed (`deterministic_technical_failure`).

## 6. Attempt records and `accepted_after_retry`

Each attempt appends a record (in `batch_state.json` `normalized_attempts`,
`quality_report.json` `attempts`, and `.build/retry_log.jsonl`):

```json
{
  "attempt_id": 1, "status": "...", "normalized_status": "...", "stage": "...",
  "failure_reason": "...", "filter_reason": "...", "api_calls": 0,
  "quality_gate_summary": "...", "old_progressive_disclosure_pattern": "...",
  "unresolved_wrong_claims": "...", "scenario_fit": "...",
  "agent_view_leakage_scan": "not_run"
}
```

When a retry turns an `auto_filtered`/`step_failed` outcome into `accepted`:

```json
{
  "accepted_after_retry": true,
  "status_changed_by_retry": true,
  "previous_normalized_status": "auto_filtered",
  "final_normalized_status": "accepted"
}
```

`accepted` instances also carry `accepted_via` (`"initial"` or `"retry"`), so
`accepted_initial` and `accepted_after_retry` are counted separately. The paper
reports bounded retry, not unbounded re-sampling: at most one full-chain
re-execution per instance.
