# Noisy Refinement Diverse20 Autofilter Report

This run validates the normalized-status semantic layer and the bounded retry
policy for `v2_noisy_refinement`. `manual_review_required` is now a legacy
compatibility string that maps to `normalized_status = auto_filtered`
(`human_review_expected = false`); no sample is claimed to be human-reviewed.
No scenario definition, dialogue main prompt, source-span threshold, quality
gate, File/Function Hit@k, model provider, or agent/evaluator view isolation
rule was loosened.

Batch dir: `data/cair_instances/batch_v2_noisy_refinement_diverse20_autofilter`
(gitignored). Semantics defined in `docs/noisy_refinement_status_taxonomy.md`.

## 1. Overall statistics

| metric | value |
|---|---:|
| accepted (legacy `accepted`) | 12 |
| manual_review_required (legacy) | 8 |
| rejected | 0 |
| step_failed | 0 |
| template_fallback | 0 |
| API calls | 127 |
| source_span_repaired_count total | 46 |
| source_span_unmatched_count total | 2 |

## 2. normalized_status statistics

| normalized_status | count | human_review_expected |
|---|---:|---|
| accepted | 12 | false |
| auto_filtered | 8 | false |
| rejected | 0 | false |
| step_failed | 0 | false |

- accepted_initial: 11
- accepted_after_retry: 1

## 3. filter_reason distribution (auto_filtered)

| filter_reason | count | retry_eligible |
|---|---:|---|
| insufficient_source_facts_for_noisy_refinement | 2 | false |
| source_span_unmatched_after_retry | 2 | false |
| quality_gate_manual_review | 2 | false |
| realization_leakage_manual_review | 2 | true |

Per-instance:

| instance_id | filter_stage | filter_reason | retry_eligible | retry_policy |
|---|---|---|---|---|
| astropy__astropy-13033 | intent_revision | insufficient_source_facts_for_noisy_refinement | false | none |
| matplotlib__matplotlib-20826 | realistic_utterance_realization | realization_leakage_manual_review | true | full_chain_retry_once |
| psf__requests-1142 | fact_extraction | source_span_unmatched_after_retry | false | none |
| pytest-dev__pytest-10051 | realistic_utterance_realization | realization_leakage_manual_review | true | full_chain_retry_once |
| pytest-dev__pytest-10081 | fact_extraction | source_span_unmatched_after_retry | false | none |
| scikit-learn__scikit-learn-10844 | quality_gate | quality_gate_manual_review | false | none |
| sphinx-doc__sphinx-10449 | quality_gate | quality_gate_manual_review | false | none |
| sympy__sympy-12096 | intent_revision | insufficient_source_facts_for_noisy_refinement | false | none |

## 4. Retry policy outcomes

- retry_eligible auto_filtered: 2 (`realization_leakage_manual_review`), both
  `full_chain_retry_once`.
- retry_policy distribution over auto_filtered: `none` = 6,
  `full_chain_retry_once` = 2.
- Instances that ran a second full-chain attempt (`normalized_attempt_count > 1`):
  `matplotlib__matplotlib-20826`, `pytest-dev__pytest-10051` (retry-eligible
  auto_filter), and `pylint-dev__pylint-4970` (retry-eligible on attempt 1,
  accepted on attempt 2).
- accepted_after_retry: 1 (`pylint-dev__pylint-4970`), recorded with
  `status_changed_by_retry=true`, `previous_normalized_status=auto_filtered`,
  `final_normalized_status=accepted`, `accepted_via=retry`. Attempt 1
  auto_filtered at `noisy_revision_event_plan` (5 calls); attempt 2 accepted
  (6 calls) — a genuine re-execution, not a cache replay.
- No content-unsuitability auto_filter (insufficient source facts, source-span
  unmatched, generic quality-gate) was retried; retry never inflates the
  accepted rate.

## 5. step_failed status

