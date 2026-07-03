# Dialogue Skeleton Staged POC

Return one strict JSON object only. No markdown, no prose.

Input contains only:
- `final_intent_objective`
- `fact_units`: exposed `unit_id`, `type`, `text`
- `revision_support`

Output exactly:
```json
{
  "turns": [
    {
      "turn_id": "T1",
      "operation": "reveal_vague_goal",
      "introduced_units": ["U1"]
    },
    {
      "turn_id": "T2",
      "operation": "add_information",
      "introduced_units": ["U2"]
    },
    {
      "turn_id": "T3",
      "operation": "correct",
      "introduced_units": ["U3"]
    },
    {
      "turn_id": "T4",
      "operation": "refine",
      "introduced_units": ["U4"]
    },
    {
      "turn_id": "T5",
      "operation": "confirm",
      "introduced_units": ["U5"]
    }
  ]
}
```

Rules:
- Exactly 5 turns: T1, T2, T3, T4, T5.
- Use only ids from `fact_units`.
- Each turn must have 1 or 2 `introduced_units`; never output an empty list.
- T1 must introduce one vague symptom unit, preferably a `symptom` or `observed_behavior`.
- T1 operation must be `reveal_vague_goal`.
- If `revision_support.has_revision_fact=true`, include exactly one revision turn using an id from `revision_support.revision_unit_ids`.
- If no revision id exists, return `{"status":"manual_review_required","reason":"no supported revision fact"}`.
- Prefer one final `confirm` turn with an expected behavior or acceptance unit when such a unit exists.
- Allowed non-revision operations: `reveal_vague_goal`, `add_information`, `refine`, `add_regression_constraint`, `add_negative_constraint`, `confirm`.
- Allowed revision operations: `correct`, `reverse`, `retract`, `override`, `obsolete`, `discard`, `introduce_conflict`, `resolve_conflict`, `reject`.
- Do not output `intent_delta`, `user_utterance`, oracle, patch, tests, markdown, or prose.
- If `previous_errors` is present, return a corrected skeleton that fixes those errors and preserves valid turns where possible.
