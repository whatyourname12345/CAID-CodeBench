from __future__ import annotations

from typing import Any

from cair_v2.construction.localization_gold import LocalizationGold


LOCALIZATION_PROMPT = """Before editing code, identify the most relevant files and functions.

Use only the user issue/dialogue you have received. Do not write a patch and do not modify code.

Return strict JSON only:
{
  "ranked_files": [
    {
      "rank": 1,
      "path": "path/to/file.py",
      "reason": "brief reason"
    }
  ],
  "ranked_functions": [
    {
      "rank": 1,
      "symbol": "path/to/file.py::function_or_method",
      "reason": "brief reason"
    }
  ]
}

Rules:
- ranked_files must contain 1 to 10 entries.
- ranked_functions may contain 0 to 10 entries.
- If you cannot identify functions, use an empty ranked_functions list.
- Do not output a patch.
- Do not modify code.
- Do not mention benchmark test-list identifiers, private tests, or private implementation diffs.
- Do not claim you ran tests or inspected the repository.
- Each reason must be no more than 30 words.
- JSON only."""


def expected_output_schema() -> dict[str, Any]:
    return {
        "ranked_files": [
            {
                "rank": 1,
                "path": "path/to/file.py",
                "reason": "brief reason",
            }
        ],
        "ranked_functions": [
            {
                "rank": 1,
                "symbol": "path/to/file.py::function_or_method",
                "reason": "brief reason",
            }
        ],
    }


def build_localization_checkpoint(gold: LocalizationGold) -> dict[str, Any]:
    return {
        "prompt": LOCALIZATION_PROMPT,
        "expected_output_schema": expected_output_schema(),
        "gold": {
            "files": list(gold.files),
            "functions": list(gold.functions),
        },
        "metrics": {
            "file_hit_at_k": [1, 3, 5, 10],
            "function_hit_at_k": [1, 3, 5, 10],
        },
    }
