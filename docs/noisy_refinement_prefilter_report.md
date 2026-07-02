# Noisy Refinement Prefilter Report

Deterministic, cheap prefilter that prioritizes candidates likely to support a
source-grounded noisy-then-refined CAIR dialogue. It selects *which* candidates
to try; it does not loosen the quality gate and does not leak evaluator-side
metadata into any agent-facing dialogue. No per-instance_id special casing.

## 1. Rules

Text features (case-insensitive, generic regex over problem_statement + hints_text):
`has_observed_behavior`, `has_expected_behavior`, `has_reproduction`,
`has_component`, `has_refinement_clues`, `has_regression_or_boundary`, plus
`error_message` and `environment` signals feeding `estimated_fact_richness` (0-8).

Metadata features (evaluator-side, construction-only): `has_file_gold`,
`patch_files_count`, `docs_only`, `tests_only`, `likely_dependency_only`,
`likely_formatting_only`, `problem_len`.

Decision:
- `keep` (suitability=high): all core signals + refinement clue present,
  `fact_richness>=5`, `prefilter_score>=0.62`, 1-3 patch files, file gold available.
- `deprioritize` (medium): partial signals, `score>=0.42`, `fact_richness>=3`.
- `reject` (low): hard exclusions (docs/tests/dependency/formatting only, no file
  gold, no expected behavior, no refinement space, issue too short) or weak signals.

## 2. Inputs / outputs

- Input candidates: 16778
- Input file: `data/candidates/manual_review_priority.csv`
- Output candidate pool (`keep` + `deprioritize`, kept in input order): `data/candidates/noisy_refinement_prefilter_candidates.csv`

These large candidate files are not tracked in ordinary git; regenerate them
locally or share a pinned snapshot through external storage/Git LFS.

## 3. Decision counts

- keep: 6288 (37.5%)
- deprioritize: 2442 (14.6%)
- reject: 8048 (48.0%)
- retained pool (keep + deprioritize): 8730 (52.0%)

## 4. Suitability distribution

- high: 6288
- medium: 2442
- low: 8048

## 5. prefilter_score buckets

| score>= | count |
|---|---:|
| 0.0 | 572 |
| 0.1 | 255 |
| 0.2 | 523 |
| 0.3 | 982 |
| 0.4 | 939 |
| 0.5 | 1713 |
| 0.6 | 1662 |
| 0.7 | 2343 |
| 0.8 | 2423 |
| 0.9 | 2661 |
| 1.0 | 2705 |

## 6. Domain distribution (kept / total)

| domain | kept | total |
|---|---:|---:|
| other | 7517 | 14840 |
| web_framework | 426 | 732 |
| symbolic_math | 210 | 374 |
| ml_library | 150 | 205 |
| scientific_computing | 123 | 171 |
| visualization | 122 | 155 |
| documentation_tooling | 95 | 155 |
| testing_tooling | 49 | 89 |
| static_analysis | 38 | 57 |

## 7. Reject reason distribution

| reason | count |
|---|---:|
| no_expected_behavior | 5236 |
| no_source_grounded_refinement_space | 4910 |
| issue_too_short | 1045 |
| no_file_gold | 344 |
| insufficient_noisy_refinement_signals | 17 |

## 8. Expected accepted-rate improvement

The prefilter does not change the gate, so accepted rate is only estimated by
raising the density of candidates with observed+expected+reproduction/component+
refinement signals. The current prefilter100 diagnosis measured a 57% accepted
rate on the selected candidates.

## 9. CAIR-Core reserve estimate

- `keep` pool available for CAIR-Core-500 / CAIR-Core-1000 seeding: 6288
- `keep` + `deprioritize` fallback reserve: 8730
- Sizing note: at a conservative post-gate accepted fraction, a `keep` pool
  of 6288 supports CAIR-Core-500 if the measured accepted rate is >= 8%, and CAIR-Core-1000 if >= 16%.
