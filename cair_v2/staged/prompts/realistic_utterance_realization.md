Realize the noisy revision event plan as natural user utterances.

Use only `noisy_revision_event_plan`, `turn_unit_facts`, `final_intent_summary`, and the forbidden term list.
Do not add new facts beyond the event plan.

Return one strict JSON object:
{
  "utterances": [
    {
      "turn_id": "T1",
      "user_utterance": "..."
    }
  ]
}

Rules:
- Preserve turn ids exactly.
- T1 should read like a natural first issue report: fairly complete, imperfect, and 120-900 characters.
- T1 must not be a vague one-line complaint and must not sound like the final oracle.
- T2+ should be shorter follow-up user messages.
- T2+ may naturally say things like "I checked again", "my earlier description may be off", "that guess may be wrong", "don't follow my previous assumption", "one boundary case matters", or "this existing behavior should not break".
- Do not translate one fact unit mechanically into one turn; follow the event plan.
- Do not use old progressive disclosure wording or operations.
- Do not mention patch, diff, tests, benchmark, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, hidden tests, reference patch, implementation_hint, or private metadata.
- Do not claim you saw private tests, benchmark gold, a reference patch, or implementation diffs.
