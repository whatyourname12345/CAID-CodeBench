from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from pathlib import Path

from cair_v2.construction.domains import domain_for_repo
from cair_v2.construction.instance_io import read_json
from cair_v2.construction.patch_summarizer import DEFAULT_CANDIDATE_CSV, load_candidate_record, split_paths


csv.field_size_limit(sys.maxsize)

DIFF_FILE_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$")
HUNK_RE = re.compile(r"^@@ .*? @@\s*(.*)$")
DEF_RE = re.compile(r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b")

DOC_OR_META_RE = re.compile(
    r"(^|/)(docs?|doc|changelog|news|release_notes?)/|"
    r"\.(md|rst|txt|toml|ini|cfg|yml|yaml|json)$|"
    r"(^|/)(README|CHANGELOG|LICENSE)",
    re.IGNORECASE,
)
TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]+\.py$|_test\.py$", re.IGNORECASE)


@dataclass
class LocalizationGold:
    files: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = "unknown"

    @property
    def function_gold_available(self) -> bool:
        return bool(self.functions)


def canonical_file_path(path: Any) -> str:
    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    return str(PurePosixPath(text)) if text else ""


def is_source_candidate(path: str, *, domain: str) -> bool:
    clean = canonical_file_path(path)
    if not clean or clean == "/dev/null":
        return False
    if TEST_PATH_RE.search(clean) and domain != "testing_tooling":
        return False
    if DOC_OR_META_RE.search(clean):
        return False
    return clean.endswith(".py")


def iter_patch_files(patch: str) -> list[str]:
    files: list[str] = []
    for line in str(patch or "").splitlines():
        match = DIFF_FILE_RE.match(line)
        if match:
            path = canonical_file_path(match.group(2))
            if path and path not in files:
                files.append(path)
    return files


def extract_symbol_from_context(context: str) -> str | None:
    text = str(context or "").strip()
    if not text:
        return None
    def_match = DEF_RE.search(text)
    if def_match:
        return def_match.group(1)
    class_match = CLASS_RE.search(text)
    if class_match:
        return class_match.group(1)
    return None


def symbols_from_patch_hunks(patch: str, gold_files: list[str]) -> tuple[list[str], list[str]]:
    functions: list[str] = []
    warnings: list[str] = []
    current_file: str | None = None
    in_file = False
    current_symbol: str | None = None
    saw_changed_line = False
    gold_file_set = set(gold_files)
    for line in str(patch or "").splitlines():
        file_match = DIFF_FILE_RE.match(line)
        if file_match:
            if current_file and saw_changed_line:
                symbol = current_symbol or "<module>"
                full = f"{current_file}::{symbol}"
                if full not in functions:
                    functions.append(full)
            current_file = canonical_file_path(file_match.group(2))
            in_file = current_file in gold_file_set
            current_symbol = None
            saw_changed_line = False
            continue
        if not in_file or not current_file:
            continue
        hunk_match = HUNK_RE.match(line)
        if hunk_match:
            if saw_changed_line:
                symbol = current_symbol or "<module>"
                full = f"{current_file}::{symbol}"
                if full not in functions:
                    functions.append(full)
            current_symbol = extract_symbol_from_context(hunk_match.group(1))
            saw_changed_line = False
            continue
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            saw_changed_line = True
            line_symbol = extract_symbol_from_context(line[1:])
            if line_symbol:
                current_symbol = line_symbol
    if current_file and in_file and saw_changed_line:
        symbol = current_symbol or "<module>"
        full = f"{current_file}::{symbol}"
        if full not in functions:
            functions.append(full)
    if not functions and gold_files:
        warnings.append("Function gold could not be extracted from diff hunk headers; using File Hit@k only.")
    return sorted(functions), warnings


def extract_gold_from_patch(patch: str, *, repo: str) -> LocalizationGold:
    domain = domain_for_repo(repo)
    files = [path for path in iter_patch_files(patch) if is_source_candidate(path, domain=domain)]
    files = sorted(dict.fromkeys(files))
    functions, warnings = symbols_from_patch_hunks(patch, files)
    return LocalizationGold(files=files, functions=functions, warnings=warnings, source="reference_patch")


def extract_gold_from_patch_metadata(metadata: dict[str, Any], *, repo: str) -> LocalizationGold:
    domain = domain_for_repo(repo)
    raw_paths = metadata.get("source_files_touched")
    if isinstance(raw_paths, dict):
        raw_paths = raw_paths.get("touched_source_files")
    files = [canonical_file_path(path) for path in split_paths(raw_paths)]
    files = sorted(dict.fromkeys(path for path in files if is_source_candidate(path, domain=domain)))
    warnings = ["File gold extracted from patch_metadata fallback; function gold unavailable."]
    return LocalizationGold(files=files, functions=[], warnings=warnings, source="patch_metadata")


def extract_localization_gold(instance_dir: Path, candidate_csv: Path = DEFAULT_CANDIDATE_CSV) -> LocalizationGold:
    source_record = read_json(instance_dir / "source_record.json")
    instance_id = str(source_record.get("instance_id") or "")
    repo = str(source_record.get("repo") or "")
    private_record_path = instance_dir / ".build" / "candidate_record_private.json"
    if private_record_path.exists():
        record = read_json(private_record_path)
    else:
        record = load_candidate_record(instance_id, candidate_csv)
    patch = str(record.get("patch") or "") if record else ""
    if patch.strip():
        gold = extract_gold_from_patch(patch, repo=repo)
        if gold.files:
            return gold
    metadata_path = instance_dir / "patch_metadata.json"
    metadata = read_json(metadata_path) if metadata_path.exists() else {}
    gold = extract_gold_from_patch_metadata(metadata, repo=repo)
    if not gold.files:
        gold.warnings.append("No source file gold could be extracted for localization checkpoint.")
    return gold
