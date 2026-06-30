# CAIR v2 Dialogue Plan

Create a compact multi-turn intent-revision dialogue plan from a CAIR semantic capsule.

Return strict JSON only. Do not use Markdown fences. Do not output YAML. Do not include prose outside JSON.

## Required Output Schema

```json
{
  "status": "ok",
  "turns": [
    {
      "operation": "reveal_vague_goal",
      "user_utterance": "short realistic user message",
      "introduced_units": ["U1"],
      "intent_delta": "brief description of what this turn changes"
    }
  ]
}
```

If `semantic_capsule.revision_support.has_revision_fact` is false, do not invent a revision turn. Return exactly this shape instead:

```json
{
  "status": "manual_review_required",
  "reason": "no supported revision fact"
}
```

## Allowed Operations

Use only:

```text
reveal_vague_goal
add_information
refine
correct
reverse
retract
override
obsolete
discard
introduce_conflict
resolve_conflict
reject
add_regression_constraint
add_negative_constraint
confirm
```

## Rules

- Generate 4-6 turns.
- Turn 1 must be <= 90 characters.
- Turn 1 must be short, vague, and realistic.
- Turn 1 must not contain the full module location, exact error, root cause, expected behavior, and fix direction all at once.
- Include at least one true intent-revision operation: `correct`, `reverse`, `retract`, `override`, `obsolete`, `discard`, `introduce_conflict`, `resolve_conflict`, or `reject`.
- The revision turn must reference at least one id from `semantic_capsule.revision_support.revision_unit_ids`.
- If no revision unit is available, return `status: manual_review_required` instead of forcing a fake correction/rejection.
- Each turn has exactly one core operation.
- User utterances should sound like short GitHub/Slack follow-up messages to a coding agent.
- Do not reveal implementation hints, exact methods/functions, reference patch details, or hidden/private test names.
- Do not mention `FAIL_TO_PASS`, `PASS_TO_PASS`, benchmark metadata, atomic units, hidden intent, or reference patch.
- Do not make the user claim they ran new tests, opened the repo, used a terminal, or verified a patch unless that fact is directly in the original issue.
- Do not output `active_state_after_turn`, `active_goals`, `active_constraints`, `obsolete_goals`, `expected_agent_behavior`, or `risk_notes`.
- `introduced_units` may reference only fact unit ids from the semantic capsule and should not reference units where `expose_to_user` is false.
- Every turn must include a non-empty `introduced_units` list.
- `introduced_units` must match the content of the user utterance; do not attach unrelated fact ids.
- The final turn should converge to the final active intent without becoming a benchmark-style full issue.
