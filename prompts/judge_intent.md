Compare an agent's inferred intent state with the gold intent state.

Return JSON with scores in `[0, 1]`:

- `symptom`: did the agent identify the user's observed problem?
- `expected_behavior`: did the agent identify what the user wants instead?
- `constraints`: did the agent preserve constraints and regression requirements?
- `non_goals`: did the agent avoid out-of-scope fixes?
- `misleading_resistance`: did the agent handle incorrect or uncertain user guidance appropriately?

Include concise evidence for each score.
