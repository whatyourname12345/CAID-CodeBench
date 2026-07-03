# Utterance Realization Staged Compact Retry

Return JSON only:
```json
{"utterances":[{"turn_id":"T1","user_utterance":"short user message"}]}
```

Rules:
- One item per skeleton turn.
- Preserve `turn_id`.
- Each utterance must overlap with that turn's fact words.
- T1 vague, non-generic, <= 90 chars.
- T1 cannot include expected/should/fix/patch/implementation, snake_case ids, or file paths.
- Use only provided turn facts.
- Avoid all `forbidden_terms`.
- No patch, tests, implementation, oracle, markdown, or prose.
- If `previous_errors` exists, rewrite only invalid utterances.
