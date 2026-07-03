from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cair_v2.construction.instance_io import read_json, write_json
from cair_v2.construction.patch_summarizer import update_patch_metadata
from cair_v2.construction.sanitizer import SanitizerResult, sanitize_semantic_capsule
from cair_v2.llm.clients import DeepSeekClient
from cair_v2.llm.prompt_runner import PromptStepResult, run_prompt_step


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = PROJECT_ROOT / "cair_v2/prompts"

ALLOWED_FACT_TYPES = {
    "symptom",
    "observed_behavior",
    "expected_behavior",
    "reproduction",
    "error_message",
    "affected_component",
    "active_constraint",
    "negative_constraint",
    "regression_expectation",
    "obsolete_candidate",
    "rejected_solution",
    "non_goal",
    "boundary_case",
    "implementation_hint",
    "acceptance_signal",
    "ambiguity_or_correction",
    "design_suggestion_non_goal",
    "workaround_to_reject",
    "conflict_or_tension",
}

REVISION_FACT_TYPES = {
    "rejected_solution",
    "obsolete_candidate",
    "negative_constraint",
    "regression_expectation",
    "ambiguity_or_correction",
    "design_suggestion_non_goal",
    "workaround_to_reject",
    "conflict_or_tension",
}


@dataclass
class SemanticCapsuleResult:
    ok: bool
    capsule: dict[str, Any] = field(default_factory=dict)
    status: str = "unknown"
    api_call_made: bool = False
    api_calls_made: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    prompt_result: PromptStepResult | None = None
    error_type: str | None = None
    repair_success: bool = False
    retry_success: bool = False
    fallback_used: bool = False
    model_used: str | None = None


def _list_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value:
            return 0
        return int(value)
    text = str(value or "").strip()
    if not text or text.lower() in {"[]", "none", "nan", "null"}:
        return 0
    if text.isdigit():
        return int(text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return len(parsed)
    except Exception:
        pass
    return text.count(",") + 1 if text.startswith("[") else 1


def _first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return ""


def safe_patch_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    patch_summary = metadata.get("patch_summary") if isinstance(metadata.get("patch_summary"), dict) else {}
    test_summary = metadata.get("test_patch_summary") if isinstance(metadata.get("test_patch_summary"), dict) else {}
    return {
        "patch_files_count": metadata.get("patch_files_count"),
        "test_files_count": metadata.get("test_files_count"),
        "source_files_touched_count": len(patch_summary.get("touched_source_files") or []),
        "test_files_touched_count": len(test_summary.get("added_or_modified_test_files") or []),
        "change_size": patch_summary.get("change_size"),
        "likely_affected_components": patch_summary.get("likely_affected_components") or [],
        "implementation_leakage_level": patch_summary.get("implementation_leakage_level"),
        "private_failing_test_count": None,
        "private_regression_test_count": None,
    }


def build_semantic_capsule_context(instance_dir: Path) -> dict[str, Any]:
    source_record = read_json(instance_dir / "source_record.json")
    source = source_record.get("source_swebench") if isinstance(source_record.get("source_swebench"), dict) else {}
    candidate_private_path = instance_dir / ".build" / "candidate_record_private.json"
    if candidate_private_path.exists():
        private_record = read_json(candidate_private_path)
    else:
        private_record = {}
    metadata_path = instance_dir / "patch_metadata.json"
    build_metadata_path = instance_dir / ".build" / "patch_metadata.json"
    if metadata_path.exists():
        metadata = read_json(metadata_path)
    elif build_metadata_path.exists():
        metadata = read_json(build_metadata_path)
    else:
        metadata = {}
    safe_metadata = safe_patch_metadata(metadata)
    candidate_meta = source_record.get("candidate_metadata") if isinstance(source_record.get("candidate_metadata"), dict) else {}
    safe_metadata["private_failing_test_count"] = (
        _list_count(_first_value(private_record, "FAIL_TO_PASS", "fail_to_pass"))
        or _list_count(candidate_meta.get("private_failing_check_count"))
        or _list_count(candidate_meta.get("private_failing_test_count"))
    )
    safe_metadata["private_regression_test_count"] = (
        _list_count(_first_value(private_record, "PASS_TO_PASS", "pass_to_pass"))
        or _list_count(candidate_meta.get("private_regression_check_count"))
        or _list_count(candidate_meta.get("private_regression_test_count"))
    )
    return {
        "instance_id": source_record.get("instance_id"),
        "repo": source_record.get("repo"),
        "source_name": source_record.get("source_name"),
        "problem_statement": source.get("problem_statement"),
        "hints_text": source.get("hints_text"),
        "safe_patch_metadata": safe_metadata,
        "output_contract": {
            "fact_unit_types": sorted(ALLOWED_FACT_TYPES),
            "revision_fact_types": sorted(REVISION_FACT_TYPES),
            "do_not_emit": [
                "reference patch code",
                "diff lines",
                "private test names",
                "FAIL_TO_PASS",
                "PASS_TO_PASS",
                "implementation hints as expected behavior",
            ],
        },
    }


def _archive_prompt_result(instance_dir: Path, result: PromptStepResult, label: str) -> None:
    raw_dir = instance_dir / ".build" / "raw_llm_outputs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for path in [result.raw_path, result.parsed_path]:
        if path and path.exists():
            shutil.copy2(path, raw_dir / f"{label}.{path.name.split('.', 1)[-1]}")


def write_semantic_capsule(instance_dir: Path, capsule: dict[str, Any]) -> None:
    build_dir = instance_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)
    write_json(build_dir / "semantic_capsule.json", capsule)


