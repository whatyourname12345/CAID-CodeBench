from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cair_v2.construction.views import agent_view_payload


EXPORTABLE_STATUSES = {"accepted", "accepted_with_template_dialogue"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)) if path.is_absolute() and path.is_relative_to(PROJECT_ROOT) else str(path)


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


def collect_exportable(input_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    state = load_batch_state(input_dir)
    records: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for instance_id, item in sorted((state.get("instances") or {}).items()):
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        if status not in EXPORTABLE_STATUSES:
            continue
        instance_dir = Path(item.get("path") or input_dir / instance_id)
        if not instance_dir.is_absolute():
            instance_dir = PROJECT_ROOT / instance_dir
        if not instance_dir.exists():
            instance_dir = input_dir / instance_id
        compact_path = instance_dir / "cair_instance.json"
        if not compact_path.exists():
            continue
        instance = json.loads(compact_path.read_text(encoding="utf-8"))
        quality_path = instance_dir / "quality_report.json"
        quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
        records.append(
            {
                "instance_id": instance_id,
                "state_item": item,
                "instance_dir": instance_dir,
                "instance": instance,
                "quality": quality,
            }
        )
    return state, records, status_counts


def write_json_stable(path: Path, payload: dict[str, Any], *, force: bool = True) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") != text and not force:
        raise FileExistsError(f"Refusing to overwrite existing different file: {path}")
    path.write_text(text, encoding="utf-8")


def load_existing_index(index_path: Path) -> dict[str, dict[str, Any]]:
    if not index_path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        instance_id = row.get("instance_id")
        if instance_id:
            rows[str(instance_id)] = row
    return rows


def copy_if_same_or_forced(source: Path, target: Path, *, force: bool) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_bytes() != source.read_bytes() and not force:
        raise FileExistsError(f"Refusing to overwrite existing different file: {target}")
    shutil.copy2(source, target)


def promote_instance(
    record: dict[str, Any],
    *,
    instances_dir: Path,
    input_dir: Path,
    pipeline_version: str | None,
    source_run_id: str,
    force: bool,
) -> dict[str, Any]:
    instance_id = record["instance_id"]
    source_dir: Path = record["instance_dir"]
    target_dir = instances_dir / instance_id
    for name in ["cair_instance.json", "quality_report.json", "source_record.json", "README.md"]:
        copy_if_same_or_forced(source_dir / name, target_dir / name, force=force)
    provenance = {
        "instance_id": instance_id,
        "source_run_id": source_run_id,
        "source_run_dir": relative(input_dir),
        "source_instance_dir": relative(source_dir),
        "status": record["state_item"].get("status"),
        "normalized_status": record["state_item"].get("normalized_status"),
        "accepted_after_retry": bool(record["state_item"].get("accepted_after_retry")),
        "api_calls": record["state_item"].get("api_calls"),
        "pipeline_version": pipeline_version,
    }
    write_json_stable(target_dir / "provenance.json", provenance, force=force)
    return provenance


def quality_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "accepted_records": len(records),
        "quality_passed": 0,
        "template_fallback_used": 0,
        "old_progressive_disclosure_pattern": 0,
        "unresolved_wrong_claims": 0,
        "scenario_fit_not_pass": 0,
        "final_active_intent_consistency_failed": 0,
        "localization_ready": 0,
    }
    for record in records:
        quality = record.get("quality") or {}
        noisy = quality.get("noisy_refinement") or {}
        checks = quality.get("checks") or {}
        template = quality.get("template") or {}
        localization = quality.get("localization") or {}
        if quality.get("passed") is True:
            summary["quality_passed"] += 1
        if template.get("template_fallback_used") is True:
            summary["template_fallback_used"] += 1
        if noisy.get("old_progressive_disclosure_pattern") is True:
            summary["old_progressive_disclosure_pattern"] += 1
        if int(noisy.get("unresolved_wrong_claims") or 0) != 0:
            summary["unresolved_wrong_claims"] += 1
        if noisy.get("scenario_fit") != "pass":
            summary["scenario_fit_not_pass"] += 1
        if checks.get("final_active_intent_consistency") is not True:
            summary["final_active_intent_consistency_failed"] += 1
        if localization.get("status") == "ready":
            summary["localization_ready"] += 1
    return summary


