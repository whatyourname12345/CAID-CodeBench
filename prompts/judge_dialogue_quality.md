# Dialogue Quality Judge

Score an agent-user coding dialogue.

Return JSON with scores in `[0, 1]`:

- `naturalness`: agent is concise, understandable, and user-facing.
- `coherence`: each message follows locally and the overall dialogue moves toward resolution.
- `information_seeking`: agent asks for missing details when needed.
- `user_grounding`: agent uses user-provided details accurately without inventing them.

Also include `diagnostics` with short evidence.