def load_semantic_capsule(instance_dir: Path) -> dict[str, Any]:
    path = instance_dir / ".build" / "semantic_capsule.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return read_json(path)


def _source_problem_statement(instance_dir: Path) -> str:
    source_record = read_json(instance_dir / "source_record.json")
    source = source_record.get("source_swebench") if isinstance(source_record.get("source_swebench"), dict) else {}
    return str(source.get("problem_statement") or "")


def validate_semantic_capsule(capsule: dict[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    suitability = capsule.get("suitability") if isinstance(capsule.get("suitability"), dict) else {}
    if suitability.get("is_cair_suitable") is not True:
        warnings.append("semantic_capsule marks the candidate as not clearly CAIR-suitable")
    units = capsule.get("fact_units") if isinstance(capsule.get("fact_units"), list) else []
    if len(units) < 3:
        errors.append("semantic_capsule.fact_units must contain at least 3 facts")
    ids: set[str] = set()
    type_by_id: dict[str, str] = {}
    covered_types: set[str] = set()
    for idx, unit in enumerate(units):
        if not isinstance(unit, dict):
            errors.append(f"fact_units[{idx}] must be an object")
            continue
        unit_id = str(unit.get("unit_id") or "")
        if not unit_id:
            errors.append(f"fact_units[{idx}].unit_id is missing")
        elif unit_id in ids:
            errors.append(f"duplicate fact unit id: {unit_id}")
        ids.add(unit_id)
        unit_type = str(unit.get("type") or "")
        if unit_id:
            type_by_id[unit_id] = unit_type
        if unit_type not in ALLOWED_FACT_TYPES:
            errors.append(f"fact_units[{idx}].type is invalid: {unit_type}")
        covered_types.add(unit_type)
        if unit_type == "implementation_hint":
            if unit.get("active_by_default") is not False or unit.get("expose_to_user") is not False:
                errors.append(f"implementation_hint {unit_id} must be inactive and non-exposed")
            if not str(unit.get("risk") or "").strip():
                errors.append(f"implementation_hint {unit_id} must have risk")
    if len(covered_types & {"symptom", "observed_behavior", "expected_behavior"}) < 2:
        errors.append("fact_units must cover at least two of symptom/observed_behavior/expected_behavior")
    revision_support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    if not isinstance(capsule.get("revision_support"), dict):
        errors.append("revision_support is missing")
    has_revision_fact = revision_support.get("has_revision_fact")
    if not isinstance(has_revision_fact, bool):
        errors.append("revision_support.has_revision_fact must be boolean")
    revision_unit_ids = revision_support.get("revision_unit_ids")
    if not isinstance(revision_unit_ids, list):
        errors.append("revision_support.revision_unit_ids must be a list")
        revision_unit_ids = []
    revision_types = revision_support.get("revision_types")
    if not isinstance(revision_types, list):
        errors.append("revision_support.revision_types must be a list")
        revision_types = []
    if has_revision_fact is True:
        if not revision_unit_ids:
            errors.append("revision_support.has_revision_fact=true requires revision_unit_ids")
        for unit_id in revision_unit_ids:
            text_id = str(unit_id)
            if text_id not in ids:
                errors.append(f"revision_support references unknown unit {text_id}")
            elif type_by_id.get(text_id) not in REVISION_FACT_TYPES:
                errors.append(f"revision_support unit {text_id} has non-revision type {type_by_id.get(text_id)}")
        for unit_type in revision_types:
            if str(unit_type) not in REVISION_FACT_TYPES:
                errors.append(f"revision_support.revision_types contains invalid type {unit_type}")
    elif has_revision_fact is False and revision_unit_ids:
        warnings.append("revision_support.has_revision_fact=false but revision_unit_ids is non-empty")
    if not str(revision_support.get("reason") or "").strip():
        errors.append("revision_support.reason is empty")
    final_intent = capsule.get("final_intent") if isinstance(capsule.get("final_intent"), dict) else {}
    if not str(final_intent.get("objective") or "").strip():
        errors.append("final_intent.objective is empty")
    if not final_intent.get("must_satisfy"):
        errors.append("final_intent.must_satisfy is empty")
    oracle = capsule.get("oracle") if isinstance(capsule.get("oracle"), dict) else {}
    for key in ["must_satisfy", "must_not_satisfy", "obsolete_intent_checks", "regression_checks", "forbidden_checks", "clarification_checks"]:
        if key not in oracle:
            errors.append(f"oracle.{key} is missing")
    return errors, warnings


def run_semantic_capsule(
    instance_dir: Path,
    *,
    model: str,
    client: DeepSeekClient | None,
    fallback_model: str | None = None,
    fallback_client: DeepSeekClient | None = None,
    llm_params: dict[str, Any] | None = None,
    json_repair_params: dict[str, Any] | None = None,
    force: bool = False,
    no_api: bool = False,
    dry_run: bool = False,
) -> SemanticCapsuleResult:
    if not force and (instance_dir / ".build" / "semantic_capsule.json").exists():
        capsule = load_semantic_capsule(instance_dir)
        sanitized = sanitize_semantic_capsule(capsule, source_text=_source_problem_statement(instance_dir))
        if isinstance(sanitized.data, dict):
            if sanitized.data != capsule:
                capsule = sanitized.data
                write_semantic_capsule(instance_dir, capsule)
        errors, warnings = validate_semantic_capsule(capsule)
        combined_errors = [*sanitized.hard_failures, *errors]
        return SemanticCapsuleResult(
            ok=not combined_errors,
            capsule=capsule,
            status="cached",
            warnings=[*sanitized.warnings, *warnings],
            errors=combined_errors,
        )

    if not no_api and not dry_run and (instance_dir / "patch_metadata.json").exists():
        update_patch_metadata(instance_dir, force=False)
    context = build_semantic_capsule_context(instance_dir)
    params = {"temperature": 0.0, "max_tokens": 5000, **(llm_params or {})}
    repair_params = json_repair_params or {}
    result = run_prompt_step(
        instance_dir=instance_dir,
        step_name="semantic_capsule",
        cache_prefix="v2_01_semantic_capsule",
        prompt_path=PROMPT_DIR / "semantic_capsule.md",
        context=context,
        client=client,
        model=model,
        compact_prompt_path=PROMPT_DIR / "semantic_capsule_compact.md",
        fallback_client=fallback_client,
        fallback_model=fallback_model,
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        thinking=params.get("thinking"),
        response_format=params.get("response_format"),
        repair_temperature=repair_params.get("temperature"),
        repair_max_tokens=repair_params.get("max_tokens"),
        repair_thinking=repair_params.get("thinking"),
        repair_response_format=repair_params.get("response_format"),
        force=force,
        no_api=no_api or dry_run,
    )
    api_calls_made = 0 if no_api or dry_run or result.status == "cached" else len(result.call_results or [])
    if result.status == "dry_run":
        return SemanticCapsuleResult(ok=True, status="dry_run", prompt_result=result)
    if result.status != "ok" or not isinstance(result.parsed, dict):
        return SemanticCapsuleResult(
            ok=False,
            status=result.status,
            api_call_made=api_calls_made > 0,
            api_calls_made=api_calls_made,
            errors=[result.error or "semantic_capsule parse failed"],
            prompt_result=result,
            error_type=result.error_type or "unknown",
            repair_success=bool(result.repair_success),
            retry_success=bool(result.retry_success),
            fallback_used=bool(result.fallback_used),
            model_used=result.model_used,
        )
    _archive_prompt_result(instance_dir, result, "semantic_capsule")
    sanitized: SanitizerResult = sanitize_semantic_capsule(
        result.parsed,
        source_text=str(context.get("problem_statement") or ""),
    )
    capsule = sanitized.data if isinstance(sanitized.data, dict) else {}
    errors, warnings = validate_semantic_capsule(capsule)
    errors = [*sanitized.hard_failures, *errors]
    warnings = [*sanitized.warnings, *warnings]
    write_semantic_capsule(instance_dir, capsule)
    if errors:
        (instance_dir / ".build" / "semantic_capsule.errors.json").write_text(
            json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return SemanticCapsuleResult(
        ok=not errors,
        capsule=capsule,
        status="ok" if not errors else "schema_invalid",
        api_call_made=api_calls_made > 0,
        api_calls_made=api_calls_made,
        warnings=warnings,
        errors=errors,
        prompt_result=result,
        error_type=None if not errors else "schema_invalid",
        repair_success=bool(result.repair_success),
        retry_success=bool(result.retry_success),
        fallback_used=bool(result.fallback_used),
        model_used=result.model_used,
    )
