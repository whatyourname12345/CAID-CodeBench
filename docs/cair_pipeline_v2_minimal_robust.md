# CAIR Pipeline v2: Minimal-Robust Construction

## Why v2 Exists

Pipeline v1 proved the compact `cair_instance.json` interface, but its construction path still depended on many LLM steps:

```text
atomic units -> final intent -> hidden state -> trajectory -> utterances
-> evaluation modes -> oracle -> QA -> leakage -> reviewer
```

This was too fragile for batch construction. In the v1 smoke run, one candidate failed because trajectory generation returned empty model responses, and another failed because SWE-bench metadata/test-name leakage propagated through final intent, evaluation prompts, and oracle text.

v2 keeps the CAIR idea but reduces model interaction to two core calls:

```text
candidate filter/diversity   local
semantic_capsule             LLM call 1
dialogue_plan                LLM call 2, with template fallback
deterministic compiler       local
sanitizer + leakage gate     local
localization checkpoint      local
quality gate                 local
optional reviewer            optional
export agent/evaluator view  local
```

The goal is not to lower CAIR standards. The goal is to move deterministic, schema-sensitive, and leakage-sensitive work out of the LLM chain.

## Semantic Capsule

`semantic_capsule` is the main semantic LLM step. It is saved at:

```text
.build/semantic_capsule.json
```

It contains:

- `suitability`: whether the issue is suitable for CAIR.
- `fact_units`: minimal functional facts, not sentence shards.
- `final_intent`: final active objective and required behaviors.
- `oracle`: evaluator checks derived from the issue.
- `dialogue_guidance`: which facts should support vague start, context, revision, or regression turns.

`implementation_hint` facts are allowed only when they are:

```json
{
  "active_by_default": false,
  "expose_to_user": false
}
```

They must not enter user dialogue, `must_satisfy`, evaluation prompts, or oracle active checks.

## Dialogue Plan

`dialogue_plan` is the second LLM step. It is saved at:

```text
.build/dialogue_plan.json
```

It contains only:

```json
{
  "turns": [
    {
      "operation": "reveal_vague_goal",
      "user_utterance": "...",
      "introduced_units": ["U1"],
      "intent_delta": "..."
    }
  ]
}
```

The model is not allowed to output full state, expected agent behavior, risk notes, YAML, markdown fences, patch details, or private test metadata.

## Template Fallback

If both Pro and Flash fail to produce a valid dialogue plan, v2 uses a local template built from `semantic_capsule.fact_units`:

```text
T1 vague symptom
T2 affected component/context
T3 observed/error/expected behavior
T4 rejected_solution / obsolete_candidate / negative_constraint / correction
T5 regression expectation, if available
T6 final confirmation
```

Template output must still pass the same quality gate. Passing template instances are marked:

```text
accepted_with_template_dialogue
```

This status is exportable but remains distinguishable from ordinary `accepted` instances.

## Deterministic Compiler

`cair_v2/construction/cair_compiler_v2.py` compiles:

```text
source_record + semantic_capsule + dialogue_plan/template + localization_gold
```

into:

```text
cair_instance.json
quality_report.json
.build/intermediate_debug.json
```

The compiler locally generates:

- sparse `dialogue.turns[].state_delta`
- `evaluation_modes`
- `oracle`
- `localization_checkpoint`
- metadata

No separate LLM calls are used for evaluation modes, QA checklist, quality diagnosis, retry decisions, or oracle formatting.

## Formal Output

A v2 instance directory should be treated as:

```text
source_record.json
cair_instance.json
quality_report.json
README.md
.build/
```

Only `cair_instance.json` and `quality_report.json` are intended as stable construction/scoring interfaces. `.build/` is debug-only.

The formal compact instance uses sparse fields. Empty state-delta arrays,
empty forbidden-file/conflict lists, raw risk notes, raw model outputs, and
candidate-private patch records stay out of `cair_instance.json`. Construction
debug data belongs under `.build/` and is not part of the release contract.

## Configuration

v2 uses two small config files:

