# Dialogue Skeleton Compact Retry

Return one strict JSON object only. No markdown, no prose.

Use input fields:
- `final_intent`
- `fact_units[].unit_id`
- `fact_units[].type`
- `fact_units[].text`
- `revision_support`
- `dialogue_guidance`
- `previous_errors` if present

Schema:
```json
{"turns":[{"operation":"reveal_vague_goal","introduced_units":["U1"],"intent_delta":"short delta"}]}
```

Rules:
- 4-6 turns.
- Use only ids from `fact_units`.
- T1 operation is `reveal_vague_goal`.
- If `revision_support.has_revision_fact=true`, exactly one revision turn must use an id from `revision_support.revision_unit_ids`.
- If no revision id exists, return `{"status":"manual_review_required","reason":"no supported revision fact"}`.
- Non-revision operations: `reveal_vague_goal`, `add_information`, `refine`, `add_regression_constraint`, `add_negative_constraint`, `confirm`.
- Revision operations: `correct`, `reverse`, `retract`, `override`, `obsolete`, `discard`, `introduce_conflict`, `resolve_conflict`, `reject`.
- Do not output `user_utterance`, oracle, patch, tests, markdown, or prose.
