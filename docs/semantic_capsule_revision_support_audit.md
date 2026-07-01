# semantic_capsule.revision_support Stability Audit

Date: 2026-07-01

Scope: local existing CAIR v2 outputs only. No new LLM/API calls, no SWE-bench execution, and no CAIR batch run were performed for this audit.

## Inputs Reviewed

- `cair_v2/prompts/semantic_capsule.md`
- `cair_v2/prompts/semantic_capsule_compact.md`
- `cair_v2/construction/semantic_capsule.py`
- `cair_v2/construction/sanitizer.py`
- Existing `semantic_capsule.json` and raw LLM logs under:
  - `data/cair_instances/batch_v2_dialogue_llm3`
  - `data/cair_instances/batch_v2_dialogue_llm3_retry`
  - `data/cair_instances/batch_v2_dialogue_staged_real1`
  - `data/cair_instances/batch_v2_json_route_staged_real1`
  - `data/cair_instances/batch_v2_json_route_staged_real3`
  - `data/cair_instances/batch_v2_llm_reliability_real1`
  - `data/cair_instances/batch_v2_staged_quality_patch_real3`

Total audited capsules: 15 across 3 instances.

## Summary

`semantic_capsule.revision_support` is stable enough for strong revision evidence, but unstable for weak uncertainty evidence.

The main unstable case is `astropy__astropy-12907`. Across seven existing capsules, `revision_support.has_revision_fact` flips between `false` and `true` for the same source issue. The true runs consistently use `ambiguity_or_correction`; the false runs classify the issue as a clear bug with no revision potential.

This is not a parser leak and not caused by the JSON route. Raw LLM logs show valid HTTP 200 responses, valid JSON content, `thinking: disabled`, `response_format: json_object`, `finish_reason: stop`, and non-empty `message.content`. The instability is a semantic extraction boundary: the model sometimes treats explicit uncertainty language as revision support, and sometimes ignores it.

## Cross-Run Results

| instance | capsules | `has_revision_fact` values | stability | notes |
| --- | ---: | --- | --- | --- |
| `astropy__astropy-12907` | 7 | `false`, `true` | unstable | Flips on whether user uncertainty counts as `ambiguity_or_correction`. |
| `astropy__astropy-13033` | 4 | `true` | stable boolean, unstable type/id | Always revision-supported, but type varies among `design_suggestion_non_goal`, `obsolete_candidate`, and `rejected_solution`. |
| `django__django-10097` | 4 | `true` | stable boolean, unstable type/id | Always revision-supported, but selected supporting units vary. |

## `astropy__astropy-12907` Details

Source evidence includes explicit uncertainty:

- "Suddenly the inputs and outputs are no longer separable?"
- "This feels like a bug to me, but I might be missing something?"

Observed outputs:

| run | `has_revision_fact` | ids | types | reason |
| --- | --- | --- | --- | --- |
| `batch_v2_dialogue_llm3` | `false` | `[]` | `[]` | No alternative solutions, workarounds, ambiguity, or rejected assumptions present. |
| `batch_v2_dialogue_llm3_retry` | `false` | `[]` | `[]` | Issue clearly identifies a bug; no rejected solutions or ambiguities exist. |
| `batch_v2_dialogue_staged_real1` | `true` | `U6` | `ambiguity_or_correction` | User questions whether observed behavior is a bug or intended. |
| `batch_v2_json_route_staged_real1` | `true` | `U4` | `ambiguity_or_correction` | User expresses uncertainty about whether behavior is a bug. |
| `batch_v2_json_route_staged_real3` | `true` | `U4` | `ambiguity_or_correction` | User expresses uncertainty whether behavior is bug or misunderstanding. |
| `batch_v2_llm_reliability_real1` | `true` | `U4` | `ambiguity_or_correction` | User is uncertain whether observed behavior is a bug or intentional. |
| `batch_v2_staged_quality_patch_real3` | `false` | `[]` | `[]` | No revision potential; issue is a clear bug with no alternative interpretations. |

Latest false raw log:

- Path: `data/cair_instances/batch_v2_staged_quality_patch_real3/astropy__astropy-12907/.build/raw_llm_outputs/1782887552751635000_semantic_capsule.primary_deepseek-v4-flash_6dab2d322e50_r0.json`
- Request: `thinking: {"type": "disabled"}`, `response_format: {"type": "json_object"}`, `max_tokens: 3000`, `temperature: 0.0`
- Response: HTTP 200, `finish_reason=stop`, `prompt_tokens=2387`, `completion_tokens=1021`, `content_len=3861`, `reasoning_content_len=0`
- Raw model output set:
  - `revision_support.has_revision_fact=false`
  - `revision_support.revision_unit_ids=[]`
  - `revision_support.reason="No revision potential; issue is a clear bug with no alternative interpretations."`
- The same output included a `regression_expectation` unit, but did not include `ambiguity_or_correction`.

Comparison true raw log:

- Path: `data/cair_instances/batch_v2_json_route_staged_real3/astropy__astropy-12907/.build/raw_llm_outputs/1782884808509269000_semantic_capsule.primary_deepseek-v4-flash_6dab2d322e50_r0.json`
- Same prompt hash and JSON route.
- Response: HTTP 200, `finish_reason=stop`, `prompt_tokens=2387`, `completion_tokens=1003`, `content_len=3861`, `reasoning_content_len=0`
- Raw model output set:
  - `revision_support.has_revision_fact=true`
  - `revision_support.revision_unit_ids=["U4"]`
  - `revision_support.revision_types=["ambiguity_or_correction"]`

Conclusion: the false result is a model semantic miss, not a reliability-layer or parser failure.

