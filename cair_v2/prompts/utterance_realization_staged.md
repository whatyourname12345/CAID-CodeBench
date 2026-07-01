# Utterance Realization Staged POC

Return one strict JSON object only. No markdown, no prose.

Input contains only:
- `dialogue_skeleton`
- `turn_unit_facts`
- `forbidden_terms`

Output exactly:
```json
{
  "utterances": [
    {
      "turn_id": "T1",
      "user_utterance": "short natural user message"
    }
  ]
}
```

Rules:
- Return one utterance per skeleton turn.
- Do not change, add, or remove `turn_id`.
- Do not output `operation` or `introduced_units`.
- Each utterance must be short, natural, and based only on that turn's fact text.
- Every utterance must include at least one concrete word from that turn's fact text so it matches `introduced_units`.
- T1 must be vague, <= 90 characters, non-generic, and not a complete issue statement.
- T1 must not include "expected", "should", "fix", "patch", "implementation", root cause claims, snake_case identifiers, or file/module paths.
- Do not include any forbidden term.
- Do not mention patch, tests, benchmark metadata, implementation, oracle, files, or hidden details.
- Do not include file extensions such as `.py`.
- If `previous_errors` is present, rewrite only the invalid utterances and keep valid turn_ids.
