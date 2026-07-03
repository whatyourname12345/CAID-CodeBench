Extract source-grounded CAIR basic fact units from the public issue text.

Use only `problem_statement`, `hints_text`, `repo`, and `domain`.
Do not infer from patches, tests, benchmark metadata, gold, or hidden data.

Return one strict JSON object:
{
  "issue_summary": "one sentence",
  "fact_units": [
    {
      "unit_id": "U1",
      "type": "symptom | observed_behavior | expected_behavior | reproduction | error_message | environment | affected_component | active_constraint | negative_constraint | regression_expectation | obsolete_candidate | rejected_solution | non_goal | ambiguity_or_correction | conflict_or_tension | implementation_hint",
      "text": "atomic fact, not a sentence split",
      "source": "problem_statement | hints_text",
      "source_span": "short exact source phrase",
      "active_by_default": true,
      "expose_to_user": true
    }
  ]
}

Rules:
- Include basic fact units, not full issue restatement.
- Cover at least two of symptom, observed_behavior, expected_behavior.
- Preserve source uncertainty or correction phrases such as "might be missing something" as ambiguity_or_correction.
- Only use source_span text traceable to the supplied source.
- implementation_hint must set active_by_default=false and expose_to_user=false.
- No patch, tests, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, diff, or test names.
