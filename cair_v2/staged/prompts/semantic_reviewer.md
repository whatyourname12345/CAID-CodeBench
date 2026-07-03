Review the staged CAIR construction for the noisy issue refinement scenario.

Return one strict JSON object:
{
  "decision": "accept | manual_review_required | reject",
  "scenario_fit": "pass | weak | fail",
  "initial_report_quality": "pass | weak | fail",
  "noisy_refinement_quality": "pass | weak | fail",
  "has_non_monotonic_claim_evolution": true,
  "wrong_or_speculative_claims_resolved": true,
  "old_progressive_disclosure_pattern": false,
  "final_intent_consistent": true,
  "revision_grounded": true,
  "unresolved_wrong_claims": 0,
  "leakage_risk": "low | medium | high",
  "issues": [],
  "required_fixes": []
}

Check:
- T1 reads like a realistic first issue report: fairly complete but imperfect.
- T1 is not a vague one-line complaint and not a final oracle.
- Later turns look like a real user adding details, guessing, correcting, retracting, replacing, narrowing, broadening, preserving regression behavior, or confirming the final interpretation.
- The dialogue is not old progressive disclosure that merely withholds the issue body until T2/T3.
- Any wrong, conflicting, or speculative claim is source-grounded and resolved, retracted, replaced, confirmed inactive, or downgraded.
- unresolved_wrong_claims must be 0 for accept.
- final active intent covers the original issue's full repair need.
- Withdrawn, obsolete, rejected, mistaken, or unresolved speculative claims are not in final_intent.must_satisfy.
- No agent-view leakage risk: patch, diff, tests, benchmark, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, hidden test, reference patch, implementation_hint, private test names, or benchmark-only metadata.

Use:
- `accept` only when scenario_fit is pass, leakage_risk is low, final intent is consistent, revision is grounded, and wrong/speculative claims are resolved.
- `manual_review_required` for weak but non-leaking noisy refinement or unresolved ambiguity that does not clearly corrupt final intent.
- `reject` for leakage, fabricated facts, old progressive disclosure, or final intent corruption.
