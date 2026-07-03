from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cair_v2.construction.domains import domain_for_repo
from cair_v2.construction.localization_gold import canonical_file_path

DIFF_FILE_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$", re.MULTILINE)
HUNK_RE = re.compile(r"^@@ .*? @@\s*(.*)$")
DEF_OR_CLASS_RE = re.compile(r"\b(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)\b")
TEST_PATH_RE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]+\.py$|_test\.py$", re.IGNORECASE)
DOC_PATH_RE = re.compile(
    r"(^|/)(docs?|doc|changelog|news|release_notes?)/|"
    r"\.(md|rst|txt)$|(^|/)(README|CHANGELOG|LICENSE)",
    re.IGNORECASE,
)
DEPENDENCY_PATH_RE = re.compile(
    r"requirements|setup\.py|setup\.cfg|pyproject\.toml|tox\.ini|environment\.yml|package-lock|"
    r"deps|dependencies|vendor",
    re.IGNORECASE,
)

EXPECTED_RE = re.compile(r"\b(expected|should|instead|actual|expect|desired|rather than)\b", re.IGNORECASE)
REPRO_RE = re.compile(r"\b(reproduce|reproduction|steps|example|snippet|input|output)\b", re.IGNORECASE)
ERROR_RE = re.compile(r"\b(error|exception|traceback|fails?|failure|crash|warning)\b", re.IGNORECASE)
REVISION_RE = re.compile(
    r"\b(do not|don't|should not|without|preserve|keep|regression|backward|compatible|"
    r"instead of|not .* but|workaround|deprecated|no longer|avoid)\b",
    re.IGNORECASE,
)
DEPENDENCY_TEXT_RE = re.compile(r"\b(dependency|dependencies|version bump|upgrade|CVE|security|pin|vendored)\b", re.IGNORECASE)
FORMATTING_TEXT_RE = re.compile(r"\b(typo|formatting|whitespace|black|isort|lint only|cleanup|style only)\b", re.IGNORECASE)


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value)


def parse_list_like(value: Any) -> list[str]:
    text = as_text(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "[]"}:
        return []
    for parser in (json.loads,):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    if text.startswith("[") and text.endswith("]"):
        return [part.strip(" '\"") for part in text.strip("[]").split(",") if part.strip()]
    return [text]


def diff_files(diff_text: str) -> list[str]:
    files: list[str] = []
    for match in DIFF_FILE_RE.finditer(diff_text or ""):
        path = canonical_file_path(match.group(2))
        if path and path != "/dev/null" and path not in files:
            files.append(path)
    return files


def is_test_file(path: str) -> bool:
    return bool(TEST_PATH_RE.search(path))


def is_docs_file(path: str) -> bool:
    return bool(DOC_PATH_RE.search(path))


def is_python_source_file(path: str) -> bool:
    return path.endswith(".py") and not is_test_file(path) and not is_docs_file(path)


def patch_stats(diff_text: str) -> dict[str, int]:
    added = removed = hunks = 0
    for line in as_text(diff_text).splitlines():
        if line.startswith("@@"):
            hunks += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {"added_lines": added, "removed_lines": removed, "hunks": hunks}


def function_symbols_from_patch(diff_text: str, source_files: set[str]) -> list[str]:
    symbols: list[str] = []
    current_file: str | None = None
    current_symbol: str | None = None
    saw_change = False
    for line in as_text(diff_text).splitlines():
        file_match = DIFF_FILE_RE.match(line)
        if file_match:
            if current_file and saw_change:
                symbol = current_symbol or "<module>"
                full = f"{current_file}::{symbol}"
                if full not in symbols:
                    symbols.append(full)
            current_file = canonical_file_path(file_match.group(2))
            current_symbol = None
            saw_change = False
            continue
        if not current_file or current_file not in source_files:
            continue
        hunk_match = HUNK_RE.match(line)
        if hunk_match:
            if saw_change:
                symbol = current_symbol or "<module>"
                full = f"{current_file}::{symbol}"
                if full not in symbols:
                    symbols.append(full)
            context_match = DEF_OR_CLASS_RE.search(hunk_match.group(1))
            current_symbol = context_match.group(1) if context_match else None
            saw_change = False
            continue
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            saw_change = True
            inline_match = DEF_OR_CLASS_RE.search(line[1:])
            if inline_match:
                current_symbol = inline_match.group(1)
    if current_file and current_file in source_files and saw_change:
        symbol = current_symbol or "<module>"
        full = f"{current_file}::{symbol}"
        if full not in symbols:
            symbols.append(full)
    return sorted(symbols)