## Secondary Instability

`astropy__astropy-13033` and `django__django-10097` consistently produce `has_revision_fact=true`, so they are not causing manual-review instability. However, the exact supporting unit ids and types vary.

For `astropy__astropy-13033`, valid runs variously identify:

- `design_suggestion_non_goal`: proposed message format from hints.
- `obsolete_candidate`: assumption that only `time` is required.
- `rejected_solution`: original or proposed narrow message behavior.

For `django__django-10097`, valid runs variously identify:

- `rejected_solution`: accepting invalid URL edge cases.
- `negative_constraint`: do not add excessive regex complexity.
- `workaround_to_reject`: query-string bypass.
- `conflict_or_tension`: complexity concern versus correctness.

These variations are acceptable for dialogue generation because the boolean support remains true and each chosen type is in `REVISION_FACT_TYPES`. They do affect dialogue naturalness and which revision operation is chosen, but they are not the current step-failure source.

## Prompt and Sanitizer Findings

The full semantic prompt already lists `ambiguity_or_correction` as revision-related and instructs the model to search for revision facts. The compact prompt is weaker: it says to use allowed types and set revision support false if no real revision fact exists, but it does not explicitly remind the model that uncertainty language such as "might be missing something" should be classified as `ambiguity_or_correction`.

The sanitizer currently infers revision ids only when `has_revision_fact` is truthy or absent-like:

```python
has_revision_fact = _as_bool(revision_support.get("has_revision_fact"), bool(revision_unit_ids or inferred_revision_ids))
if has_revision_fact and not revision_unit_ids:
    revision_unit_ids = inferred_revision_ids[:4]
if not has_revision_fact:
    revision_unit_ids = []
```

This means if the model explicitly returns `has_revision_fact=false`, the sanitizer discards inferred revision ids. That is conservative and avoids inventing weak revisions, but it also means deterministic source evidence like "I might be missing something" is not recovered.

## Root Cause

The unstable behavior is caused by an underspecified boundary between:

- a clear bug report with no real intent revision, and
- a clear bug report that contains explicit user uncertainty and can support a small `ambiguity_or_correction` revision turn.

For CAIR, the second category is useful and defensible when the issue text itself contains explicit uncertainty. `astropy__astropy-12907` belongs to that category.

## Recommended Minimal Fix

Add a deterministic post-sanitizer inference pass for explicit uncertainty/correction language in the problem statement. This should be narrow and evidence-based.

Suggested behavior:

1. After normal LLM semantic capsule parsing and sanitization, inspect only safe source text already available to `semantic_capsule`.
2. If `revision_support.has_revision_fact=false`, look for high-precision uncertainty/correction markers such as:
   - `might be missing something`
   - `am I missing something`
   - `is this expected`
   - `is this intended`
   - `feels like a bug`
   - `not sure if this is a bug`
3. If such a marker exists, add one `ambiguity_or_correction` fact unit with source `problem_statement`, `active_by_default=true`, and `expose_to_user=true`.
4. Set:
   - `revision_support.has_revision_fact=true`
   - `revision_support.revision_unit_ids=[new_unit_id]`
   - `revision_support.revision_types=["ambiguity_or_correction"]`
   - a reason that cites explicit user uncertainty.
5. Add the new id to `dialogue_guidance.revision_units`.

This fix should not relax the quality gate. It makes a source-grounded revision fact explicit before the existing gate evaluates the capsule.

## What Not To Fix

- Do not treat every `regression_expectation` as sufficient revision support. Regression facts are useful, but many ordinary bugs include regression preservation without supporting a real user-intent revision.
- Do not force `has_revision_fact=true` for every issue with expected/observed behavior. That would weaken CAIR quality.
- Do not solve this in dialogue planning alone. If `semantic_capsule.revision_support` is false, downstream staged dialogue correctly reaches manual review or skips template fallback.
- Do not change quality gate rules for this issue.

## Implemented Fix

Implemented after this audit:

- `sanitize_semantic_capsule` now accepts source problem text and detects high-precision uncertainty/correction markers.
- Explicit source uncertainty is canonicalized into one stable fact unit:
  - `unit_id`: `U999`
  - `type`: `ambiguity_or_correction`
  - `source`: `problem_statement`
  - `active_by_default`: `true`
  - `expose_to_user`: `true`
- If the model misses the uncertainty and returns `has_revision_fact=false`, the sanitizer adds `U999` and sets `revision_support` to:
  - `has_revision_fact=true`
  - `revision_unit_ids=["U999"]`
  - `revision_types=["ambiguity_or_correction"]`
- If the model already produced an `ambiguity_or_correction` unit with an unstable id such as `U4` or `U6`, the sanitizer canonicalizes that unit to `U999` and remaps `revision_support` / `dialogue_guidance` references.
- Cached semantic capsules are re-sanitized on load, so old false cached outputs can be stabilized without a new LLM call.
- The prompt and compact retry prompt now explicitly instruct the model to classify reporter uncertainty as `ambiguity_or_correction`.

Local validation:

- `astropy__astropy-12907` false historical output now normalizes to `has_revision_fact=true`, `revision_unit_ids=["U999"]`, with no schema validation errors.
- `astropy__astropy-12907` true historical output with model-selected `U4` now normalizes to the same `U999`.
- A plain clear-bug source text with no uncertainty markers remains `has_revision_fact=false`.
- `.venv/bin/python -m compileall cair_v2 scripts` passed.

## Remaining Next Step

Run only the existing small staged checks when ready. A full 5-step staged-LLM refactor should wait until this semantic stability fix is validated on the small samples.
