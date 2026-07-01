# Dialogue Skeleton

Return strict JSON only.

Output shape: `{"turns":[{"operation": "...", "introduced_units": ["U1"], "intent_delta": "..."}]}`.

Hard rules:
- 4-6 turns.
- Use only exposed `fact_units`.
- T1 operation must be `reveal_vague_goal`.
- If `revision_support.has_revision_fact=true`, include exactly one revision turn using one id from `revision_support.revision_unit_ids`; do not return manual review.
- If `revision_support.has_revision_fact=false` or `revision_unit_ids=[]`, return `{"status":"manual_review_required","reason":"no supported revision fact"}`.
- Each turn has one operation and non-empty `introduced_units`.
- Prefer this order: vague goal, context, observed/expected refinement, revision, regression if any, confirmation.
- Do not output `user_utterance`, oracle, patch, tests, markdown, or prose.

Allowed revision operations:
`correct`, `reverse`, `retract`, `override`, `obsolete`, `discard`, `introduce_conflict`, `resolve_conflict`, `reject`.

If `previous_errors` is present, rewrite the skeleton to fix exactly those errors.
