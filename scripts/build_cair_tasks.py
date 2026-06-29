"""Build CAIR-CodeBench dialogue tasks from SWE-bench JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.scenario_builder import write_task_artifacts
from harness.swe_dataset import load_swe_instances, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Build CAIR dialogue tasks from SWE-bench.")
    parser.add_argument("--swe-jsonl", default="datasets/swe-bench/dev.jsonl", help="Input SWE-bench JSONL.")
    parser.add_argument("--limit", type=int, help="Optional number of tasks to build.")
    parser.add_argument("--tasks-out", default="datasets/tasks.jsonl", help="Output CAIR task JSONL.")
    parser.add_argument("--dialogues-dir", default="datasets/dialogues", help="Output dialogue scenario directory.")
    parser.add_argument("--gold-dir", default="datasets/gold_intent_states", help="Output gold intent directory.")
    parser.add_argument(
        "--evaluation-dir",
        default="datasets/evaluation_specs",
        help="Output SWE executable evaluation spec directory.",
    )
    args = parser.parse_args()

    instances = load_swe_instances(args.swe_jsonl, limit=args.limit)
    rows = [
        write_task_artifacts(
            instance,
            dialogues_dir=args.dialogues_dir,
            gold_dir=args.gold_dir,
            evaluation_dir=args.evaluation_dir,
        )
        for instance in instances
    ]
    write_jsonl(args.tasks_out, rows)

    print(f"Wrote {len(rows)} CAIR tasks to {args.tasks_out}")
    print(f"Dialogue scenarios: {args.dialogues_dir}")
    print(f"Gold intent states: {args.gold_dir}")
    print(f"Evaluation specs: {args.evaluation_dir}")


if __name__ == "__main__":
    main()
