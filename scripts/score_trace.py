"""Score an existing CAIR trace with configured intermediate evaluators.

The default framework only defines evaluator interfaces. This script currently
loads the trace and writes an empty score object unless concrete judges are
injected in code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.interactive_runner import InteractiveRunner
from harness.trace import load_trace
from harness.user_simulator import load_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a CAIR trace JSONL file with configured evaluators.")
    parser.add_argument("--scenario", required=True, help="Dialogue scenario JSON.")
    parser.add_argument("--trace", required=True, help="Trace JSONL.")
    parser.add_argument("--output", required=True, help="Output score JSON.")
    parser.add_argument("--explore-gt", help="Optional SWE-Explore-style ground-truth JSON.")
    args = parser.parse_args()

    scenario = load_scenario(args.scenario)
    trace = load_trace(args.trace)
    explore_gt = None
    if args.explore_gt:
        explore_gt = json.loads(Path(args.explore_gt).read_text(encoding="utf-8"))

    scores = InteractiveRunner().score_trace(scenario, trace, explore_gt)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(scores, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote scores to {output_path}")


if __name__ == "__main__":
    main()