def patch_size_bucket(stats: dict[str, int]) -> str:
    total = stats["added_lines"] + stats["removed_lines"]
    if total <= 40:
        return "small"
    if total <= 160:
        return "medium"
    if total <= 500:
        return "large"
    return "very_large"


def classify_row(row: dict[str, Any]) -> dict[str, Any]:
    repo = as_text(row.get("repo"))
    domain = domain_for_repo(repo)
    problem = as_text(row.get("problem_statement"))
    hints = as_text(row.get("hints_text"))
    text = f"{problem}\n{hints}"
    patch = as_text(row.get("patch"))
    test_patch = as_text(row.get("test_patch"))
    files = diff_files(patch)
    test_patch_files = diff_files(test_patch)
    source_files = [path for path in files if is_python_source_file(path)]
    test_files = [path for path in files + test_patch_files if is_test_file(path)]
    docs_files = [path for path in files if is_docs_file(path)]
    stats = patch_stats(patch)
    source_gold_files = source_files or (test_files if domain == "testing_tooling" else [])
    function_symbols = function_symbols_from_patch(patch, set(source_gold_files))

    docs_only = bool(files) and len(docs_files) == len(files)
    tests_only = bool(files) and all(is_test_file(path) for path in files)
    dependency_like = bool(DEPENDENCY_TEXT_RE.search(text) or any(DEPENDENCY_PATH_RE.search(path) for path in files))
    formatting_like = bool(FORMATTING_TEXT_RE.search(text))
    has_patch = bool(patch.strip())
    has_test_patch = bool(test_patch.strip())
    has_file_gold = bool(source_gold_files)
    has_function_gold = bool(function_symbols)

    risk_reasons: list[str] = []
    risk = 0
    if not has_patch:
        risk += 50
        risk_reasons.append("missing_patch")
    if docs_only:
        risk += 45
        risk_reasons.append("docs_only")
    if tests_only and domain != "testing_tooling":
        risk += 35
        risk_reasons.append("tests_only")
    if not has_file_gold:
        risk += 45
        risk_reasons.append("missing_file_gold")
    if len(files) > 8:
        risk += 25
        risk_reasons.append("many_patch_files")
    elif len(files) > 3:
        risk += 10
        risk_reasons.append("multi_file_patch")
    if len(problem) < 200:
        risk += 20
        risk_reasons.append("short_problem_statement")
    if dependency_like:
        risk += 20
        risk_reasons.append("dependency_or_security_maintenance")
    if formatting_like:
        risk += 20
        risk_reasons.append("formatting_typo_cleanup")
    if not has_test_patch:
        risk += 10
        risk_reasons.append("missing_test_patch")
    risk = min(risk, 100)

    score = 0
    if 300 <= len(problem) <= 5000:
        score += 15
    elif len(problem) >= 200:
        score += 8
    if has_patch:
        score += 10
    if has_test_patch:
        score += 10
    if has_file_gold:
        score += 15
    if has_function_gold:
        score += 5
    if 1 <= len(files) <= 3:
        score += 15
    elif 4 <= len(files) <= 6:
        score += 6
    if EXPECTED_RE.search(text):
        score += 8
    if REPRO_RE.search(text):
        score += 8
    if ERROR_RE.search(text):
        score += 8
    if REVISION_RE.search(text):
        score += 12
    if hints.strip():
        score += 5
    if parse_list_like(row.get("FAIL_TO_PASS") or row.get("fail_to_pass")):
        score += 6
    if parse_list_like(row.get("PASS_TO_PASS") or row.get("pass_to_pass")):
        score += 3
    score -= risk // 4
    score = max(0, min(100, score))

    if docs_only or (tests_only and domain != "testing_tooling") or not has_patch or dependency_like or formatting_like:
        label = "reject"
    elif not has_file_gold or risk >= 45 or len(files) > 8 or len(problem) < 200:
        label = "manual_review"
    elif score >= 50:
        label = "candidate"
    else:
        label = "manual_review"

    reject_reason = ";".join(risk_reasons) if label == "reject" else ""
    manual_override = "REJECT_OR_DOWNRANK" if label == "reject" else ""
    tier = "reject" if label == "reject" else ("priority" if label == "candidate" else "manual_review")
    return {
        "domain": domain,
        "problem_len": len(problem),
        "has_patch": has_patch,
        "has_test_patch": has_test_patch,
        "patch_files_count": len(files),
        "source_files_count": len(source_files),
        "test_files_count": len(sorted(set(test_files))),
        "docs_files_count": len(docs_files),
        "source_files_touched": ";".join(sorted(set(source_gold_files))),
        "test_files_touched": ";".join(sorted(set(test_files))),
        "docs_only": docs_only,
        "tests_only": tests_only,
        "has_file_gold": has_file_gold,
        "has_function_gold": has_function_gold,
        "function_symbols_touched": ";".join(function_symbols),
        "patch_size_bucket": patch_size_bucket(stats),
        "patch_added_lines": stats["added_lines"],
        "patch_removed_lines": stats["removed_lines"],
        "patch_hunks": stats["hunks"],
        "likely_dependency_only": dependency_like,
        "likely_formatting_only": formatting_like,
        "quality_risk_score": risk,
        "quality_risk_reasons": ";".join(risk_reasons),
        "cair_rule_score": score,
        "rule_label": label,
        "rule_reject_reason": reject_reason,
        "manual_override_label": manual_override,
        "manual_review_tier": tier,
        "manual_recommended_categories": "",
    }


