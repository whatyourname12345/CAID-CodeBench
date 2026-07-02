# Noisy Refinement Diverse20 Statusfinal Report

This run performs final status-semantics normalization for `v2_noisy_refinement`: parseable, attributable validator failures route to `manual_review_required` or `rejected`; `step_failed` is reserved for true technical/runtime failures. No noisy-refinement scenario goal, dialogue main prompt, source-span threshold, quality gate, File/Function Hit@k, model provider, or view isolation rule was loosened.

## 1. Modified files

- `cair_v2/staged/step_runner.py`: adds final routing for fact grounding validator failures and utterance template artifacts after parseable targeted retry; technical retry/API/JSON failures still remain step_failed.
- `cair_v2/staged/pipeline.py`: propagates `fact_extraction` manual/rejected status instead of hard-coding step_failed.
- `cair_v2/batch/instance_runner.py`: records `fact_grounding_validation_failed`, `utterance_template_artifact`, source-span stats, and realization metadata in batch_state/quality_report.
- Previously modified stability files still in this working tree: `cair_v2/staged/source_spans.py`, `cair_v2/staged/validators.py`.
- `docs/noisy_refinement_diverse20_statusfinal_report.md`: this report.

## 2. fact_extraction grounding failure routing

- Source grounding remains mandatory; source-free or hallucinated spans are not accepted.
- Existing source-span repair logic and thresholds are unchanged.
- If critical `source_span` alignment fails, `fact_extraction` gets one targeted retry instructing the model to use exact original substrings from `problem_statement` or `hints_text`.
- If the retry is parseable but critical spans are still unmatched, the instance becomes `manual_review_required` with `failure_reason=fact_grounding_validation_failed`.
- API/transport/empty response/unrecoverable JSON/code exceptions still remain technical failures.

## 3. utterance template artifact routing

- `realistic_utterance_realization` template/meta wording now shares the leakage retry path: one targeted retry removes template artifacts and forbidden benchmark/private wording while preserving issue content.
- If retry remains parseable but still contains only template artifact or benchmark-ish/user-facing implementation wording, status becomes `manual_review_required`.
- If evaluator-private leakage appears, status routes to `rejected`. No rejected cases occurred in this run.

## 4. diverse20_statusfinal statistics

- accepted: 8
- manual_review_required: 12
- rejected: 0
- step_failed: 0
- template_fallback: 0
- API calls: 108
- source_span_repaired_count total: 51
- source_span_unmatched_count total: 1

## 5. Step-failed status

No `step_failed` samples remain in this run. Under this run, `step_failed` is therefore restricted to true technical failures by absence; all parseable validator failures were routed to `manual_review_required`.

## 6. Manual routing counts

- `fact_grounding_validation_failed`: 1
- `utterance_template_artifact`: 1

| instance_id | stage | failure_reason | manual_review_reason | repaired | unmatched |
|---|---|---|---|---:|---:|
| psf__requests-1142 | fact_extraction | fact_grounding_validation_failed | critical source_span unmatched after repair/retry: fact_units[11].source_span is not aligned to source | 2 | 1 |
| sympy__sympy-12419 | realistic_utterance_realization | utterance_template_artifact | utterances[3] contains template artifact wording | 6 | 0 |

## 7. Accepted sample checks

- All accepted samples have `old_progressive_disclosure_pattern=false`.
- All accepted samples have `unresolved_wrong_claims=0`.
- All accepted samples have `scenario_fit=pass`.

| instance_id | old_progressive_disclosure_pattern | unresolved_wrong_claims | scenario_fit |
|---|---|---:|---|
| django__django-10097 | False | 0 | pass |
| django__django-10999 | False | 0 | pass |
| psf__requests-1724 | False | 0 | pass |
| pylint-dev__pylint-4970 | False | 0 | pass |
| scikit-learn__scikit-learn-10297 | False | 0 | pass |
| scikit-learn__scikit-learn-10844 | False | 0 | pass |
| sphinx-doc__sphinx-10449 | False | 0 | pass |
| sphinx-doc__sphinx-10614 | False | 0 | pass |

## 8. File/Function Hit@k

- Evaluator-view retains `localization_checkpoint.gold.files` and `localization_checkpoint.gold.functions` for all exported accepted samples.
- Evaluator-view retains `localization_checkpoint.metrics.file_hit_at_k` and `function_hit_at_k`.
- Agent-view removes oracle/gold while retaining localization metric schema.

## 9. Agent-view leakage scan

Pass. `rg` found no matches in `data/releases/cair_batch_v2_noisy_refinement_diverse20_statusfinal_agent.jsonl` for oracle, gold, FAIL_TO_PASS, PASS_TO_PASS, test_patch, reference patch, diff --git, hidden test, raw_llm_outputs, or `test_*`.

## 10. Comparison

- stabilityfix: accepted=11, manual_review_required=7, rejected=0, step_failed=2, API calls=108
- eventfix: accepted=7, manual_review_required=9, rejected=0, step_failed=4, API calls=97
- statusfinal: accepted=8, manual_review_required=12, rejected=0, step_failed=0, API calls=108

| instance_id | eventfix | statusfinal |
|---|---|---|
| astropy__astropy-12907 | manual_review_required / noisy_revision_event_plan / insufficient_source_facts_for_noisy_refinement | manual_review_required / realistic_utterance_realization / realization_leakage_manual_review |
| matplotlib__matplotlib-20826 | step_failed / fact_extraction / fact_extraction | manual_review_required / realistic_utterance_realization / realization_leakage_manual_review |
| mwaskom__seaborn-3187 | accepted / done / None | manual_review_required / realistic_utterance_realization / realization_leakage_manual_review |
| psf__requests-1142 | step_failed / fact_extraction / fact_extraction | manual_review_required / fact_extraction / fact_grounding_validation_failed |
| psf__requests-1724 | manual_review_required / quality_gate / manual_review | accepted / done / None |
| pytest-dev__pytest-10051 | step_failed / fact_extraction / fact_extraction | manual_review_required / realistic_utterance_realization / realization_leakage_manual_review |
| scikit-learn__scikit-learn-10844 | manual_review_required / realistic_utterance_realization / realization_leakage_manual_review | accepted / done / None |
| sympy__sympy-12419 | step_failed / realistic_utterance_realization / realistic_utterance_realization | manual_review_required / realistic_utterance_realization / utterance_template_artifact |

## 11. Recommendation on limit=50

Yes, a `limit=50` diagnostic is now reasonable. The remaining non-accepted cases are manual-reviewable content/validation outcomes rather than step_failed runtime failures, and accepted-sample quality flags plus view isolation remain intact. Keep the run diagnostic-only and continue not committing generated `data/cair_instances` or `data/releases` artifacts.
