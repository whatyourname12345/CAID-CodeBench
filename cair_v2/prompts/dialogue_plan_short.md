# Dialogue Skeleton Retry

Return one JSON object only.

Shape: `{"turns":[4 to 6 objects]}`.

Turn object keys:
- `operation`
- `introduced_units`
- `intent_delta`

Rules:
- Use only ids from `fact_units`.
- T1: `reveal_vague_goal`.
- If `revision_support.has_revision_fact=true`, exactly one revision turn must use one id from `revision_support.revision_unit_ids`.
- If no revision id exists, return `{"status":"manual_review_required","reason":"no supported revision fact"}`.
- Allowed non-revision operations: `reveal_vague_goal`, `add_information`, `refine`, `add_regression_constraint`, `add_negative_constraint`, `confirm`.
- Allowed revision operations: `correct`, `reverse`, `retract`, `override`, `obsolete`, `discard`, `introduce_conflict`, `resolve_conflict`, `reject`.
- No `user_utterance`, patch, tests, markdown, or prose.
