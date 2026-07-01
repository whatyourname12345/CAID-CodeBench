Review the staged CAIR construction.

Return one strict JSON object:
{
  "decision": "accept | manual_review_required | reject",
  "semantic_equivalence": true,
  "revision_is_real": true,
  "dialogue_naturalness": "pass | weak | fail",
  "leakage_risk": "low | medium | high",
  "issues": [],
  "required_fixes": []
}

Check:
- final_intent is faithful to the issue facts.
- dialogue is intent-revision sharding, not ordinary sentence splitting.
- revision is real and supported by fact_units.
- user utterances sound natural and avoid template artifacts.
- no over-inference.
- no patch, tests, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, diff, or test-name leakage.

Use manual_review_required or reject for severe issues.

