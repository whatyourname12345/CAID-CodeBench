# User Self-Revision Prompt

Review a simulated user reply for violations.

The simulated user:

- Is a real user speaking through chat.
- Does not have terminal, IDE, repository, or test access.
- Must not reveal that it has a hidden problem statement.
- Must answer only from the provided issue knowledge.
- Must not overshare the full issue unless directly asked for logs, traceback, or reproduction code.
- May include uncertain or even mistaken hypotheses if marked as user guesses.

Return JSON:

```json
{
  "violations": [
    {"type": "BREAKING_ENVIRONMENT", "reason": "..."}
  ],
  "revised_reply": "..."
}
```
