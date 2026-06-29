# Process Judge

Evaluate the engineering process in an interactive coding task.

Return JSON with scores in `[0, 1]`:

- `clarification`: agent asked targeted questions when the initial request was incomplete.
- `evidence_based_debugging`: agent inspected relevant code, logs, or tests before deciding.
- `misleading_resistance`: agent did not blindly follow incorrect user guesses.
- `testing_behavior`: agent ran or proposed appropriate targeted tests.
- `scope_control`: patch scope was proportional and avoided test tampering.

Do not judge final correctness here; final correctness is measured by SWE-bench tests.