`step_failed` = 0. Under this run all parseable validator/gate outcomes route
to `auto_filtered`, and no true technical failure (API/transport, empty
response after retries, unrecoverable JSON, schema/code exception, internal
bug) occurred. The `step_failed` bucket is therefore restricted to genuine
technical failures by construction; its transient subset is
`full_chain_retry_once`-eligible and its deterministic subset is not.

## 6. Accepted sample quality gates

All 12 accepted samples satisfy `old_progressive_disclosure_pattern=false`,
`unresolved_wrong_claims=0`, and `scenario_fit=pass`; all carry
`normalized_status=accepted` and `human_review_expected=false`.

| instance_id | old_progressive_disclosure_pattern | unresolved_wrong_claims | scenario_fit | accepted_via |
|---|---|---:|---|---|
| astropy__astropy-12907 | False | 0 | pass | initial |
| django__django-10097 | False | 0 | pass | initial |
| django__django-10999 | False | 0 | pass | initial |
| matplotlib__matplotlib-20488 | False | 0 | pass | initial |
| mwaskom__seaborn-3187 | False | 0 | pass | initial |
| psf__requests-1724 | False | 0 | pass | initial |
| pydata__xarray-2905 | False | 0 | pass | initial |
| pydata__xarray-3151 | False | 0 | pass | initial |
| pylint-dev__pylint-4970 | False | 0 | pass | retry |
| scikit-learn__scikit-learn-10297 | False | 0 | pass | initial |
| sphinx-doc__sphinx-10614 | False | 0 | pass | initial |
| sympy__sympy-12419 | False | 0 | pass | initial |

## 7. File/Function Hit@k

- Evaluator view retains `localization_checkpoint.gold.files`/`.functions` and
  `localization_checkpoint.metrics.file_hit_at_k`/`function_hit_at_k` for all 12
  exported accepted samples (verified: 1 gold file + 1 gold function on the
  sample checked; metrics keys present on every line).
- Agent view removes `localization_checkpoint.gold` and top-level `oracle` while
  retaining the `file_hit_at_k` / `function_hit_at_k` metric schema.

## 8. Agent-view leakage scan

Pass. `data/releases/cair_batch_v2_noisy_refinement_diverse20_autofilter_agent.jsonl`
(12 lines) has zero matches for `oracle`, `gold`, `FAIL_TO_PASS`,
`PASS_TO_PASS`, `test_patch`, `reference patch`, `diff --git`, `hidden test`,
`raw_llm_outputs`, or `test_*`, and no forbidden evaluator-only key path
(`oracle|gold|fail_to_pass|pass_to_pass|test_patch|reference_patch|hidden`).

## 9. Comparison with statusfinal

| run | accepted | auto_filtered (manual_review_required) | rejected | step_failed | API calls |
|---|---:|---:|---:|---:|---:|
| statusfinal | 8 | 12 | 0 | 0 | 108 |
| autofilter | 12 | 8 | 0 | 0 | 127 |

The autofilter run adds the semantic layer and one bounded full-chain retry per
retry-eligible instance; the extra API calls come from the three second
attempts. Per-instance accepted/auto_filtered membership shifts within the
noisy-refinement boundary are attributable to LLM generation stochasticity, not
to gate loosening — the accepted-sample quality flags and view isolation are
unchanged.

## 10. Recommendation on limit=100

Recommended. The run has zero `step_failed` and zero `rejected`; all
non-accepted outcomes are `auto_filtered` with a canonical `filter_reason` and a
bounded, typed retry decision, and all accepted samples keep their quality flags
and view isolation. Suggested next step: run **prefiltered100 first**
(`keep`-tier candidates from `data/candidates/noisy_refinement_prefilter_candidates.csv`)
to estimate the achievable accepted rate on selected candidates, then
**raw100** on the unfiltered head for an unbiased denominator. Both stay
diagnostic-only; do not commit generated `data/cair_instances` or
`data/releases` artifacts. Do not run limit=100 until explicitly requested.
