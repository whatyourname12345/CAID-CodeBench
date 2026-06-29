"""Create a simple leaderboard from score JSON files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a CAIR-CodeBench leaderboard.")
    parser.add_argument("scores", nargs="+", help="Score JSON files.")
    args = parser.parse_args()

    rows = []
    for score_file in args.scores:
        scores = json.loads(Path(score_file).read_text(encoding="utf-8"))
        total = len(scores)
        resolved = sum(1 for item in scores if item.get("resolved"))
        rows.append({"file": score_file, "resolved": resolved, "total": total, "rate": resolved / total if total else 0.0})

    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
