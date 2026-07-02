Plan the first user turn for a realistic noisy multi-turn issue refinement scenario.

Use only source-grounded `fact_units`, `final_intent`, and `revision_support`.
Do not invent facts, patches, tests, gold data, hidden tests, reference diffs, or implementation hints.

Return one strict JSON object:
{
  "initial_report": {
    "introduced_units": ["U1", "U2", "U3"],
    "imperfection_types": [
      "uncertainty",
      "wrong_assumption",
      "missing_boundary",
      "overgeneralization",
      "incomplete_reproduction",
      "ambiguous_scope",
      "wrong_root_cause_guess",
      "missing_regression_constraint"
    ],
    "noisy_or_imperfect_units": ["U3"],
    "withheld_units_for_later": ["U4"],
    "rationale": "..."
  }
}

If the source facts cannot support a realistic noisy refinement dialogue, return:
{
  "status": "manual_review_required",
  "reason": "..."
}

Rules:
- T1 introduced_units must contain 3-7 user-exposable fact unit ids.
- T1 must cover at least two of: symptom, observed_behavior, expected_behavior, reproduction, ambiguity_or_correction, affected_component.
- T1 should be a reasonably complete first issue report, not a vague one-line complaint.
- T1 may be uncertain, overgeneralized, missing a boundary, missing a regression constraint, or include a source-grounded wrong assumption.
- T1 must not equal the final oracle or expose all final checks.
- Withhold at least one source-grounded fact unit for a later add, correction, withdrawal, confirmation, or constraint turn.
- Do not include user_utterance.
- Do not mention patch, diff, tests, benchmark, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, hidden tests, reference patch, or implementation_hint.
