"""Deterministic, cheap prefilter for noisy-refinement CAIR-Core candidates.

Purpose
-------
Raise the downstream *accepted* rate for the `v2_noisy_refinement` pipeline by
prioritizing candidates whose issue text plausibly supports a source-grounded,
multi-turn noisy-then-refined dialogue. This is a construction-side selection
aid ONLY.

Guarantees / non-goals
----------------------
- Does NOT loosen the quality gate. It changes *which* candidates are tried,
  never whether an output is accepted.
- Uses evaluator-side metadata (gold files/functions, patch size) for
  construction selection, but never emits those signals into any agent-facing
  dialogue. The output CSV is a construction input, not an agent view.
- No per-instance_id special casing. All decisions come from generic text and
  metadata features.

Usage
-----
    .venv/bin/python scripts/build_noisy_refinement_prefilter.py \
        --input data/candidates/manual_review_priority.csv \
        --output data/candidates/noisy_refinement_prefilter_candidates.csv \
        --report docs/noisy_refinement_prefilter_report.md
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


# --- Text feature detectors (generic, case-insensitive) ----------------------

OBSERVED_RE = re.compile(
    r"\b(actual(ly)?|observ|current(ly)?|instead of|returns?|produces?|"
    r"fails?|failing|crash|error|exception|traceback|raises?|wrong|incorrect|"
    r"unexpected|broken|does ?n'?t|doesn't|not work)\b",
    re.IGNORECASE,
)
EXPECTED_RE = re.compile(
    r"\b(expect(ed|s)?|should|shouldn'?t|desired|supposed to|ought to|"
    r"would (expect|like|want)|want(ed)? to|intended|the correct)\b",
    re.IGNORECASE,
)
REPRODUCTION_RE = re.compile(
    r"(```|>>>|traceback \(most recent call last\)|steps to reproduce|"
    r"to reproduce|minimal (example|reproduc)|reproduc(e|ible|tion)|"
    r"\bexample\b|\binput\b.*\boutput\b|code ?snippet)",
    re.IGNORECASE,
)
COMPONENT_RE = re.compile(
    r"(\b[\w/]+\.py\b|::|`[A-Za-z_][A-Za-z0-9_\.]*`|\b(class|def|function|method|"
    r"module|attribute|parameter|argument)\b)",
    re.IGNORECASE,
)
REFINEMENT_RE = re.compile(
    r"\b(actually|wait|hmm|not sure|maybe|perhaps|i think|correction|edit:|"
    r"update:|however|on second thought|turns out|misleading|confusing|"
    r"ambiguous|or should|alternatively|i was wrong|scratch that|rethink|"
    r"reconsider|clarif|second look|to be clear|correct me)\b",
    re.IGNORECASE,
)
REGRESSION_RE = re.compile(
    r"\b(regression|backward[- ]?compat|backwards[- ]?compat|compatibilit|"
    r"edge case|corner case|boundary|only if|unless|except when|empty|"
    r"\bnone\b|\bnull\b|\bzero\b|negative|unicode|nested|off[- ]by[- ]one)\b",
    re.IGNORECASE,
)
ERROR_MESSAGE_RE = re.compile(
    r"(Error|Exception|Traceback|Warning|assert|raise[sd]?)\b", re.IGNORECASE
)
ENVIRONMENT_RE = re.compile(
    r"\b(version|python 3|python2|numpy|pandas|django \d|installed|environment|"
    r"platform|os\b|windows|linux|macos)\b",
    re.IGNORECASE,
)


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def compute_features(row: dict[str, Any]) -> dict[str, Any]:
    problem = str(row.get("problem_statement") or "")
    hints = str(row.get("hints_text") or "")
    text = f"{problem}\n{hints}"
    problem_len = _int(row.get("problem_len"), len(problem))

    has_observed = bool(OBSERVED_RE.search(text))
    has_expected = bool(EXPECTED_RE.search(text))
    has_reproduction = bool(REPRODUCTION_RE.search(text))
    has_component = bool(COMPONENT_RE.search(text)) or _bool(row.get("has_function_gold"))
    has_refinement = bool(REFINEMENT_RE.search(text)) or len(hints.strip()) > 40
    has_regression = bool(REGRESSION_RE.search(text))
    has_error = bool(ERROR_MESSAGE_RE.search(text))
    has_environment = bool(ENVIRONMENT_RE.search(text))

    fact_signals = [
        has_observed,
        has_expected,
        has_reproduction,
        has_component,
        has_refinement,
        has_regression,
        has_error,
        has_environment,
    ]
    fact_richness = sum(1 for signal in fact_signals if signal)

    return {
        "problem_len": problem_len,
        "has_observed_behavior": has_observed,
        "has_expected_behavior": has_expected,
        "has_reproduction": has_reproduction,
        "has_component": has_component,
        "has_refinement_clues": has_refinement,
        "has_regression_or_boundary": has_regression,
        "estimated_fact_richness": fact_richness,
    }


def score_and_decide(row: dict[str, Any], features: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    problem_len = features["problem_len"]
    patch_files = _int(row.get("patch_files_count"), 0)
    has_file_gold = _bool(row.get("has_file_gold"))
    docs_only = _bool(row.get("docs_only"))
    tests_only = _bool(row.get("tests_only"))
    dependency_only = _bool(row.get("likely_dependency_only"))
    formatting_only = _bool(row.get("likely_formatting_only"))

    # Weighted, deterministic prefilter score in [0, 1].
    weights = {
        "has_observed_behavior": 0.14,
        "has_expected_behavior": 0.18,
        "has_reproduction": 0.16,
        "has_component": 0.12,
        "has_refinement_clues": 0.20,
        "has_regression_or_boundary": 0.10,
    }
    score = sum(weight for key, weight in weights.items() if features[key])
    if problem_len >= 800:
        score += 0.06
        reasons.append("long_problem_statement")
    elif problem_len < 300:
        score -= 0.10
        reasons.append("short_problem_statement")
    if 1 <= patch_files <= 3:
        score += 0.04
        reasons.append("focused_patch_files")
    elif patch_files > 5:
        score -= 0.08
        reasons.append("patch_touches_many_files")
    if has_file_gold:
        reasons.append("file_gold_available")
    score = max(0.0, min(1.0, score))

    # Hard exclusions that cannot support noisy refinement.
    hard_reject = []
    if docs_only:
        hard_reject.append("docs_only")
    if tests_only:
        hard_reject.append("tests_only")
    if dependency_only:
        hard_reject.append("dependency_only")
    if formatting_only:
        hard_reject.append("formatting_only")
    if not has_file_gold:
        hard_reject.append("no_file_gold")
    if not features["has_expected_behavior"]:
        hard_reject.append("no_expected_behavior")
    if problem_len < 200:
        hard_reject.append("issue_too_short")
    if not features["has_refinement_clues"]:
        hard_reject.append("no_source_grounded_refinement_space")

    core_ok = (
        features["has_observed_behavior"]
        and features["has_expected_behavior"]
        and (features["has_reproduction"] or features["has_component"])
        and features["has_refinement_clues"]
        and 1 <= patch_files <= 3
        and has_file_gold
    )

    if hard_reject:
        decision = "reject"
        suitability = "low"
        reasons.extend(f"reject:{item}" for item in hard_reject)
    elif core_ok and features["estimated_fact_richness"] >= 5 and score >= 0.62:
        decision = "keep"
        suitability = "high"
        reasons.append("keep:core_signals_and_refinement_present")
    elif score >= 0.42 and features["estimated_fact_richness"] >= 3:
        decision = "deprioritize"
        suitability = "medium"
        reasons.append("deprioritize:partial_signals")
    else:
        decision = "reject"
        suitability = "low"
        reasons.append("reject:insufficient_noisy_refinement_signals")

    return {
        "prefilter_score": round(score, 3),
        "estimated_noisy_refinement_suitability": suitability,
        "prefilter_decision": decision,
        "prefilter_reasons": ";".join(reasons),
    }


PREFILTER_FIELDS = [
    "prefilter_score",
    "has_observed_behavior",
    "has_expected_behavior",
    "has_reproduction",
    "has_component",
    "has_refinement_clues",
    "has_regression_or_boundary",
    "estimated_fact_richness",
    "estimated_noisy_refinement_suitability",
    "prefilter_decision",
    "prefilter_reasons",
]


def run(input_path: Path, output_path: Path, report_path: Path) -> dict[str, Any]:
    total = 0
    decision_counts: Counter[str] = Counter()
    suitability_counts: Counter[str] = Counter()
    domain_kept: Counter[str] = Counter()
    domain_total: Counter[str] = Counter()
    score_buckets: Counter[str] = Counter()
    reject_reason_counts: Counter[str] = Counter()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open(newline="", encoding="utf-8") as fin:
        reader = csv.DictReader(fin)
        base_fields = list(reader.fieldnames or [])
        out_fields = base_fields + [f for f in PREFILTER_FIELDS if f not in base_fields]
        with output_path.open("w", newline="", encoding="utf-8") as fout:
            writer = csv.DictWriter(fout, fieldnames=out_fields, extrasaction="ignore")
            writer.writeheader()
            for row in reader:
                total += 1
                features = compute_features(row)
                verdict = score_and_decide(row, features)
                decision = verdict["prefilter_decision"]
                decision_counts[decision] += 1
                suitability_counts[verdict["estimated_noisy_refinement_suitability"]] += 1
                domain = str(row.get("domain") or "unknown")
                domain_total[domain] += 1
                bucket = f"{int(verdict['prefilter_score'] * 10) / 10:.1f}"
                score_buckets[bucket] += 1
                if decision == "reject":
                    for token in verdict["prefilter_reasons"].split(";"):
                        if token.startswith("reject:"):
                            reject_reason_counts[token[len("reject:"):]] += 1
                if decision in {"keep", "deprioritize"}:
                    domain_kept[domain] += 1
                    merged = dict(row)
                    merged.update({k: features[k] for k in features if k in PREFILTER_FIELDS})
                    merged.update(verdict)
                    writer.writerow(merged)

    stats = {
        "total": total,
        "decision_counts": dict(decision_counts),
        "suitability_counts": dict(suitability_counts),
        "domain_total": dict(domain_total),
        "domain_kept": dict(domain_kept),
        "score_buckets": dict(sorted(score_buckets.items())),
        "reject_reason_counts": dict(reject_reason_counts.most_common()),
    }
    _write_report(report_path, input_path, output_path, stats)
    return stats


def _write_report(report_path: Path, input_path: Path, output_path: Path, stats: dict[str, Any]) -> None:
    keep = stats["decision_counts"].get("keep", 0)
    deprioritize = stats["decision_counts"].get("deprioritize", 0)
    reject = stats["decision_counts"].get("reject", 0)
    total = stats["total"] or 1
    pool = keep + deprioritize

    lines = [
        "# Noisy Refinement Prefilter Report",
        "",
        "Deterministic, cheap prefilter that prioritizes candidates likely to support a",
        "source-grounded noisy-then-refined CAIR dialogue. It selects *which* candidates",
        "to try; it does not loosen the quality gate and does not leak evaluator-side",
        "metadata into any agent-facing dialogue. No per-instance_id special casing.",
        "",
        "## 1. Rules",
        "",
        "Text features (case-insensitive, generic regex over problem_statement + hints_text):",
        "`has_observed_behavior`, `has_expected_behavior`, `has_reproduction`,",
        "`has_component`, `has_refinement_clues`, `has_regression_or_boundary`, plus",
        "`error_message` and `environment` signals feeding `estimated_fact_richness` (0-8).",
        "",
        "Metadata features (evaluator-side, construction-only): `has_file_gold`,",
        "`patch_files_count`, `docs_only`, `tests_only`, `likely_dependency_only`,",
        "`likely_formatting_only`, `problem_len`.",
        "",
        "Decision:",
        "- `keep` (suitability=high): all core signals + refinement clue present,",
        "  `fact_richness>=5`, `prefilter_score>=0.62`, 1-3 patch files, file gold available.",
        "- `deprioritize` (medium): partial signals, `score>=0.42`, `fact_richness>=3`.",
        "- `reject` (low): hard exclusions (docs/tests/dependency/formatting only, no file",
        "  gold, no expected behavior, no refinement space, issue too short) or weak signals.",
        "",
        "## 2. Inputs / outputs",
        "",
        f"- Input candidates: {stats['total']}",
        f"- Input file: `{input_path}`",
        f"- Output candidate pool (`keep` + `deprioritize`, kept in input order): `{output_path}`",
        "",
        "## 3. Decision counts",
        "",
        f"- keep: {keep} ({keep / total:.1%})",
        f"- deprioritize: {deprioritize} ({deprioritize / total:.1%})",
        f"- reject: {reject} ({reject / total:.1%})",
        f"- retained pool (keep + deprioritize): {pool} ({pool / total:.1%})",
        "",
        "## 4. Suitability distribution",
        "",
    ]
    for key in ("high", "medium", "low"):
        lines.append(f"- {key}: {stats['suitability_counts'].get(key, 0)}")
    lines += ["", "## 5. prefilter_score buckets", "", "| score>= | count |", "|---|---:|"]
    for bucket, count in stats["score_buckets"].items():
        lines.append(f"| {bucket} | {count} |")
    lines += ["", "## 6. Domain distribution (kept / total)", "", "| domain | kept | total |", "|---|---:|---:|"]
    for domain in sorted(stats["domain_total"], key=lambda d: -stats["domain_total"][d]):
        lines.append(f"| {domain} | {stats['domain_kept'].get(domain, 0)} | {stats['domain_total'][domain]} |")
    lines += ["", "## 7. Reject reason distribution", "", "| reason | count |", "|---|---:|"]
    for reason, count in stats["reject_reason_counts"].items():
        lines.append(f"| {reason} | {count} |")

    keep_rate_est = "unknown"
    if pool:
        # Baseline statusfinal accepted rate on unfiltered diverse20 was ~40% (8/20).
        # `keep` candidates carry all core noisy-refinement signals, so a moderate
        # lift is plausible; we do NOT assert a number without a measured limit=100.
        keep_rate_est = "to be measured on limit=100 (raw100 vs prefiltered100)"

    if keep:
        sizing_note = (
            f"  of {keep} supports CAIR-Core-500 if the measured accepted rate is "
            f">= {500 / keep:.0%}, and CAIR-Core-1000 if >= {1000 / keep:.0%}."
        )
    else:
        sizing_note = "  of 0 is insufficient; broaden the input candidate set."

    lines += [
        "",
        "## 8. Expected accepted-rate improvement",
        "",
        "The prefilter does not change the gate, so accepted rate is only estimated by",
        "raising the density of candidates with observed+expected+reproduction/component+",
        "refinement signals. Actual lift must be measured by comparing raw100 vs",
        f"prefiltered100. Current estimate: {keep_rate_est}.",
        "",
        "## 9. CAIR-Core reserve estimate",
        "",
        f"- `keep` pool available for CAIR-Core-500 / CAIR-Core-1000 seeding: {keep}",
        f"- `keep` + `deprioritize` fallback reserve: {pool}",
        "- Sizing note: at a conservative post-gate accepted fraction, a `keep` pool",
        sizing_note,
        "",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deterministic noisy-refinement candidate prefilter.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def main() -> None:
    args = parse_args()
    stats = run(_resolve(args.input), _resolve(args.output), _resolve(args.report))
    print(f"total: {stats['total']}")
    for decision in ("keep", "deprioritize", "reject"):
        print(f"{decision}: {stats['decision_counts'].get(decision, 0)}")
    print(f"output: {args.output}")
    print(f"report: {args.report}")


if __name__ == "__main__":
    main()