def build_candidate_pool(input_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(input_csv, dtype=str, keep_default_na=False)
    feature_rows = [classify_row(row) for row in df.to_dict(orient="records")]
    features = pd.DataFrame(feature_rows)
    pool = pd.concat([df, features], axis=1)
    pool = pool.sort_values(
        by=["rule_label", "cair_rule_score", "quality_risk_score", "patch_files_count"],
        ascending=[True, False, True, True],
        kind="mergesort",
    )
    label_order = {"candidate": 0, "manual_review": 1, "reject": 2}
    pool["_label_order"] = pool["rule_label"].map(label_order).fillna(9)
    pool = pool.sort_values(
        by=["_label_order", "cair_rule_score", "quality_risk_score", "patch_files_count", "instance_id"],
        ascending=[True, False, True, True, True],
        kind="mergesort",
    ).drop(columns=["_label_order"])
    return pool


def write_outputs(pool: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pool_path = output_dir / "cair_candidate_pool.csv"
    manual_path = output_dir / "manual_review_priority.csv"
    reject_path = output_dir / "reject_or_risky_candidates.csv"
    pool.to_csv(pool_path, index=False)
    manual = pool[pool["rule_label"] != "reject"].copy()
    manual.to_csv(manual_path, index=False)
    risky = pool[
        (pool["rule_label"] == "reject")
        | (pool["quality_risk_score"].astype(int) >= 45)
        | (pool["has_file_gold"] == False)  # noqa: E712
    ].copy()
    risky.to_csv(reject_path, index=False)
    print(f"Wrote {len(pool)} rows to {pool_path}")
    print(f"Wrote {len(manual)} rows to {manual_path}")
    print(f"Wrote {len(risky)} rows to {reject_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build CAIR v2 heuristic candidate pool from deduped SWE-bench issues.")
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "data/processed/all_swebench_issues_dedup.csv")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data/candidates")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_csv = resolve(args.input)
    if not input_csv.exists():
        raise FileNotFoundError(f"Missing deduped CSV: {input_csv}. Run scripts/dedup_swebench.py first.")
    pool = build_candidate_pool(input_csv)
    write_outputs(pool, resolve(args.output_dir))


if __name__ == "__main__":
    main()
