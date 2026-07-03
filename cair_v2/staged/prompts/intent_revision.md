Build the final active CAIR intent from fact units.

Return one strict JSON object:
{
  "final_intent": {
    "objective": "...",
    "must_satisfy": ["..."],
    "must_not_satisfy": ["..."],
    "non_goals": ["..."],
    "regression_expectations": ["..."]
  },
  "revision_support": {
    "has_revision_fact": true,
    "revision_type": "ambiguity_or_correction | rejected_solution | obsolete_candidate | negative_constraint | regression_expectation | conflict_or_tension",
    "revision_unit_ids": ["U4"],
    "reason": "..."
  },
  "oracle": {
    "must_satisfy": ["..."],
    "must_not_satisfy": ["..."],
    "obsolete_intent_checks": ["..."],
    "regression_checks": ["..."],
    "forbidden_checks": [],
    "clarification_checks": []
  }
}

Rules:
- final_intent must stay faithful to the issue facts.
- A revision is valid only if supported by existing fact_units.
- If no true revision fact exists, set has_revision_fact=false and revision_unit_ids=[].
- Do not put obsolete, rejected, or non-goal facts in must_satisfy.
- No patch, tests, gold, oracle leakage, FAIL_TO_PASS, PASS_TO_PASS, diff, or test names.

