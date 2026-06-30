from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


YAML_SECTION_FILES = {
    "atomic_issue_units": "atomic_issue_units.yaml",
    "final_intent": "final_intent.yaml",
    "hidden_intent_state": "hidden_intent_state.yaml",
    "operation_trajectory": "operation_trajectory.yaml",
    "evaluation_modes": "evaluation_modes.yaml",
    "intent_oracle": "intent_oracle.yaml",
    "qa_checklist": "qa_checklist.yaml",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_section(instance_dir: Path, section: str) -> dict[str, Any]:
    if section == "source_record":
        return read_json(instance_dir / "source_record.json")
    if section == "patch_metadata":
        return read_json(instance_dir / "patch_metadata.json")
    filename = YAML_SECTION_FILES[section]
    return read_yaml(instance_dir / filename)


def write_section(instance_dir: Path, section: str, data: dict[str, Any]) -> None:
    if section == "source_record":
        write_json(instance_dir / "source_record.json", data)
        return
    if section == "patch_metadata":
        write_json(instance_dir / "patch_metadata.json", data)
        return
    filename = YAML_SECTION_FILES[section]
    write_yaml(instance_dir / filename, data)


def load_instance_parts(instance_dir: Path) -> dict[str, Any]:
    parts: dict[str, Any] = {}
    for section in ["source_record", "patch_metadata", *YAML_SECTION_FILES.keys()]:
        path = instance_dir / ("source_record.json" if section == "source_record" else "patch_metadata.json" if section == "patch_metadata" else YAML_SECTION_FILES[section])
        if path.exists():
            parts[section] = load_section(instance_dir, section)
    return parts


def contains_todo(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper().startswith("TODO") or "TODO:" in value
    if isinstance(value, list):
        return any(contains_todo(item) for item in value)
    if isinstance(value, dict):
        return any(contains_todo(item) for item in value.values())
    return False


def unwrap_section(data: dict[str, Any], section_key: str) -> Any:
    if section_key in data:
        return data[section_key]
    return data


def normalize_section_output(data: Any, section_key: str) -> dict[str, Any]:
    if isinstance(data, dict) and section_key in data:
        return data
    return {section_key: data}

