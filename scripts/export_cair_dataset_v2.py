from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cair_v2.construction.views import agent_view_payload


EXPORTABLE_STATUSES = {"accepted", "accepted_with_template_dialogue"}


def load_batch_state(batch_dir: Path) -> dict[str, Any]:
    path = batch_dir / "batch_state.json"
    if not path.exists():
        return {"instances": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def view_payload(instance: dict[str, Any], *, evaluator_view: bool, include_debug: bool, include_patch: bool, instance_dir: Path) -> dict[str, Any]:
    if not evaluator_view and include_debug:
        raise ValueError("--include-debug is evaluator-view only")
    if not evaluator_view and include_patch:
        raise ValueError("--include-patch is evaluator-view only")
    clean = agent_view_payload(instance) if not evaluator_view else json.loads(json.dumps(instance, ensure_ascii=False))
    if evaluator_view:
        quality_path = instance_dir / "quality_report.json"
        if quality_path.exists():
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            clean["quality_report_summary"] = {
                "passed": quality.get("passed"),
                "status": quality.get("status"),
                "hard_failures": quality.get("hard_failures", []),
                "soft_warnings": quality.get("soft_warnings", []),
                "localization": quality.get("localization", {}),
            }
    if include_debug:
        debug_path = instance_dir / ".build" / "intermediate_debug.json"
        if debug_path.exists():
            clean["_debug"] = json.loads(debug_path.read_text(encoding="utf-8"))
    if include_patch:
        source_path = instance_dir / "source_record.json"
        if source_path.exists():
            clean["_source_record"] = json.loads(source_path.read_text(encoding="utf-8"))
    return clean


def export_v2(
    *,
    input_dir: Path,
    output_jsonl: Path,
    manifest_path: Path,
    quality_report_path: Path,
    evaluator_view: bool,
    include_debug: bool = False,
    include_patch: bool = False,
) -> None:
    state = load_batch_state(input_dir)
    exported: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for instance_id, item in sorted((state.get("instances") or {}).items()):
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in EXPORTABLE_STATUSES:
            continue
        instance_dir = Path(item.get("path") or input_dir / instance_id)
        if not instance_dir.is_absolute():
            instance_dir = PROJECT_ROOT / instance_dir
        compact_path = instance_dir / "cair_instance.json"
        if not compact_path.exists():
            continue
        instance = json.loads(compact_path.read_text(encoding="utf-8"))
        exported.append(
            view_payload(
                instance,
                evaluator_view=evaluator_view,
                include_debug=include_debug,
                include_patch=include_patch,
                instance_dir=instance_dir,
            )
        )

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for item in exported:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    manifest = {
        "format": "cair_batch_v2_noisy_refinement",
        "input_dir": str(input_dir),
        "output_jsonl": str(output_jsonl),
        "count": len(exported),
        "view": "evaluator" if evaluator_view else "agent",
        "exportable_statuses": sorted(EXPORTABLE_STATUSES),
        "status_counts": status_counts,
        "include_debug": include_debug,
        "include_patch": include_patch,
        "default_excludes": [
            "full reference patch",
            "raw LLM output",
            ".build debug data",
            "hidden/private test details",
            "agent-view oracle and evaluator-only prompts",
            "agent-view localization gold",
            "agent-view non-exposed semantic fact units",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    quality_report_path.write_text(
        "\n".join(
            [
                "# CAIR Batch v2 Release Quality Report",
                "",
                f"Exported instances: {len(exported)}",
                f"View: `{'evaluator' if evaluator_view else 'agent'}`",
                f"Include debug: `{include_debug}`",
                f"Include patch/source record: `{include_patch}`",
                "",
                "Only `accepted` and `accepted_with_template_dialogue` instances are exported.",
                "Each JSONL line is one compact `cair_instance.json` payload.",
                "Agent view removes localization gold, oracle, evaluator-only prompts, and non-exposed semantic facts.",
                "Evaluator view keeps oracle and localization gold.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export accepted CAIR pipeline v2 compact instances.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data/releases/cair_batch_v2.jsonl")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data/releases/cair_batch_v2_manifest.json")
    parser.add_argument("--quality-report", type=Path, default=PROJECT_ROOT / "data/releases/cair_batch_v2_quality_report.md")
    parser.add_argument("--include-debug", action="store_true")
    parser.add_argument("--include-patch", action="store_true", help="Evaluator-view only; include source record for traceability.")
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--agent-view", action="store_true", help="Export agent view without localization gold or oracle. Default.")
    view.add_argument("--evaluator-view", action="store_true", help="Export evaluator view with localization gold and oracle.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir if args.input_dir.is_absolute() else PROJECT_ROOT / args.input_dir
    export_v2(
        input_dir=input_dir,
        output_jsonl=args.output if args.output.is_absolute() else PROJECT_ROOT / args.output,
        manifest_path=args.manifest if args.manifest.is_absolute() else PROJECT_ROOT / args.manifest,
        quality_report_path=args.quality_report if args.quality_report.is_absolute() else PROJECT_ROOT / args.quality_report,
        evaluator_view=args.evaluator_view,
        include_debug=args.include_debug,
        include_patch=args.include_patch,
    )
    print(f"Wrote {args.output}")
    print(f"Wrote {args.manifest}")
    print(f"Wrote {args.quality_report}")


if __name__ == "__main__":
    main()
