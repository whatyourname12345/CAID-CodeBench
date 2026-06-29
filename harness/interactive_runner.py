"""Interactive CAIR runner skeleton."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from adapters.base import AgentAdapter
from harness.dialogue_judge import DialogueJudge
from harness.exploration import ExplorationEvaluator, ExplorationExtractor
from harness.intent_state import IntentExtractor, IntentJudge, load_intent_state
from harness.patch_extractor import extract_patch
from harness.process_judge import ProcessJudge
from harness.schemas import DialogueScenario, TraceEvent, to_jsonable
from harness.trace import TraceRecorder
from harness.user_revision import UserRevisionPolicy
from harness.user_simulator import RuleBasedUserSimulator, UserSimulator


@dataclass
class InteractiveRunResult:
    task_id: str
    patch: str
    trace: list[TraceEvent]
    scores: dict[str, object]


class InteractiveRunner:
    def __init__(
        self,
        user_simulator: UserSimulator | None = None,
        dialogue_judge: DialogueJudge | None = None,
        process_judge: ProcessJudge | None = None,
        intent_extractor: IntentExtractor | None = None,
        intent_judge: IntentJudge | None = None,
        exploration_extractor: ExplorationExtractor | None = None,
        exploration_evaluator: ExplorationEvaluator | None = None,
        revision_policy: UserRevisionPolicy | None = None,
        max_turns: int = 8,
    ) -> None:
        self.user_simulator = user_simulator or RuleBasedUserSimulator()
        self.dialogue_judge = dialogue_judge
        self.process_judge = process_judge
        self.intent_extractor = intent_extractor
        self.intent_judge = intent_judge
        self.exploration_extractor = exploration_extractor
        self.exploration_evaluator = exploration_evaluator
        self.revision_policy = revision_policy or UserRevisionPolicy()
        self.max_turns = max_turns

    def run(
        self,
        scenario: DialogueScenario,
        adapter: AgentAdapter,
        repo_path: Path,
        exploration_ground_truth: dict[str, object] | None = None,
    ) -> InteractiveRunResult:
        """Run a dialogue task.

        Current adapters expose a one-shot `run` method, so this runner records
        the initial user message, delegates to the adapter, and scores the
        resulting trace. Multi-turn adapters should implement the same result
        contract and include message/tool events in metadata["trace_events"].
        """
        recorder = TraceRecorder()
        initial = self.user_simulator.initial_message(scenario)
        recorder.append("user", "message", initial, turn_id=1)

        result = adapter.run(
            {
                "task_id": scenario.task_id,
                "repo": scenario.repo,
                "base_commit": scenario.base_commit,
                "initial_user_query": initial,
                "dialogue_scenario": to_jsonable(scenario),
            },
            repo_path,
        )
        recorder.append("agent", "message", result.trace or "Agent completed execution.", turn_id=1)
        patch = result.patch or extract_patch(result.trace)
        if patch:
            recorder.append("agent", "patch", patch, turn_id=1)

        for item in result.metadata.get("trace_events", []) if result.metadata else []:
            if isinstance(item, dict):
                recorder.append(
                    str(item.get("actor", "agent")),
                    str(item.get("event_type", "message")),
                    str(item.get("content", "")),
                    turn_id=int(item.get("turn_id", 1)),
                    tool=item.get("tool"),
                    command=item.get("command"),
                    metadata=item.get("metadata", {}),
                )

        scores = self.score_trace(scenario, recorder.events, exploration_ground_truth)
        return InteractiveRunResult(task_id=scenario.task_id, patch=patch, trace=recorder.events, scores=scores)

    def score_trace(
        self,
        scenario: DialogueScenario,
        trace: list[TraceEvent],
        exploration_ground_truth: dict[str, object] | None = None,
    ) -> dict[str, object]:
        scores: dict[str, object] = {}
        gold_intent = load_intent_state(scenario.gold_intent_state_path)

        if self.intent_extractor is not None and self.intent_judge is not None:
            predicted_intent = self.intent_extractor.extract(trace)
            intent_score = self.intent_judge.score(predicted_intent, gold_intent)
            scores["intent"] = {**to_jsonable(intent_score), "overall": intent_score.overall}

        if self.dialogue_judge is not None:
            dialogue_score = self.dialogue_judge.score(trace)
            scores["dialogue"] = {**to_jsonable(dialogue_score), "overall": dialogue_score.overall}

        if self.process_judge is not None:
            process_score = self.process_judge.score(trace, gold_intent)
            scores["process"] = {**to_jsonable(process_score), "overall": process_score.overall}

        if (
            self.exploration_extractor is not None
            and self.exploration_evaluator is not None
            and exploration_ground_truth is not None
        ):
            regions = self.exploration_extractor.extract(trace)
            exploration_score = self.exploration_evaluator.score(regions, exploration_ground_truth)
            scores["exploration"] = {**to_jsonable(exploration_score), "overall": exploration_score.overall}

        return scores


def write_interactive_result(result: InteractiveRunResult, output_dir: str | Path) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{result.task_id}.patch").write_text(result.patch, encoding="utf-8")
    with (output / f"{result.task_id}.scores.json").open("w", encoding="utf-8") as file:
        json.dump(result.scores, file, indent=2, ensure_ascii=False)
        file.write("\n")
    with (output / f"{result.task_id}.trace.jsonl").open("w", encoding="utf-8") as file:
        for event in result.trace:
            file.write(json.dumps(to_jsonable(event), ensure_ascii=False) + "\n")
