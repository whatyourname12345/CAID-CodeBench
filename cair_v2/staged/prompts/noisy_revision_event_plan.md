Plan a realistic noisy multi-turn issue refinement dialogue.

Use only source-grounded `fact_units`, `final_intent`, `revision_support`, and `initial_report_plan`.
The dialogue should model a real user who starts with a fairly complete but imperfect issue report, then adds, revises, contradicts, retracts, corrects, narrows, broadens, or confirms claims.

Return one strict JSON object:
{
  "turns": [
    {
      "turn_id": "T1",
      "operation": "initial_imperfect_report",
      "introduced_units": ["U1", "U2", "U3"],
      "claim_status": "partially_active_with_uncertainty",
      "active_after_turn": ["..."],
      "inactive_after_turn": [],
      "must_be_resolved_later": ["..."],
      "notes": "..."
    },
    {
      "turn_id": "T2",
      "operation": "add_detail | speculative_hypothesis | mistaken_clarification | incorrect_reproduction_detail | correct_previous_claim | retract_previous_claim | replace_previous_claim | resolve_conflict | narrow_scope | broaden_scope | add_missing_detail | add_reproduction_detail | add_regression_constraint | confirm_final_active_intent",
      "introduced_units": ["U4"],
      "revises_turns": ["T1"],
      "revises_units": ["U3"],
      "deactivates_claims": ["..."],
      "activates_claims": ["..."],
      "claim_status": "active | speculative | mistaken | correction | retraction | replacement | confirmation",
      "must_be_resolved_later": false,
      "notes": "..."
    }
  ]
}

If the source facts cannot support this without fabrication, return:
{
  "status": "manual_review_required",
  "reason": "..."
}

Rules:
- Use 3-6 turns total.
- T1 must be `initial_imperfect_report` and must reuse initial_report_plan.initial_report.introduced_units.
- Never use `reveal_vague_goal`, `refine`, `correct`, or `add_information`.
- T2+ may add correct information or source-grounded user uncertainty, guesses, corrections, retractions, replacements, scope changes, regression constraints, or final confirmation.
- At least one T2+ turn must show non-monotonic issue refinement.
- Any speculative, mistaken, conflicting, or misleading claim that affects repair direction must later be corrected, retracted, replaced, resolved, confirmed inactive, or downgraded.
- Bind corrections, retractions, replacements, and final confirmation to revision_support.revision_unit_ids when possible.
- Do not invent wrong claims for drama; wrong or speculative claims must be plausible and source-grounded.
- final active intent must remain consistent with final_intent.
- Inactive, withdrawn, rejected, or unresolved speculative claims must not become final active requirements.
- Do not include user_utterance.
- Do not mention patch, diff, tests, benchmark, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, hidden tests, reference patch, or implementation_hint.
