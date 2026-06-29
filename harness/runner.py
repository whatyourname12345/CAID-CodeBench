"""CLI entry point for running CAIR-CodeBench tasks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from harness.metrics import compute_cair_report, compute_score


def load_tasks(path: str | Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def run_task(task: dict[str, Any]) -> dict[str, object]:
    """Run a task placeholder.

    The concrete agent execution, checkout, patch application, and tests are
    intentionally wired in later by benchmark-specific adapters.
    """
    task_id = str(task["task_id"])
    if "dialogue_scenario_path" in task:
        return compute_cair_report(
            task_id=task_id,
            resolved=False,
            tests_passed=False,
            intermediate_scores={
                "intent": {"overall": 0.0},
                "dialogue": {"overall": 0.0},
                "process": {"overall": 0.0},
                "exploration": {"overall": 0.0},
            },
        )
    return compute_score(task_id=task_id, tests_passed=False).as_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CAIR-CodeBench tasks.")
    parser.add_argument("--tasks", default="datasets/tasks.jsonl", help="Path to task JSONL file.")
    parser.add_argument("--output", default="results/scores/scores.json", help="Score output path.")
    args = parser.parse_args()

    scores = [run_task(task) for task in load_tasks(args.tasks)]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(scores, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(scores)} scores to {output_path}")


if __name__ == "__main__":
    main()
