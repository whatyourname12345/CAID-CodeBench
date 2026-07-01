# Dialogue Skeleton Staged Compact Retry

Return JSON only:
```json
{"turns":[{"turn_id":"T1","operation":"reveal_vague_goal","introduced_units":["U1"]}]}
```

Rules:
- Exactly 5 turns: T1-T5.
- Use only `fact_units.unit_id`.
- 1-2 introduced unit ids per turn; no empty `introduced_units`.
- T1 uses one vague symptom/observed unit.
- Include one revision operation with one `revision_support.revision_unit_ids` id when revision support exists.
- If no revision id exists: `{"status":"manual_review_required","reason":"no supported revision fact"}`.
- No `intent_delta`, no `user_utterance`, no prose.
- If `previous_errors` exists, correct only those structural errors.
