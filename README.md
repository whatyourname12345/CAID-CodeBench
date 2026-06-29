# CAIR-CodeBench

CAIR-CodeBench is an interaction-first extension around SWE-bench. It keeps SWE-bench's executable `model_patch` validation, then adds evaluation of how an agent communicates, acquires user intent, resists misleading user guesses, and explores repository context before submitting the final patch.

## Build Tasks

```bash
.venv/bin/python scripts/build_cair_tasks.py \
  --swe-jsonl datasets/swe-bench/dev.jsonl \
  --limit 5 \
  --tasks-out datasets/tasks.jsonl
```

This creates:

- `datasets/dialogues/{task_id}.json`
- `datasets/gold_intent_states/{task_id}.json`
- `datasets/evaluation_specs/{task_id}.json`
- `datasets/tasks.jsonl`

## Score Axes

Primary:

- `resolved`: final SWE-style patch success using hidden tests.

Diagnostic:

- `intent`: whether the agent understood the real user need.
- `dialogue`: naturalness, coherence, and information seeking.
- `process`: evidence-based debugging, testing, scope control, and resistance to misleading cues.
- `exploration`: ranked region quality when SWE-Explore-style line-level ground truth is available.

The expensive pieces, such as LLM-backed user simulation and LLM-as-judge scoring, are exposed as interfaces in `harness/` and intentionally left replaceable.
