# Semantic Capsule Compact Retry

Return one strict JSON object only. No markdown, no prose.

Use input fields:
- `instance_id`
- `repo`
- `problem_statement`
- `hints_text`
- `safe_patch_metadata`
- `output_contract.fact_unit_types`
- `output_contract.revision_fact_types`
- `output_contract.do_not_emit`

Schema:
```json
{
  "suitability": {"is_cair_suitable": true, "risk_level": "low", "reason": "brief reason"},
  "fact_units": [
    {
      "unit_id": "U1",
      "type": "symptom",
      "text": "brief functional fact",
      "source": "problem_statement",
      "active_by_default": true,
      "expose_to_user": true,
      "risk": null
    }
  ],
  "final_intent": {
    "objective": "final behavior objective",
    "must_satisfy": ["required behavior"],
    "must_not_satisfy": ["obsolete or forbidden behavior"],
    "non_goals": [],
    "regression_expectations": []
  },
  "revision_support": {
    "has_revision_fact": true,
    "revision_unit_ids": ["U4"],
    "revision_types": ["rejected_solution"],
    "reason": "brief evidence"
  },
  "oracle": {
    "must_satisfy": [],
    "must_not_satisfy": [],
    "obsolete_intent_checks": [],
    "regression_checks": [],
    "forbidden_checks": [],
    "clarification_checks": []
  },
  "dialogue_guidance": {
    "vague_symptom_units": [],
    "context_units": [],
    "revision_units": [],
    "regression_units": []
  }
}
```

Rules:
- 5-10 fact units.
- Use only allowed fact unit types.
- Explicit reporter uncertainty like "might be missing something", "is this expected/intended", or "feels like a bug" is `ambiguity_or_correction` revision support.
- If no real revision fact exists, set `revision_support.has_revision_fact=false` and empty revision lists.
- `implementation_hint` must be inactive and not exposed.
- Do not emit patch code, diff lines, private test names, `FAIL_TO_PASS`, or `PASS_TO_PASS`.
