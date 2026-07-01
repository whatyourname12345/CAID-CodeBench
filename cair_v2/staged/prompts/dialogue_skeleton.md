Create an intent-revision dialogue skeleton.

Return one strict JSON object:
{
  "turns": [
    {
      "turn_id": "T1",
      "operation": "reveal_vague_goal",
      "introduced_units": ["U1"]
    }
  ]
}

Rules:
- 4 to 6 turns.
- T1 must be reveal_vague_goal.
- Include at least one real revision operation.
- The revision operation must introduce revision_support.revision_unit_ids.
- Each introduced_units entry must be valid and user-exposable.
- Prefer 1 or 2 introduced_units per turn.
- Do not write user_utterance.
- Do not write intent_delta.
- No patch, tests, gold, oracle, FAIL_TO_PASS, PASS_TO_PASS, diff, or test names.

