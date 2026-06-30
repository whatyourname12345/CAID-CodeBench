from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def int_value(value: Any, default: int = 0) -> int:
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except Exception:
        return default


def float_value(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except Exception:
        return default


def sort_priority(df: pd.DataFrame) -> pd.DataFrame:
    label_order = {"candidate": 0, "manual_review": 1, "reject": 9}
    source_order = {"verified": 0, "lite": 1, "full": 2}
    work = df.copy()
    rule_label = work["rule_label"] if "rule_label" in work.columns else pd.Series([""] * len(work), index=work.index)
    source_name = work["source_name"] if "source_name" in work.columns else pd.Series([""] * len(work), index=work.index)
    risk = work["quality_risk_score"] if "quality_risk_score" in work.columns else pd.Series([0] * len(work), index=work.index)
    score = work["cair_rule_score"] if "cair_rule_score" in work.columns else pd.Series([0] * len(work), index=work.index)
    patch_files = work["patch_files_count"] if "patch_files_count" in work.columns else pd.Series([999] * len(work), index=work.index)
    work["_label_order"] = rule_label.map(label_order).fillna(5)
    work["_source_order"] = source_name.map(source_order).fillna(5)
    work["_risk"] = risk.map(float_value)
    work["_score"] = score.map(float_value)
    work["_patch_files"] = patch_files.map(lambda value: int_value(value, 999))
    return work.sort_values(
        by=["_label_order", "_risk", "_score", "_source_order", "_patch_files", "instance_id"],
        ascending=[True, True, False, True, True, True],
        kind="mergesort",
    ).drop(columns=["_label_order", "_source_order", "_risk", "_score", "_patch_files"])


def choose_diverse(
    df: pd.DataFrame,
    *,
    target_size: int,
    max_per_repo: int,
    max_domain_ratio: float,
) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    domain_cap = max(1, math.floor(target_size * max_domain_ratio))
    selected: list[pd.Series] = []
    repo_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}

    def can_take(row: pd.Series, *, enforce_domain: bool) -> bool:
        repo = str(row.get("repo") or "")
        domain = str(row.get("domain") or "other")
        if repo_counts.get(repo, 0) >= max_per_repo:
            return False
        if enforce_domain and domain_counts.get(domain, 0) >= domain_cap:
            return False
        return True

    for _, row in sort_priority(df).iterrows():
        if len(selected) >= target_size:
            break
        if can_take(row, enforce_domain=True):
            selected.append(row)
            repo = str(row.get("repo") or "")
            domain = str(row.get("domain") or "other")
            repo_counts[repo] = repo_counts.get(repo, 0) + 1
            domain_counts[domain] = domain_counts.get(domain, 0) + 1

    if len(selected) < target_size:
        warnings.append("Strict domain cap did not fill target size; relaxing domain cap while keeping repo cap.")
        selected_ids = {str(row.get("instance_id") or "") for row in selected}
        for _, row in sort_priority(df).iterrows():
            if len(selected) >= target_size:
                break
            if str(row.get("instance_id") or "") in selected_ids:
                continue
            if can_take(row, enforce_domain=False):
                selected.append(row)
                selected_ids.add(str(row.get("instance_id") or ""))
                repo = str(row.get("repo") or "")
                domain = str(row.get("domain") or "other")
                repo_counts[repo] = repo_counts.get(repo, 0) + 1
                domain_counts[domain] = domain_counts.get(domain, 0) + 1

    return pd.DataFrame(selected), warnings


def distribution_lines(title: str, values: pd.Series) -> list[str]:
    lines = [f"## {title}", ""]
    counts = values.fillna("unknown").astype(str).value_counts()
    if counts.empty:
        lines.append("_No rows._")
    else:
        for key, count in counts.items():
            lines.append(f"- {key}: {count}")
    lines.append("")
    return lines


def write_report(
    *,
    path: Path,
    input_df: pd.DataFrame,
    selected: pd.DataFrame,
    reject_count: int,
    warnings: list[str],
    target_size: int,
    max_per_repo: int,
    max_domain_ratio: float,
) -> None:
    total = len(selected)
    web_count = int((selected.get("domain", pd.Series(dtype=str)) == "web_framework").sum()) if total else 0
    web_ratio = web_count / total if total else 0.0
    lines = [
        "# Diverse Seed Candidate Report",
        "",
        f"- Input rows: {len(input_df)}",
        f"- Selected rows: {len(selected)}",
        f"- Reject/risky rows excluded upstream: {reject_count}",
        f"- Target size: {target_size}",
        f"- Max per repo: {max_per_repo}",
        f"- Max domain ratio: {max_domain_ratio}",
        f"- Django/Web selected ratio: {web_count}/{total} ({web_ratio:.1%})",
        "",
    ]
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.extend(distribution_lines("Repo Distribution", selected.get("repo", pd.Series(dtype=str))))
    lines.extend(distribution_lines("Domain Distribution", selected.get("domain", pd.Series(dtype=str))))
    lines.extend(distribution_lines("Manual Label Distribution", selected.get("manual_override_label", pd.Series(dtype=str))))
    lines.extend(distribution_lines("Rule Label Distribution", selected.get("rule_label", pd.Series(dtype=str))))
    lines.extend(["## Top Candidates", ""])
    for idx, row in selected.reset_index(drop=True).iterrows():
        lines.append(
            f"{idx + 1}. `{row.get('instance_id')}` | repo={row.get('repo')} | "
            f"domain={row.get('domain')} | score={row.get('cair_rule_score')} | risk={row.get('quality_risk_score')}"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `django__django-14011` can remain a construction golden example, but the selected seed list is repo/domain-stratified.",
            "- Use this CSV as input for CAIR v2 smoke batches before any larger conversion.",
            "- Do not commit the full `data/candidates/` outputs by default; commit only the small sample if needed.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a repo/domain-diverse CAIR v2 seed candidate CSV.")
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "data/candidates/manual_review_priority.csv")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data/candidates/diverse_seed_candidates.csv")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "data/candidates/diverse_seed_report.md")
    parser.add_argument("--sample-output", type=Path, default=PROJECT_ROOT / "datasets/candidates/diverse_seed_candidates_sample.csv")
    parser.add_argument("--target-size", type=int, default=20)
    parser.add_argument("--max-per-repo", type=int, default=2)
    parser.add_argument("--max-domain-ratio", type=float, default=0.3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = resolve(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Missing manual priority CSV: {input_path}. Run scripts/build_candidate_pool.py first.")
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    before = len(df)
    if "rule_label" in df.columns:
        df = df[df["rule_label"] != "reject"].copy()
    if "manual_override_label" in df.columns:
        df = df[df["manual_override_label"] != "REJECT_OR_DOWNRANK"].copy()
    selected, warnings = choose_diverse(
        df,
        target_size=args.target_size,
        max_per_repo=args.max_per_repo,
        max_domain_ratio=args.max_domain_ratio,
    )
    output = resolve(args.output)
    report = resolve(args.report)
    sample_output = resolve(args.sample_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output, index=False)
    sample_output.parent.mkdir(parents=True, exist_ok=True)
    selected.head(5).to_csv(sample_output, index=False)
    write_report(
        path=report,
        input_df=df,
        selected=selected,
        reject_count=before - len(df),
        warnings=warnings,
        target_size=args.target_size,
        max_per_repo=args.max_per_repo,
        max_domain_ratio=args.max_domain_ratio,
    )
    print(f"Wrote {len(selected)} rows to {output}")
    print(f"Wrote sample {min(5, len(selected))} rows to {sample_output}")
    print(f"Wrote {report}")


if __name__ == "__main__":
    main()
