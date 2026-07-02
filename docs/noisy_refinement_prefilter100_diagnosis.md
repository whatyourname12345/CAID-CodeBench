# Noisy Refinement Prefilter100 Diagnosis

Run date: 2026-07-02

Input:
`data/candidates/noisy_refinement_prefilter_candidates.csv`

Output dir:
`data/runs/batch_v2_noisy_refinement_prefilter100`

Accepted-only views:

- Agent: `data/release/agent/{instance_id}.json`
- Evaluator: `data/release/evaluator/{instance_id}.json`
- Index: `data/release/index.jsonl`

The run used the requested staged LLM command with `--limit 100`, `--max-api-calls 7000`, and `--force`. No code changes, raw100 run, 21k full run, SWE-bench execution, or repo clone was performed.

## 1. Overall Statistics

| Metric | Count |
|---|---:|
| Candidates processed | 100 |
| accepted | 57 |
| auto_filtered | 41 |
| rejected | 1 |
| step_failed | 1 |
| template_fallback | 0 |
| API calls | 646 |

Legacy status summary from stdout: `accepted=57`, `manual_review_required=41`, `rejected=1`, `step_failed=1`.

## 2. normalized_status Statistics

| normalized_status | Count |
|---|---:|
| accepted | 57 |
| auto_filtered | 41 |
| rejected | 1 |
| step_failed | 1 |

## 3. Retry Statistics

| Metric | Count |
|---|---:|
| accepted_initial | 53 |
| accepted_after_retry | 4 |
| retry_eligible=True | 11 |
| retry_eligible=False | 31 |
| retry_eligible missing/null | 58 |
| normalized_attempt_count=2 | 19 |

Retry policy distribution:

| retry_policy | Count |
|---|---:|
| null | 58 |
| none | 31 |
| full_chain_retry_once | 11 |

`full_chain_retry` usage: 15 instances if counted as policy-labeled full-chain retries plus accepted-via-retry successes (`11` final auto_filtered with `retry_policy=full_chain_retry_once`, plus `4` accepted_after_retry). There were 19 instances with `normalized_attempt_count=2`; 4 of those were retry/recheck records with `retry_policy=none`.

## 4. auto_filtered Reason Distribution

Distribution uses final `filter_reason` for `normalized_status=auto_filtered`.

| Reason | Count |
|---|---:|
| insufficient_source_facts_for_noisy_refinement | 20 |
| no_withheld_units_for_later_refinement | 0 |
| invalid_noisy_revision_event_plan | 1 |
| fact_grounding_validation_failed | 0 |
| source_span_unmatched_after_retry | 6 |
| realization_leakage_manual_review | 10 |
| quality_gate_manual_review | 4 |
| localization_not_ready | 0 |
| semantic_reviewer_not_pass | 0 |
| Other reasons | 0 |

Note: the 6 `source_span_unmatched_after_retry` entries have `failure_reason=fact_grounding_validation_failed`.

## 5. Accepted Sample Quality

Accepted quality was checked over the 57 accepted `quality_report.json` files.

| Check | Result |
|---|---|
| old_progressive_disclosure_pattern | all `false` (57/57) |
| unresolved_wrong_claims | all `0` (57/57) |
| scenario_fit | all `pass` (57/57) |
| final_active_intent_consistency | all passed (57/57) |
| template_fallback_used | none (0/57) |
| quality_report `passed` | all true (57/57) |

No accepted sample violated these final quality checks.

## 6. Localization / Hit@k

| Check | Result |
|---|---|
| evaluator-view retains localization gold files | yes, 57/57 rows |
| evaluator-view retains localization gold functions | yes, 57/57 rows |
| evaluator-view retains `file_hit_at_k` | yes, 57/57 rows |
| evaluator-view retains `function_hit_at_k` | yes, 57/57 rows |
| agent-view removes `gold` key paths | yes, 0 key paths |
| agent-view removes `oracle` key paths | yes, 0 key paths |

The agent view still contains the localization prompt/schema and Hit@k metric names, but no localization gold files/functions and no oracle fields.

## 7. Leakage Scan

Agent-view keyword scan:

| Forbidden keyword/pattern | Matches |
|---|---:|
| oracle | 0 |
| gold | 0 |
| FAIL_TO_PASS | 0 |
| PASS_TO_PASS | 0 |
| test_patch | 0 |
| reference patch | 0 |
| diff --git | 0 |
| hidden test | 0 |
| raw_llm_outputs | 0 |
| test_* | 0 |

Forbidden evaluator-only key-path scan on agent-view: 0 paths.

The evaluator view intentionally contains evaluator-only paths such as `oracle`, `localization_checkpoint.gold.*`, and localization gold count summaries. These were absent from the agent view.

## 8. prefilter100 Accepted Rate

| Rate | Value |
|---|---:|
| accepted / 100 | 57% |
| accepted_initial / 100 | 53% |
| accepted_after_retry / 100 | 4% |
| auto_filtered / 100 | 41% |
| step_failed / 100 | 1% |
| rejected / 100 | 1% |

## 9. CAIR-Core Scale Estimate

Assumption: keep pool = 6288 and observed accepted rate = 57%.

Estimated accepted count:

`6288 * 0.57 = 3584.16`, approximately 3584 accepted instances.

| Target | Enough? | Rationale |
|---|---|---|
| CAIR-Core-500 | yes | expected accepted pool is far above 500 |
| CAIR-Core-1000 | yes | expected accepted pool is far above 1000 |

## 10. Cost Estimate

Observed API calls:

- API calls / candidate: `646 / 100 = 6.46`
- API calls / accepted: `646 / 57 = 11.33`

Projected API calls to construct accepted targets at the observed rate:

| Target | Estimated candidates needed | Estimated API calls |
|---|---:|---:|
| CAIR-Core-500 | 878 | 5667 |
| CAIR-Core-1000 | 1755 | 11333 |

The full keep pool estimate at this rate is about `6288 * 6.46 = 40620` API calls.

## 11. Recommendation

| Next step | Recommendation | Reason |
|---|---|---|
| raw100 | Do not run now | Not needed for the immediate Core sizing decision; useful only if measuring prefilter lift against raw candidates. |
| prefiltered500 | Run before committing to CAIR-Core-1000; optional for CAIR-Core-500 | It would reduce sampling variance and reveal repo/subset drift. |
| CAIR-Core-500 generation | Proceed | Observed 57% accepted rate and projected cost are sufficient for 500 accepted instances. |

Bottom line: the prefilter100 result is strong enough to support CAIR-Core-500 and appears sufficient for CAIR-Core-1000 by pool-size estimate. A prefiltered500 diagnostic is still the prudent next validation before spending a larger generation budget.
