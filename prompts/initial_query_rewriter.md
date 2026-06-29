# Initial Query Rewriter

Turn a full SWE issue title into a realistic first user message.

Rules:

- Keep only the broad intent.
- Do not include full reproduction steps, stack traces, hidden tests, or implementation hints.
- Make the message short enough to require follow-up dialogue.
- Preserve whether the user wants a bug fix, feature, upgrade, or behavior change.
- It is acceptable for the user to sound uncertain.

Return only the user message.