def write_release_database(
    *,
    input_dir: Path,
    release_dir: Path,
    instances_dir: Path | None,
    release_id: str,
    source_run_id: str,
    force: bool,
) -> None:
    state, records, status_counts = collect_exportable(input_dir)
    pipeline_version = state.get("pipeline_version")
    agent_dir = release_dir / "agent"
    evaluator_dir = release_dir / "evaluator"
    index_path = release_dir / "index.jsonl"
    existing_index = load_existing_index(index_path)
    index_rows: list[dict[str, Any]] = []
    for record in records:
        instance_id = record["instance_id"]
        instance_dir: Path = record["instance_dir"]
        agent_payload = view_payload(
            record["instance"],
            evaluator_view=False,
            include_debug=False,
            include_patch=False,
            instance_dir=instance_dir,
        )
        evaluator_payload = view_payload(
            record["instance"],
            evaluator_view=True,
            include_debug=False,
            include_patch=False,
            instance_dir=instance_dir,
        )
        agent_path = agent_dir / f"{instance_id}.json"
        evaluator_path = evaluator_dir / f"{instance_id}.json"
        write_json_stable(agent_path, agent_payload, force=force)
        write_json_stable(evaluator_path, evaluator_payload, force=force)
        provenance = (
            promote_instance(
                record,
                instances_dir=instances_dir,
                input_dir=input_dir,
                pipeline_version=pipeline_version,
                source_run_id=source_run_id,
                force=force,
            )
            if instances_dir is not None
            else {}
        )
        quality = record.get("quality") or {}
        localization = quality.get("localization") or {}
        index_rows.append(
            {
                "instance_id": instance_id,
                "repo": record["instance"].get("repo"),
                "base_commit": record["instance"].get("base_commit"),
                "source_name": record["instance"].get("source_name"),
                "domain": record["instance"].get("domain"),
                "agent_path": relative(agent_path),
                "evaluator_path": relative(evaluator_path),
                "canonical_instance_path": relative(instances_dir / instance_id) if instances_dir else None,
                "source_run_id": source_run_id,
                "source_instance_dir": relative(instance_dir),
                "status": record["state_item"].get("status"),
                "normalized_status": record["state_item"].get("normalized_status"),
                "accepted_after_retry": bool(record["state_item"].get("accepted_after_retry")),
                "api_calls": record["state_item"].get("api_calls"),
                "quality_passed": quality.get("passed"),
                "localization_status": localization.get("status"),
                "provenance": provenance,
            }
        )

    merged_index = existing_index | {str(row["instance_id"]): row for row in index_rows}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8") as handle:
        for _instance_id, row in sorted(merged_index.items()):
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    previous_manifest_path = release_dir / "manifest.json"
    previous_manifest = (
        json.loads(previous_manifest_path.read_text(encoding="utf-8"))
        if previous_manifest_path.exists()
        else {}
    )
    previous_source_runs = previous_manifest.get("source_run_ids") or []
    source_run_ids = sorted({*map(str, previous_source_runs), source_run_id})
    manifest = {
        "release_id": release_id,
        "format": "cair_v2_per_instance_release",
        "schema_version": pipeline_version or "cair_v2",
        "generated_at": utc_now(),
        "source_run_ids": source_run_ids,
        "latest_import_source_run_id": source_run_id,
        "source_run_dir": relative(input_dir),
        "release_dir": relative(release_dir),
        "instances_dir": relative(instances_dir) if instances_dir else None,
        "index_path": relative(index_path),
        "agent_view_dir": relative(agent_dir),
        "evaluator_view_dir": relative(evaluator_dir),
        "instance_count": len(merged_index),
        "latest_import_instance_count": len(records),
        "exportable_statuses": sorted(EXPORTABLE_STATUSES),
        "latest_import_status_counts": status_counts,
        "latest_import_quality_summary": quality_summary(records),
        "visibility_policy": {
            "agent_view_excludes": [
                "oracle",
                "localization gold",
                "evaluator-only oracle prompts",
                "non-exposed semantic fact units",
                "raw LLM output",
                ".build debug data",
                "hidden/private test details",
                "full reference patch",
            ],
            "evaluator_view_includes": [
                "oracle",
                "localization gold",
                "quality report summary",
                "Hit@k metric definitions",
            ],
        },
    }
    write_json_stable(release_dir / "manifest.json", manifest, force=True)
    (release_dir / "quality_report.md").write_text(
        "\n".join(
            [
                "# CAIR v2 Release Quality Report",
                "",
                f"Release: `{release_id}`",
                f"Latest import source run: `{source_run_id}`",
                f"Latest imported instances: {len(records)}",
                f"Release database instances: {len(merged_index)}",
                "",
                "Release layout:",
                "",
                "- `agent/{instance_id}.json`: agent-facing payload.",
                "- `evaluator/{instance_id}.json`: evaluator-facing payload.",
                "- `index.jsonl`: one row per instance with paths and provenance.",
                "- `manifest.json`: release-level metadata and visibility policy.",
                "",
                "Agent view excludes oracle fields, localization gold, raw LLM outputs, hidden tests, debug `.build` data, and full reference patches.",
                "Evaluator view keeps oracle and localization gold for scoring.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def export_jsonl_compat(
    *,
    input_dir: Path,
    output_jsonl: Path,
    manifest_path: Path,
    quality_report_path: Path,
    evaluator_view: bool,
    include_debug: bool = False,
    include_patch: bool = False,
) -> None:
    _state, records, status_counts = collect_exportable(input_dir)
    exported: list[dict[str, Any]] = []
    for record in records:
        instance_dir: Path = record["instance_dir"]
        exported.append(
            view_payload(
                record["instance"],
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
    parser.add_argument("--output", type=Path, default=None, help="Compatibility mode: write one JSONL view.")
    parser.add_argument("--release-dir", type=Path, default=PROJECT_ROOT / "data/release")
    parser.add_argument("--instances-dir", type=Path, default=PROJECT_ROOT / "data/instances")
    parser.add_argument("--release-id", default=None)
    parser.add_argument("--source-run-id", default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--quality-report", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="Overwrite existing per-instance release files when contents differ.")
    parser.add_argument("--include-debug", action="store_true")
    parser.add_argument("--include-patch", action="store_true", help="Evaluator-view only; include source record for traceability.")
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--agent-view", action="store_true", help="Compatibility JSONL mode: export agent view without localization gold or oracle.")
    view.add_argument("--evaluator-view", action="store_true", help="Compatibility JSONL mode: export evaluator view with localization gold and oracle.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_dir = resolve(args.input_dir)
    source_run_id = args.source_run_id or input_dir.name
    release_id = args.release_id or "cair-v2-release"
    if args.output is not None:
        output_jsonl = resolve(args.output)
        manifest_path = resolve(args.manifest) if args.manifest else output_jsonl.with_name(f"{output_jsonl.stem}_manifest.json")
        quality_report_path = (
            resolve(args.quality_report)
            if args.quality_report
            else output_jsonl.with_name(f"{output_jsonl.stem}_quality_report.md")
        )
        export_jsonl_compat(
            input_dir=input_dir,
            output_jsonl=output_jsonl,
            manifest_path=manifest_path,
            quality_report_path=quality_report_path,
            evaluator_view=args.evaluator_view,
            include_debug=args.include_debug,
            include_patch=args.include_patch,
        )
        print(f"Wrote {args.output}")
        print(f"Wrote {relative(manifest_path)}")
        print(f"Wrote {relative(quality_report_path)}")
        return
    write_release_database(
        input_dir=input_dir,
        release_dir=resolve(args.release_dir),
        instances_dir=resolve(args.instances_dir) if args.instances_dir else None,
        release_id=release_id,
        source_run_id=source_run_id,
        force=args.force,
    )
    print(f"Wrote {relative(resolve(args.release_dir))}")


if __name__ == "__main__":
    main()
