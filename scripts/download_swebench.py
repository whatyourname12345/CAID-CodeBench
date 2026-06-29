"""Download SWE-bench from Hugging Face Datasets."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Download SWE-bench dataset.")
    parser.add_argument("--name", default="SWE-bench/SWE-bench", help="Hugging Face dataset name.")
    parser.add_argument("--output-dir", default="datasets/swe-bench", help="Directory for JSONL exports.")
    parser.add_argument("--hf-endpoint", help="Optional Hugging Face endpoint, for example https://hf-mirror.com.")
    args = parser.parse_args()

    if args.hf_endpoint:
        os.environ["HF_ENDPOINT"] = args.hf_endpoint

    from datasets import load_dataset

    dataset = load_dataset(args.name)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for split_name, split in dataset.items():
        output_path = output_dir / f"{split_name}.jsonl"
        split.to_json(str(output_path), orient="records", lines=True, force_ascii=False)
        print(f"Wrote {len(split)} rows to {output_path}")


if __name__ == "__main__":
    main()
