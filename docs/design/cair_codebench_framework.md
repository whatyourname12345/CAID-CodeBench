# CAIR-CodeBench Framework

This is a legacy framework note. The current CAIR v2 data-construction path is
the staged-LLM pipeline under `cair_v2/staged/` and `cair_v2/batch/`. In v2,
SWE-bench is raw corpus only; final CAIR data must come from
suitability-screened subsets, and agent-view exports must not expose oracle
fields, localization gold, reference patches, hidden tests, or SWE-bench
test-list metadata.

CAIR-CodeBench keeps SWE-bench's executable patch validation as the final correctness signal and adds an interaction layer around it.

## Motivation

SWE-bench starts from a complete GitHub issue. That is useful for final repair benchmarking, but it does not model a realistic user who starts with an incomplete request, supplies details through dialogue, may be uncertain, and may even give misleading hypotheses. Dialogue-SWEBench shows that coding agents need a `message_user` style interaction loop, user personas, self-revised user simulation, and dialogue quality evaluation. SWE-Explore shows that final pass/fail hides whether the agent explored the right repository context.

CAIR-CodeBench combines these ideas:

- Final patch success remains SWE-bench `FAIL_TO_PASS` plus `PASS_TO_PASS`.
- Initial user input is a weakened, realistic query generated from the issue.
- The full issue, gold patch, test patch, and test lists are hidden from the agent.
- The simulated user answers from the hidden issue and persona.
- Misleading user cues are explicit benchmark artifacts.
- Intermediate metrics score intent acquisition, dialogue quality, process quality, and repository exploration.

## Artifacts

Historically, each prototype task had:

- `tasks.jsonl`: public task index.
- `dialogues/{task_id}.json`: hidden dialogue scenario and user persona.
- `gold_intent_states/{task_id}.json`: gold structured intent.
- `evaluation_specs/{task_id}.json`: final SWE executable evaluation spec.
- `results/raw_traces/{task_id}.jsonl`: agent/user/tool events.
- `results/patches/{task_id}.patch`: final model patch.
- `results/scores/{task_id}.json`: final and intermediate scores.

These prototype artifacts are not part of the current CAIR v2 data-construction
contract. Current CAIR v2 construction and release artifacts live under
`data/candidates/`, `data/runs/`, `data/instances/`, and `data/release/`.

## Metrics

Primary metric:

- `resolved`: model patch applies, hidden test patch applies, all `FAIL_TO_PASS` and `PASS_TO_PASS` tests pass.

Intermediate metric interfaces:

- `intent`: symptom, expected behavior, constraints, non-goals, misleading-resistance.
- `dialogue`: naturalness, coherence, information seeking, user grounding.
- `process`: clarification, evidence-based debugging, misleading-resistance, testing behavior, scope control.
- `exploration`: line/file/region context quality when SWE-Explore-style ground truth is available.

The mixed `intermediate_score` is diagnostic only. Leaderboards should report `resolved_rate` separately from the interaction scores.

## Interfaces Left Open

The current framework intentionally leaves the expensive or model-specific pieces behind interfaces. There are no default heuristic scores intended for experiments:

- LLM initial-query rewriting.
- LLM user simulation.
- LLM self-revision.
- LLM-as-judge intent/dialogue/process scoring.
- Concrete CLI adapters for Codex, Claude Code, SWE-agent, mini-SWE-agent, and ReAct bash.
- Full Docker SWE-bench final evaluation.

This keeps the repo runnable while preserving clean extension points for the core research components.
