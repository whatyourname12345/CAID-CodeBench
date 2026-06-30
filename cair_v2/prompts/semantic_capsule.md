# CAIR v2 Semantic Capsule

You are converting one SWE-bench issue into a compact CAIR semantic capsule.

CAIR is intent-revision sharding, not ordinary issue splitting. The goal is to preserve the final active intent while enabling a later multi-turn user dialogue with additions, corrections, rejected assumptions, non-goals, or regression constraints.

Return strict JSON only. Do not use Markdown fences. Do not include prose outside JSON.
Keep the JSON compact. Use one-line strings, not multiline strings.

## Input

You receive safe issue context:

- `instance_id`
- `repo`
- `problem_statement`
- `hints_text`
- safe patch/test metadata summaries
- counts for private test metadata, not test names

## Required Output Schema

```json
{
  "suitability": {
    "is_cair_suitable": true,
    "risk_level": "low",
    "reason": "brief reason"
  },
  "fact_units": [
    {
      "unit_id": "U1",
      "type": "symptom",
      "text": "one functional fact",
      "source": "problem_statement",
      "active_by_default": true,
      "expose_to_user": true,
      "risk": null
    }
  ],
  "final_intent": {
    "objective": "final active behavior objective",
    "must_satisfy": ["behavior a correct patch must satisfy"],
    "must_not_satisfy": ["obsolete or forbidden behavior that must not be kept active"],
    "non_goals": ["optional ideas that are not required"],
    "regression_expectations": ["existing behavior to preserve"]
  },
  "revision_support": {
    "has_revision_fact": true,
    "revision_unit_ids": ["U4"],
    "revision_types": ["rejected_solution"],
    "reason": "brief evidence for why a real intent revision can be constructed"
  },
  "oracle": {
    "must_satisfy": ["patch-level behavior checks"],
    "must_not_satisfy": ["checks that reject obsolete/non-goal solutions"],
    "obsolete_intent_checks": ["obsolete or rejected assumptions"],
    "regression_checks": ["regression preservation checks"],
    "forbidden_checks": ["forbidden edits/actions if any"],
    "clarification_checks": ["when an agent should clarify ambiguity"]
  },
  "dialogue_guidance": {
    "vague_symptom_units": ["U1"],
    "context_units": ["U2"],
    "revision_units": ["U4"],
    "regression_units": ["U5"]
  }
}
```

## Allowed Fact Unit Types

Use only:

```text
symptom
observed_behavior
expected_behavior
reproduction
error_message
affected_component
active_constraint
negative_constraint
regression_expectation
obsolete_candidate
rejected_solution
non_goal
boundary_case
implementation_hint
acceptance_signal
ambiguity_or_correction
design_suggestion_non_goal
workaround_to_reject
conflict_or_tension
```

## Rules

- Fact units are minimal functional facts, not sentence shards.
- Output 5-10 fact units, not more than 10.
- Each fact unit `text` should be brief, ideally <= 25 words.
- Each list in `final_intent`, `oracle`, and `dialogue_guidance` should contain at most 6 items.
- `reason` and `risk` fields should be one short sentence.
- Extract at least two of: `symptom`, `observed_behavior`, `expected_behavior` when supported by the issue.
- Explicitly search for revision-related facts before writing `revision_support`.
- Revision-related fact units include:
  - `rejected_solution`: a solution mentioned or implied in the issue that should not be the final fix.
  - `obsolete_candidate`: an early plausible explanation/direction that should be discarded later.
  - `negative_constraint`: something the user does not want the agent to do.
  - `regression_expectation`: old behavior that must be preserved during the fix.
  - `ambiguity_or_correction`: a point where the user might plausibly start with a mistaken or incomplete interpretation and later correct it.
  - `design_suggestion_non_goal`: a design suggestion in the issue that is not part of the core fix.
  - `workaround_to_reject`: a workaround that avoids the symptom but should not be the final answer.
  - `conflict_or_tension`: tension between the fix goal and compatibility, regression, or user constraints.
- If the original issue has no real revision potential, do not invent one. Set:
  - `"revision_support.has_revision_fact": false`
  - `"revision_support.revision_unit_ids": []`
  - `"revision_support.revision_types": []`
  - `"revision_support.reason": "why no supported revision fact exists"`
- If `revision_support.has_revision_fact` is true, every id in `revision_unit_ids` must exist in `fact_units`, and each referenced unit must have a revision-related `type`.
- Put revision-related units in `dialogue_guidance.revision_units`.
- `expected_behavior` must describe user-visible or final behavior, not an implementation path.
- If `hints_text` contains method names, function names, code snippets, patch-like suggestions, exact file edits, or implementation routes, encode them as `implementation_hint`.
- Every `implementation_hint` must have:
  - `active_by_default: false`
  - `expose_to_user: false`
  - non-empty `risk`
- Do not put implementation hints into `final_intent.must_satisfy` or `oracle.must_satisfy`.
- `non_goal`, `design_suggestion_non_goal`, `obsolete_candidate`, `rejected_solution`, and `workaround_to_reject` must not enter `final_intent.must_satisfy`.
- Optional design suggestions should be `design_suggestion_non_goal` or `non_goal` with `active_by_default: false`.
- Technical hypotheses should be `boundary_case` or `affected_component` with risk if speculative.
- Do not include reference patch code, exact diff lines, private test names, `FAIL_TO_PASS`, or `PASS_TO_PASS`.
- Use private test metadata only as weak oracle feasibility evidence; do not name tests.
- If the issue is unsuitable for CAIR, set `is_cair_suitable: false` and still fill the schema with the best conservative interpretation.
