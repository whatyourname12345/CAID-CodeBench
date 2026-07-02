from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cair_v2.batch.batch_config import load_batch_config
from cair_v2.batch.batch_runner_v2 import BatchV2Options, run_batch_v2
from cair_v2.llm.clients import MissingAPIKeyError


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CAIR pipeline v2 batch construction.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None, help="Run output directory. Defaults to data/runs/{run-id}.")
    parser.add_argument("--run-id", default=None, help="Run id used when --output-dir is omitted.")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs/batch_default.yaml")
    parser.add_argument("--model-generator", default=None)
    parser.add_argument("--model-critical", default=None)
    parser.add_argument("--model-reviewer", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-api-calls", type=int, default=None)
    parser.add_argument("--client-max-retries", type=int, default=None)
    parser.add_argument("--client-timeout", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--optional-reviewer", action="store_true", help="Reserved for future v2 reviewer pass; default v2 skips reviewer.")
    parser.add_argument("--mode", choices=["staged-llm"], default="staged-llm")
    return parser.parse_args()


def default_run_id(input_path: Path) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{input_path.stem}_{stamp}"


def main() -> None:
    args = parse_args()
    config = load_batch_config(resolve(args.config))
    models = config.model_config.models
    output_dir = resolve(args.output_dir) if args.output_dir else PROJECT_ROOT / "data/runs" / (args.run_id or default_run_id(args.input))
    options = BatchV2Options(
        input_file=resolve(args.input),
        output_dir=output_dir,
        model_generator=args.model_generator or models.get("flash", "deepseek-v4-flash"),
        model_critical=args.model_critical or models.get("pro", "deepseek-v4-pro"),
        model_reviewer=args.model_reviewer or models.get("pro", "deepseek-v4-pro"),
        limit=args.limit if args.limit is not None else config.defaults.limit,
        max_api_calls=args.max_api_calls if args.max_api_calls is not None else config.defaults.max_api_calls,
        client_max_retries=args.client_max_retries if args.client_max_retries is not None else config.defaults.client_max_retries,
        client_timeout=args.client_timeout if args.client_timeout is not None else config.defaults.client_timeout,
        dry_run=args.dry_run,
        no_api=args.no_api,
        resume=args.resume,
        force=args.force,
        include_rejected=args.include_rejected,
        optional_reviewer=args.optional_reviewer,
        mode=args.mode,
        llm_params=config.model_config.llm_params,
    )
    try:
        state = run_batch_v2(options, config)
    except MissingAPIKeyError as exc:
        print(str(exc))
        raise SystemExit(2) from exc
    counts: dict[str, int] = {}
    normalized_counts: dict[str, int] = {}
    accepted_after_retry = 0
    for item in state.data.get("instances", {}).values():
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        normalized = str(item.get("normalized_status") or "unknown")
        normalized_counts[normalized] = normalized_counts.get(normalized, 0) + 1
        if item.get("accepted_after_retry"):
            accepted_after_retry += 1
    print(f"batch_id: {state.data.get('batch_id')}")
    print(f"run_dir: {output_dir}")
    print(f"pipeline_version: {state.data.get('pipeline_version')}")
    print(f"api_calls: {state.data.get('api_calls')}")
    print("--- status (legacy) ---")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print("--- normalized_status ---")
    for status, count in sorted(normalized_counts.items()):
        print(f"{status}: {count}")
    print(f"accepted_after_retry: {accepted_after_retry}")


if __name__ == "__main__":
    main()