```text
configs/model_config.yaml
configs/batch_default.yaml
```

`model_config.yaml` describes only the v2 minimal-robust chain:

```text
semantic_capsule -> dialogue_plan -> local compiler -> local quality gate
```

It intentionally does not contain v1 steps such as `atomic_issue_units`,
`hidden_intent_state`, `operation_trajectory`, standalone `qa_checklist`, or
standalone `intent_oracle` generation.

`batch_default.yaml` keeps smoke-test defaults conservative:

- `limit: 1`
- `max_api_calls: 30`
- input-order preservation for curated diverse seed CSVs
- rejected/downranked candidate filtering
- no reviewer by default
- agent-view release defaults

Command-line flags override these defaults. If `preserve_input_order` is true,
the runner trusts the input CSV order. Turn it off only when running from an
unsorted candidate pool and you want local quality-score ordering.

## Sanitizer First

`cair_v2/construction/sanitizer.py` runs before compilation and again over the compact instance. It scrubs or blocks:

- `FAIL_TO_PASS`
- `PASS_TO_PASS`
- hidden/private test wording
- `test_*`-like private test names
- reference patch mentions
- diff markers
- patch-like code snippets

If sanitization cannot remove leakage without damaging semantics, the quality gate sends the instance to manual review or rejection.

## Localization Checkpoint

v2 preserves the localization checkpoint introduced in v1:

```json
{
  "localization_checkpoint": {
    "prompt": "...",
    "expected_output_schema": {
      "ranked_files": [],
      "ranked_functions": []
    },
    "gold": {
      "files": [],
      "functions": []
    },
    "metrics": {
      "file_hit_at_k": [1, 3, 5, 10],
      "function_hit_at_k": [1, 3, 5, 10]
    }
  }
}
```

The prompt is for the agent before code editing. It never contains gold files/functions. Gold is evaluator-only and extracted locally from reference patch metadata.

Gold extraction first uses the private construction record in `.build/` when
available. If the full patch is absent, it falls back to v2 patch metadata under
`.build/patch_metadata.json`, then to legacy root `patch_metadata.json` only for
compatibility with older instance directories.

## Agent View vs Evaluator View

Use:

```bash
python scripts/export_cair_dataset_v2.py --input-dir data/cair_instances/batch_v2_smoke --output data/releases/cair_batch_v2_agent.jsonl --agent-view
python scripts/export_cair_dataset_v2.py --input-dir data/cair_instances/batch_v2_smoke --output data/releases/cair_batch_v2_evaluator.jsonl --evaluator-view
```

Agent view excludes:

- localization gold
- oracle
- source patch
- raw LLM output
- `.build`

Evaluator view includes:

- oracle
- localization gold
- quality summary

It still excludes full reference patch unless `--include-patch` is explicitly passed.

## Quality Gate v2

`cair_v2/batch/quality_gate_v2.py` checks:

- semantic suitability
- fact-unit coverage of symptom/observed/expected behavior
- final intent objective and `must_satisfy`
- 3-8 dialogue turns
- T1 <= 90 chars and vague
- at least one revision operation
- valid `introduced_units`
- non-exposed facts absent from user dialogue
- implementation hints absent from user-facing surfaces
- non-goals/rejected solutions absent from `must_satisfy`
- complete evaluation modes
- localization checkpoint readiness
- complete oracle
- agent-view has no gold, hidden tests, benchmark metadata, or patch leakage

The gate is strict about leakage and final active intent preservation. It does not replace final SWE-bench execution.

## Run Smoke

Run a small smoke only:

```bash
python scripts/run_cair_batch_v2.py \
  --input data/candidates/diverse_seed_candidates.csv \
  --output-dir data/cair_instances/batch_v2_smoke \
  --model-generator deepseek-v4-flash \
  --model-critical deepseek-v4-pro \
  --model-reviewer deepseek-v4-pro \
  --limit 3 \
  --max-api-calls 60
```

Do not expand to limit 10+ until the smoke output has acceptable `accepted`, `accepted_with_template_dialogue`, and manual-review rates.
