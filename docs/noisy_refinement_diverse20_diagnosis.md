# Noisy Refinement Diverse20 Diagnosis

Run: `batch_v2_noisy_refinement_diverse20` using `v2_noisy_refinement` in `staged-llm` mode. This document is diagnostic only; no code, prompt, or quality gate was changed during the run.

## Overall statistics

- accepted: 7
- manual_review_required: 4
- rejected: 0
- step_failed: 9
- template_fallback: 0
- API calls: 87

## Step-failed check

`step_failed` did not include the intended noisy-refinement content adaptation reasons. The 9 failures are all recorded as `schema_invalid`, but diverse20 exposed a status-semantics boundary: realization leakage/forbidden wording is an LLM output validation failure, not a transport/runtime failure. It is currently still counted as `step_failed`.

| instance_id | stage | error_type | diagnostic |
|---|---|---|---|
| matplotlib__matplotlib-20826 | fact_extraction | schema_invalid | fact_units[10].source_span is not an exact substring; fact_units[11].source_span is not an exact substring; fact_units[15].source_span is not an exact substring; fact_units[17].source_span is not an exact substring |
| psf__requests-1724 | fact_extraction | schema_invalid | fact_units[5].source_span is not an exact substring; fact_units[7].source_span is not an exact substring |
| pydata__xarray-2905 | fact_extraction | schema_invalid | fact_units[7].source_span is not an exact substring |
| pytest-dev__pytest-10051 | fact_extraction | schema_invalid | fact_units[7].source_span is not an exact substring |
| pytest-dev__pytest-10081 | realistic_utterance_realization | schema_invalid | T1 must be an imperfect issue report without benchmark/private wording; utterances[0] leaks forbidden implementation/benchmark text |
| scikit-learn__scikit-learn-10844 | realistic_utterance_realization | schema_invalid | utterances[3] leaks forbidden implementation/benchmark text |
| sphinx-doc__sphinx-10449 | fact_extraction | schema_invalid | fact_units[4].source_span is not an exact substring |
| sphinx-doc__sphinx-10614 | fact_extraction | schema_invalid | fact_units[6].source_span is not an exact substring |
| sympy__sympy-12096 | fact_extraction | schema_invalid | fact_units[4].source_span is not an exact substring |

Assessment: fact_extraction source_span failures are upstream grounding/schema failures and fit the current technical-failure bucket. The two `realistic_utterance_realization` leakage failures are not source-fact insufficiency, but should be considered for future routing to manual/rejected rather than step_failed if the strict semantics are enforced literally. No evidence of API/transport failure, empty response, or JSON parse unrecoverable failure appeared in this run.

## Manual review classification

| category | count | notes |
|---|---:|---|
| insufficient_source_facts_for_noisy_refinement | 3 | astropy__astropy-13033, django__django-10999, matplotlib__matplotlib-20488 |
| no_withheld_units_for_later_refinement | 0 | No explicit occurrences. |
| would_degenerate_into_progressive_disclosure | 0 | No explicit occurrences; matplotlib is effectively protected by insufficient source facts routing. |
| unsupported noisy/wrong claim | 0 | No explicit occurrences. |
| weak or missing revision evidence | 1 | astropy__astropy-13033 has explicit no source-grounded revision fact warning. |
| localization issue | 0 | No manual review caused by localization. |
| semantic reviewer issue | 0 | No manual review caused by semantic reviewer decision. |
| quality gate issue | 1 | sympy__sympy-12419 failed quality gate due user-facing implementation hint and overlong confirm turn. |

## Accepted sample checks

- `old_progressive_disclosure_pattern`: false for all 7 accepted samples.
- `unresolved_wrong_claims`: 0 for all 7 accepted samples.
- `scenario_fit`: pass for all 7 accepted samples.
- T1 quality: all accepted samples have a complete but imperfect initial issue report with component/observed/expected/reproduction or uncertainty coverage.
- Noisy refinement: all accepted samples include at least one follow-up that adds detail, narrows scope, introduces speculation, retracts, corrects, resolves conflict, or confirms final active intent.
- Wrong/speculative claim resolution: all accepted speculative or mistaken claims are either corrected, retracted, deactivated, or converted into final confirmation.
- Final active intent: quality gate passed `final_active_intent_consistency` for all accepted samples.

Accepted localization summary:

| instance_id | gold files | gold functions | localization status |
|---|---:|---:|---|
| astropy__astropy-12907 | 1 | 1 | ready |
| django__django-10097 | 1 | 1 | ready |
| psf__requests-1142 | 1 | 1 | ready |
| pydata__xarray-3151 | 1 | 1 | ready |
| pylint-dev__pylint-4970 | 1 | 1 | ready |
| scikit-learn__scikit-learn-10297 | 1 | 2 | ready |
| mwaskom__seaborn-3187 | 2 | 2 | ready |

## Generation stability

The overlapping first-five instances show status fluctuation compared with `real5_statusfix`:

| instance_id | observed change | interpretation |
|---|---|---|
| astropy__astropy-12907 | real5_statusfix manual_review_required -> diverse20 accepted | Planning/reviewer boundary is not fully deterministic across runs. |
| django__django-10999 | real5_statusfix accepted -> diverse20 manual_review_required | Planning/reviewer boundary is not fully deterministic across runs. |
| matplotlib__matplotlib-20488 | real5_statusfix accepted -> diverse20 manual_review_required | Planning/reviewer boundary is not fully deterministic across runs. |

This looks less like random runtime failure and more like LLM/planning instability near the source-facts sufficiency boundary. Planning temperatures are already 0.0, so later work should consider deterministic tie-breakers, stricter structured pre-checks before LLM planning, or caching/reuse for exact reruns. Lowering `realistic_utterance_realization` temperature may reduce realization leakage, but will not by itself fix fact_extraction source_span instability.

## Metrics and view isolation

- evaluator-view retains `localization_checkpoint.gold.files` and `localization_checkpoint.gold.functions` for all 7 exported accepted samples.
- evaluator-view retains `localization_checkpoint.metrics.file_hit_at_k` and `function_hit_at_k` for all 7 accepted samples.
- agent-view removes oracle and localization gold while preserving localization metric schema.
- agent-view leakage scan: pass: rg found no matches for oracle, gold, FAIL_TO_PASS, PASS_TO_PASS, test_patch, reference patch, diff --git, hidden test, raw_llm_outputs, or test_* in agent JSONL.
- Exported agent/evaluator JSONL each contain 7 rows, matching accepted-only release export behavior.

## Diagnostic recommendations

- Do not lower quality gate standards based on this run; leakage and oracle/gold isolation behaved correctly.
- Investigate whether realization leakage validator failures should be routed to manual/rejected instead of step_failed under the new status semantics.
- Investigate fact_extraction source_span exact-substring fragility; 7/20 failures are blocked before noisy refinement begins.
- Add deterministic pre-checks or tie-breakers for source-facts sufficiency to reduce accepted/manual oscillation on identical instances.
- Do not proceed to full-scale generation until status fluctuation and step_failed semantics are addressed. A focused diverse20 rerun after those fixes is preferable to diverse100/21k.
