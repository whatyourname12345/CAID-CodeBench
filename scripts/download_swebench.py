from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATASETS = {
    "full": {
        "hf_dataset": "princeton-nlp/SWE-bench",
        "output": "swebench_full.jsonl",
    },
    "lite": {
        "hf_dataset": "princeton-nlp/SWE-bench_Lite",
        "output": "swebench_lite.jsonl",
    },
    "verified": {
        "hf_dataset": "princeton-nlp/SWE-bench_Verified",
        "output": "swebench_verified.jsonl",
    },
}


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def write_dataset_jsonl(*, hf_dataset: str, source_name: str, output_path: Path) -> int:
    from datasets import DatasetDict, load_dataset

    dataset = load_dataset(hf_dataset)
    rows = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        if isinstance(dataset, DatasetDict):
            split_items = dataset.items()
        else:
            split_items = [("default", dataset)]
        for split_name, split in split_items:
            for row in split:
                record = dict(row)
                record["_hf_split"] = split_name
                record["_hf_dataset"] = hf_dataset
                record["_source_name"] = source_name
                handle.write(json.dumps(record, ensure_ascii=False, default=json_default) + "\n")
                rows += 1
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download SWE-bench Full/Lite/Verified JSONL files.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    parser.add_argument("--hf-endpoint", help="Optional Hugging Face endpoint, for example https://hf-mirror.com.")
    parser.add_argument(
        "--only",
        choices=sorted(DATASETS),
        nargs="*",
        help="Optional subset to download. Defaults to full, lite, verified.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.hf_endpoint:
        os.environ["HF_ENDPOINT"] = args.hf_endpoint
    output_dir = resolve(args.output_dir)
    selected = args.only or ["full", "lite", "verified"]
    print("Downloading SWE-bench datasets without cloning repos or running evaluations.")
    for source_name in selected:
        spec = DATASETS[source_name]
        output_path = output_dir / spec["output"]
        rows = write_dataset_jsonl(
            hf_dataset=spec["hf_dataset"],
            source_name=source_name,
            output_path=output_path,
        )
        print(f"{source_name}: wrote {rows} rows to {output_path}")


if __name__ == "__main__":
    main()
