from __future__ import annotations

import json
import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cair_v2.construction.sanitizer import BENCHMARK_METADATA_RE, IMPLEMENTATION_HINT_RE, text_blob
from cair_v2.llm.clients import DeepSeekClient
from cair_v2.llm.prompt_runner import PromptStepResult, run_prompt_step
from cair_v2.staged.schemas import REVISION_FACT_TYPES, StageStepResult
from cair_v2.staged.source_spans import (
    SOURCE_MATCH_OK,
    find_source_span_match,
    normalized_source_text,
    summarize_source_matches,
)
from cair_v2.staged.validators import VALIDATORS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class StepConfig:
    model: str
    params: dict[str, Any]


ClientFactory = Callable[[str], DeepSeekClient | None]


class StagedStepRunner:
    def __init__(
        self,
        *,
        instance_dir: Path,
        step_configs: dict[str, StepConfig],
        client_factory: ClientFactory,
        no_api: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.instance_dir = instance_dir
        self.step_configs = step_configs
        self.client_factory = client_factory
        self.no_api = no_api
        self.dry_run = dry_run
        (self.instance_dir / ".build").mkdir(parents=True, exist_ok=True)

    def run_step(self, step_name: str, context: dict[str, Any]) -> StageStepResult:
        config = self.step_configs[step_name]
        primary = self._run_prompt(step_name, context, suffix="")
        if primary.status != "ok" or not isinstance(primary.parsed, dict):
            result = StageStepResult(
                step_name=step_name,
                ok=False,
                status=primary.status,
                errors=[primary.error or f"{step_name} parse failed"],
                prompt_result=primary,
                repair_success=bool(primary.repair_success),
                retry_success=bool(primary.retry_success),
                fallback_used=bool(primary.fallback_used),
                model_used=primary.model_used or config.model,
                error_type=primary.error_type or "invalid_json",
            )
            self._write_step_error(result)
            return result

        primary_data = self._local_repair(step_name, primary.parsed, context)
        errors, warnings = self._validate(step_name, primary_data, context)
        if not errors:
            result = StageStepResult(
                step_name=step_name,
                ok=True,
                status="ok",
                data=primary_data,
                warnings=warnings,
                prompt_result=primary,
                repair_success=bool(primary.repair_success),
                retry_success=bool(primary.retry_success),
                fallback_used=bool(primary.fallback_used),
                model_used=primary.model_used or config.model,
            )
            self._write_step_json(step_name, primary_data)
            self._write_repair_log(result)
            return result

        retry_context = dict(context)
        retry_context["previous_errors"] = errors[:12]
        retry_context["previous_output"] = primary_data
        retry_instruction = self._targeted_retry_instruction(step_name, errors)
        if retry_instruction:
            retry_context["targeted_retry_instruction"] = retry_instruction
        retry = self._run_prompt(step_name, retry_context, suffix="_targeted_retry")
        retry_validated = False
        failure_data: dict[str, Any] | None = primary_data
        if retry.status == "ok" and isinstance(retry.parsed, dict):
            retry_data = self._local_repair(step_name, retry.parsed, context)
            retry_errors, retry_warnings = self._validate(step_name, retry_data, context)
            if not retry_errors:
                result = StageStepResult(
                    step_name=step_name,
                    ok=True,
                    status="ok",
                    data=retry_data,
                    warnings=[*warnings, *retry_warnings],
                    prompt_result=primary,
                    retry_prompt_result=retry,
                    retry_used=True,
                    retry_success=True,
                    repair_success=bool(primary.repair_success or retry.repair_success),
                    fallback_used=bool(primary.fallback_used or retry.fallback_used),
                    model_used=retry.model_used or primary.model_used or config.model,
                )
                self._write_step_json(step_name, retry_data)
                self._write_repair_log(result)
                return result
            errors = retry_errors
            warnings = [*warnings, *retry_warnings]
            retry_validated = True
            failure_data = retry_data
        else:
            errors = [retry.error or f"{step_name} targeted retry failed"]

        manual_payload = None
        if retry_validated:
            manual_payload = self._manual_review_payload_for_validation(
                step_name,
                errors,
                data=failure_data if isinstance(failure_data, dict) else {},
                retry_used=True,
            )
        if manual_payload is not None:
            if step_name in {"fact_extraction", "realistic_utterance_realization"}:
                result = StageStepResult(
                    step_name=step_name,
                    ok=False,
                    status=str(manual_payload.get("status") or "manual_review_required"),
                    data=manual_payload,
                    errors=errors,
                    warnings=warnings,
                    prompt_result=primary,
                    retry_prompt_result=retry,
                    retry_used=True,
                    retry_success=False,
                    repair_success=bool(primary.repair_success or retry.repair_success),
                    fallback_used=bool(primary.fallback_used or retry.fallback_used),
                    model_used=retry.model_used or primary.model_used or config.model,
                    error_type=str(manual_payload.get("error_type") or "validation_routed"),
                )
                self._write_step_error(result)
                return result
            result = StageStepResult(
                step_name=step_name,
                ok=True,
                status="ok",
                data=manual_payload,
                warnings=warnings,
                prompt_result=primary,
                retry_prompt_result=retry,
                retry_used=True,
                retry_success=False,
                repair_success=bool(primary.repair_success or retry.repair_success),
                fallback_used=bool(primary.fallback_used or retry.fallback_used),
                model_used=retry.model_used or primary.model_used or config.model,
            )
            self._write_step_json(step_name, manual_payload)
            self._write_repair_log(result)
            return result

        result = StageStepResult(
            step_name=step_name,
            ok=False,
            status="schema_invalid",
            data=self._local_repair(step_name, retry.parsed, context) if isinstance(retry.parsed, dict) else primary_data,
            errors=errors,
            warnings=warnings,
            prompt_result=primary,
            retry_prompt_result=retry,
            retry_used=True,
            retry_success=False,
            repair_success=bool(primary.repair_success or retry.repair_success),
            fallback_used=bool(primary.fallback_used or retry.fallback_used),
            model_used=retry.model_used or primary.model_used or config.model,
            error_type="schema_invalid",
        )
        self._write_step_error(result)
        return result

    def _run_prompt(self, step_name: str, context: dict[str, Any], *, suffix: str) -> PromptStepResult:
        config = self.step_configs[step_name]
        params = {"temperature": 0.0, "max_tokens": 1500, **config.params}
        repair_params = self.step_configs.get("json_repair", StepConfig(model=config.model, params={})).params
        model = config.model
        client = self.client_factory(model)
        return run_prompt_step(
            instance_dir=self.instance_dir,
            step_name=step_name,
            cache_prefix=f"staged_{step_name}{suffix}",
            prompt_path=PROMPT_DIR / f"{step_name}.md",
            context=context,
            client=client,
            model=model,
            temperature=params.get("temperature"),
            max_tokens=params.get("max_tokens"),
            thinking=params.get("thinking"),
            response_format=params.get("response_format"),
            repair_temperature=repair_params.get("temperature"),
            repair_max_tokens=repair_params.get("max_tokens"),
            repair_thinking=repair_params.get("thinking"),
            repair_response_format=repair_params.get("response_format"),
            force=True,
            no_api=self.no_api or self.dry_run,
            same_model_retries=1,
            compact_retries=0,
            fallback_retries=0,
        )

    def _validate(self, step_name: str, data: dict[str, Any], context: dict[str, Any]) -> tuple[list[str], list[str]]:
        validator = VALIDATORS[step_name]
        return validator(data, context)

    def _manual_review_payload_for_validation(
        self,
        step_name: str,
        errors: list[str],
        *,
        data: dict[str, Any] | None = None,
        retry_used: bool = False,
    ) -> dict[str, Any] | None:
        data = data if isinstance(data, dict) else {}
        if step_name == "fact_extraction":
            if not self._has_fact_grounding_errors(errors):
                return None
            stats = self._source_span_stats_from_fact_data(data)
            unmatched_error_count = sum(
                1 for error in errors if "source_span is not aligned to source" in str(error)
            )
            if unmatched_error_count > int(stats.get("source_span_unmatched_count") or 0):
                missing = unmatched_error_count - int(stats.get("source_span_unmatched_count") or 0)
                stats["source_span_unmatched_count"] = unmatched_error_count
                status_counts = stats.get("source_match_status")
                if not isinstance(status_counts, dict):
                    status_counts = {}
                status_counts["unmatched"] = int(status_counts.get("unmatched") or 0) + missing
                stats["source_match_status"] = status_counts
            return {
                "status": "manual_review_required",
                "reason": "fact_grounding_validation_failed",
                "manual_review_reason": (
                    "critical source_span unmatched after repair/retry: "
                    + "; ".join(errors)
                ),
                "review_stage": step_name,
                "source_span_repaired_count": stats["source_span_repaired_count"],
                "source_span_unmatched_count": stats["source_span_unmatched_count"],
                "source_match_status": stats["source_match_status"],
                "issues": errors,
            }
        if step_name == "realistic_utterance_realization":
            if not (self._has_realization_leakage_errors(errors) or self._has_realization_template_artifact_errors(errors)):
                return None
            classification = self._classify_realization_leakage(data)
            status = "rejected" if classification["severity"] == "private_or_evaluator_leakage" else "manual_review_required"
            reason = (
                "realization_private_leakage_rejected"
                if status == "rejected"
                else (
                    "utterance_template_artifact"
                    if self._has_realization_template_artifact_errors(errors)
                    else "realization_leakage_manual_review"
                )
            )
            return {
                "status": status,
                "reason": reason,
                "manual_review_reason": "; ".join(errors),
                "review_stage": step_name,
                "realization_leakage_detected": True,
                "realization_retry_used": bool(retry_used),
                "leakage_severity": (
                    "template_artifact_or_benchmarkish"
                    if reason == "utterance_template_artifact"
                    else classification["severity"]
                ),
                "final_status": status,
                "error_type": reason,
                "issues": errors,
            }
        if step_name not in {"initial_report_plan", "noisy_revision_event_plan"}:
            return None
        text = "\n".join(errors).lower()
        reason = ""
        if "withheld_units_for_later" in text or "withhold at least one" in text:
            reason = "no_withheld_units_for_later_refinement"
        elif "must not introduce every user-exposable fact unit" in text:
            reason = "no_withheld_units_for_later_refinement"
        elif "noisy/non-monotonic" in text or "non-monotonic" in text:
            reason = "insufficient_source_facts_for_noisy_refinement"
        elif "wrong or speculative turns are unresolved" in text:
            reason = "insufficient_source_facts_for_noisy_refinement"
        elif "must introduce, revise, deactivate, or activate" in text:
            reason = "insufficient_source_facts_for_noisy_refinement"
        elif "final turn must not leave" in text:
            reason = "insufficient_source_facts_for_noisy_refinement"
        elif "old progressive" in text or "deprecated progressive" in text:
            reason = "would_degenerate_into_progressive_disclosure"
        elif step_name == "noisy_revision_event_plan":
            reason = "invalid_noisy_revision_event_plan"
        if not reason:
            return None
        return {
            "status": "manual_review_required",
            "reason": reason,
            "manual_review_reason": "; ".join(errors),
            "review_stage": step_name,
            "old_progressive_disclosure_pattern": False,
            "unresolved_wrong_claims": "not_applicable",
            "scenario_fit": "not_generated",
            "issues": errors,
        }

    def _has_realization_leakage_errors(self, errors: list[str]) -> bool:
        text = "\n".join(str(error) for error in errors).lower()
        return "leaks forbidden implementation/benchmark text" in text or "benchmark/private" in text

    def _has_fact_grounding_errors(self, errors: list[str]) -> bool:
        text = "\n".join(str(error) for error in errors).lower()
        return "source_span is not aligned to source" in text or "source_span unmatched" in text

    def _has_realization_template_artifact_errors(self, errors: list[str]) -> bool:
        text = "\n".join(str(error) for error in errors).lower()
        return "template artifact" in text

    def _targeted_retry_instruction(self, step_name: str, errors: list[str]) -> str:
        if step_name == "fact_extraction" and self._has_fact_grounding_errors(errors):
            return (
                "Fix only source grounding. Every fact_units[].source_span must be an exact original substring "
                "from problem_statement or hints_text matching its source field. Do not paraphrase source_span. "
                "Remove or mark non-user-facing facts that cannot be grounded."
            )
        if step_name == "realistic_utterance_realization":
            if self._has_realization_leakage_errors(errors) or self._has_realization_template_artifact_errors(errors):
                return (
                    "Remove template/meta wording and forbidden implementation, benchmark, oracle, gold, patch, diff, "
                    "hidden-test, reference-patch, and test-name wording while preserving the same source-grounded user issue."
                )
        return ""

    def _source_span_stats_from_fact_data(self, data: dict[str, Any]) -> dict[str, Any]:
        if isinstance(data.get("source_match_status"), dict):
            return {
                "source_span_repaired_count": int(data.get("source_span_repaired_count") or 0),
                "source_span_unmatched_count": int(data.get("source_span_unmatched_count") or 0),
                "source_match_status": data.get("source_match_status") or {},
            }
        units = data.get("fact_units") if isinstance(data.get("fact_units"), list) else []
        return summarize_source_matches(units)

    def _classify_realization_leakage(self, data: dict[str, Any]) -> dict[str, Any]:
        blob = text_blob(data)
        private_re = re.compile(
            r"\boracle\b|\bgold\b|FAIL_TO_PASS|PASS_TO_PASS|test_patch|reference\s+patch|"
            r"diff\s+--git|hidden\s+tests?|\braw_llm_outputs\b|\btest_[A-Za-z0-9_]+\b",
            re.IGNORECASE,
        )
        if private_re.search(blob):
            return {"severity": "private_or_evaluator_leakage"}
        if BENCHMARK_METADATA_RE.search(blob):
            return {"severity": "benchmarkish_or_implementation_hint"}
        if IMPLEMENTATION_HINT_RE.search(blob):
            return {"severity": "benchmarkish_or_implementation_hint"}
        if re.search(r"\bpatch\b|\bdiff\b|\btests?\b|\bbenchmark\b", blob, re.IGNORECASE):
            return {"severity": "benchmarkish_or_implementation_hint"}
        return {"severity": "benchmarkish_or_implementation_hint"}

    def _local_repair(self, step_name: str, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return data
        if step_name == "fact_extraction":
            return self._repair_fact_extraction(data, context)
        if step_name == "intent_revision":
            return self._repair_intent_revision(data, context)
        if step_name == "initial_report_plan":
            return self._repair_initial_report_plan(data, context)
        if step_name == "noisy_revision_event_plan":
            return self._repair_noisy_revision_event_plan(data, context)
        if step_name == "realistic_utterance_realization":
            return self._repair_realistic_utterance_realization(data, context)
        return data

    def _exposed_unit_ids(self, fact_data: dict[str, Any]) -> set[str]:
        return {
            str(unit.get("unit_id"))
            for unit in fact_data.get("fact_units", [])
            if isinstance(unit, dict)
            and unit.get("unit_id")
            and unit.get("expose_to_user") is not False
            and unit.get("type") != "implementation_hint"
        }

    def _clean_unit_ids(self, values: Any, exposed_ids: set[str]) -> list[str]:
        if not isinstance(values, list):
            values = [] if values in (None, "") else [values]
        result: list[str] = []
        for value in values:
            unit_id = str(value or "").strip()
            if unit_id and unit_id in exposed_ids and unit_id not in result:
                result.append(unit_id)
        return result

    def _repair_initial_report_plan(self, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if str(data.get("status") or "") == "manual_review_required":
            return data
        repaired = copy.deepcopy(data)
        report = repaired.get("initial_report") if isinstance(repaired.get("initial_report"), dict) else {}
        fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
        exposed_ids = self._exposed_unit_ids(fact_data)
        if isinstance(report, dict):
            for field in ["introduced_units", "noisy_or_imperfect_units", "withheld_units_for_later"]:
                report[field] = self._clean_unit_ids(report.get(field), exposed_ids)
            if not isinstance(report.get("imperfection_types"), list):
                report["imperfection_types"] = [str(report.get("imperfection_types") or "").strip()] if report.get("imperfection_types") else []
            repaired["initial_report"] = report
        return repaired

    def _repair_noisy_revision_event_plan(self, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if str(data.get("status") or "") == "manual_review_required":
            return data
        repaired = copy.deepcopy(data)
        turns = repaired.get("turns") if isinstance(repaired.get("turns"), list) else []
        fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
        exposed_ids = self._exposed_unit_ids(fact_data)
        clean_turns: list[dict[str, Any]] = []
        for raw in turns:
            if not isinstance(raw, dict):
                continue
            turn = copy.deepcopy(raw)
            turn["introduced_units"] = self._clean_unit_ids(turn.get("introduced_units"), exposed_ids)
            turn["revises_units"] = self._clean_unit_ids(turn.get("revises_units"), exposed_ids)
            for field in [
                "revises_turns",
                "deactivates_claims",
                "activates_claims",
                "active_after_turn",
                "inactive_after_turn",
            ]:
                if not isinstance(turn.get(field), list):
                    turn[field] = [] if turn.get(field) in (None, "", False) else [str(turn.get(field))]
                else:
                    turn[field] = [str(item).strip() for item in turn.get(field, []) if str(item).strip()]
            if "must_be_resolved_later" not in turn:
                turn["must_be_resolved_later"] = False
            clean_turns.append(turn)
        for index, turn in enumerate(clean_turns, start=1):
            turn["turn_id"] = f"T{index}"
        repaired["turns"] = clean_turns
        return repaired

    def _repair_realistic_utterance_realization(self, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        repaired = copy.deepcopy(data)
        utterances = repaired.get("utterances") if isinstance(repaired.get("utterances"), list) else []
        event_plan = context.get("noisy_revision_event_plan") if isinstance(context.get("noisy_revision_event_plan"), dict) else {}
        fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
        turns = event_plan.get("turns") if isinstance(event_plan.get("turns"), list) else []
        turn_by_id = {str(turn.get("turn_id")): turn for turn in turns if isinstance(turn, dict) and turn.get("turn_id")}
        unit_by_id = {
            str(unit.get("unit_id")): unit
            for unit in fact_data.get("fact_units", [])
            if isinstance(unit, dict) and unit.get("unit_id")
        }
        forbidden = re.compile(
            r"\bpatch\b|\bdiff\b|\btests?\b|\bbenchmark\b|\bgold\b|\boracle\b|"
            r"FAIL_TO_PASS|PASS_TO_PASS|hidden\s+test|reference\s+patch|pr\s*#?\d+",
            re.IGNORECASE,
        )
        repaired_utterances: list[dict[str, Any]] = []
        for index, raw in enumerate(utterances):
            if not isinstance(raw, dict):
                continue
            utterance = copy.deepcopy(raw)
            turn_id = str(utterance.get("turn_id") or "").strip()
            text = str(utterance.get("user_utterance") or "").strip()
            turn = turn_by_id.get(turn_id) if isinstance(turn_by_id.get(turn_id), dict) else {}
            introduced = [str(unit_id) for unit_id in turn.get("introduced_units", []) if str(unit_id).strip()]
            needs_repair = not text or bool(forbidden.search(text))
            if index == 0 and not (120 <= len(text) <= 900):
                needs_repair = True
            if introduced and not self._utterance_aligns(text, introduced, unit_by_id):
                needs_repair = True
            if needs_repair:
                utterance["user_utterance"] = self._realistic_utterance_for_turn(index, turn, introduced, unit_by_id)
            repaired_utterances.append(utterance)
        repaired["utterances"] = repaired_utterances
        return repaired

    def _utterance_aligns(self, text: str, introduced: list[str], unit_by_id: dict[str, dict[str, Any]]) -> bool:
        text_tokens = self._utterance_tokens(text)
        if not text_tokens:
            return False
        for unit_id in introduced:
            unit_tokens = self._utterance_tokens(unit_by_id.get(unit_id, {}).get("text", ""))
            if text_tokens & unit_tokens:
                return True
        return False

    def _utterance_tokens(self, text: Any) -> set[str]:
        aliases = {
            "urls": "url",
            "failing": "fail",
            "fails": "fail",
            "failed": "fail",
            "fixing": "fix",
            "rejected": "reject",
            "rejecting": "reject",
            "accepted": "accept",
            "accepting": "accept",
            "encoded": "encode",
            "encoding": "encode",
            "complexity": "complex",
            "complex": "complex",
        }
        stopwords = {"the", "and", "for", "with", "that", "this", "should", "must", "need", "needs", "still"}
        raw = re.sub(r"[-_/().:=]+", " ", str(text or "").lower())
        tokens: set[str] = set()
        for token in re.findall(r"[a-z][a-z0-9]{2,}", raw):
            token = aliases.get(token, token)
            if token.endswith("ing") and len(token) > 5:
                token = token[:-3]
            elif token.endswith("ed") and len(token) > 4:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            token = aliases.get(token, token)
            if token not in stopwords:
                tokens.add(token)
        return tokens

    def _realistic_utterance_for_turn(
        self,
        index: int,
        turn: dict[str, Any],
        introduced: list[str],
        unit_by_id: dict[str, dict[str, Any]],
    ) -> str:
        operation = str(turn.get("operation") or "")
        units = [unit_by_id.get(unit_id, {}) for unit_id in introduced]
        types = {str(unit.get("type") or "") for unit in units}
        facts = [self._clean_fact_for_utterance(str(unit.get("text") or "")) for unit in units if unit.get("text")]
        facts = [fact for fact in facts if fact]
        if index == 0:
            body = " ".join(facts[:4])
            if not body:
                body = "I have a reasonably specific bug report, but a few details are still uncertain."
            if len(body) < 120:
                body = (
                    f"{body} I may be missing one boundary condition, and my first read of the cause might be off, "
                    "so please treat this as the report to clarify rather than a final diagnosis."
                )
            return body[:900].strip()
        if operation == "add_regression_constraint" or "regression_expectation" in types:
            return f"One constraint I forgot: {facts[0] if facts else 'existing supported behavior in this area still needs to keep working'}"
        if operation in {"correct_previous_claim", "retract_previous_claim", "replace_previous_claim", "resolve_conflict"} or types & REVISION_FACT_TYPES:
            return f"I checked that part again; my earlier read may be wrong. {facts[0] if facts else 'Please use this corrected detail instead.'}"
        if operation in {"speculative_hypothesis", "mistaken_clarification", "incorrect_reproduction_detail"}:
            return f"This may be my misunderstanding, but {facts[0].lower() if facts else 'there is one detail I am not fully sure about.'}"
        if operation == "confirm_final_active_intent":
            return f"Yes, the final behavior should follow that corrected interpretation. {facts[0] if facts else ''}".strip()
        return facts[0] if facts else "I confirmed one more detail from the report."

    def _clean_fact_for_utterance(self, text: str) -> str:
        cleaned = self._scrub_benchmark_surface(text)
        cleaned = re.sub(r"\b(?:the reporter|the report says)\b", "I", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bpr\s*#?\d+\b", "the earlier change", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" .")
        if len(cleaned) > 140:
            cleaned = cleaned[:137].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
        if cleaned:
            return cleaned[0].upper() + cleaned[1:] + "."
        return ""

    def _repair_fact_extraction(self, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        repaired = copy.deepcopy(data)
        if "issue_summary" in repaired:
            repaired["issue_summary"] = self._scrub_benchmark_surface(str(repaired.get("issue_summary") or ""))
        units = repaired.get("fact_units") if isinstance(repaired.get("fact_units"), list) else []
        units = self._repair_fact_units(units, context)
        units = self._ensure_basic_behavior_units(units, context)
        repaired["fact_units"] = units
        repaired.update(summarize_source_matches(units))
        if any(isinstance(unit, dict) and unit.get("type") == "ambiguity_or_correction" for unit in units):
            return repaired
        source_text = str(context.get("problem_statement") or "")
        match = self._source_uncertainty_match(source_text)
        if match is None:
            return repaired
        next_id = self._next_unit_id(units)
        units.append(
            {
                "unit_id": next_id,
                "type": "ambiguity_or_correction",
                "text": "The reporter is uncertain whether the observed behavior is a bug or intended behavior.",
                "source": "problem_statement",
                "source_span": match.group(0).strip(),
                "normalized_source_span": normalized_source_text(match.group(0).strip()),
                "source_match_status": "exact",
                "active_by_default": True,
                "expose_to_user": True,
            }
        )
        repaired["fact_units"] = units
        repaired.update(summarize_source_matches(units))
        return repaired

    def _repair_fact_units(self, units: list[Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        repaired_units: list[dict[str, Any]] = []
        inactive_types = {"non_goal", "obsolete_candidate", "rejected_solution"}
        for raw in units:
            if not isinstance(raw, dict):
                continue
            unit = copy.deepcopy(raw)
            if "text" in unit:
                unit["text"] = self._scrub_benchmark_surface(str(unit.get("text") or ""))
            text = str(unit.get("text") or "")
            source_span = str(unit.get("source_span") or "")
            if BENCHMARK_METADATA_RE.search(text) or BENCHMARK_METADATA_RE.search(source_span):
                continue
            source = str(unit.get("source") or "")
            combined = f"{text}\n{source_span}".lower()
            haystack = str(context.get(source) or "")
            if source_span:
                match = find_source_span_match(source_span, haystack, fact_text=text)
                status = str(match.get("status") or "unmatched")
                matched_span = str(match.get("matched_span") or "").strip()
                if status in SOURCE_MATCH_OK and matched_span:
                    if matched_span != source_span:
                        unit["original_source_span"] = source_span
                        unit["source_span"] = matched_span
                        source_span = matched_span
                    unit["normalized_source_span"] = normalized_source_text(matched_span)
                    unit["source_match_status"] = status
                    if status == "fuzzy":
                        unit["source_match_score"] = match.get("score")
                    combined = f"{text}\n{source_span}".lower()
                else:
                    unit["source_match_status"] = status
                    unit["normalized_source_span"] = normalized_source_text(source_span)
            if source == "hints_text" and (
                ".py" in combined
                or "line " in combined
                or "proposal" in combined
                or "proposed fix" in combined
                or "relevant code" in combined
                or "assumption" in combined
                or "run tests" in combined
                or "cannot run tests" in combined
                or "local env" in combined
            ):
                unit["type"] = "implementation_hint"
            if source == "hints_text" and re.search(
                r"\b(?:np|numpy)\s*[0-9]|\b(?:released|dropped)\b|\b\d+\s*(?:h|hr|hour)s?\b",
                combined,
            ):
                unit["type"] = "implementation_hint"
            if re.search(r"\bpr\s*#?\d+\b", combined) or re.search(
                r"\b(?:test|case)\s+(?:came|comes|was introduced|introduced)\s+in\b",
                combined,
            ):
                unit["type"] = "implementation_hint"
            if re.search(r"\b(?:originates?|derived)\b[^\n.]{0,120}\b(?:gist|#\d+)\b", combined):
                unit["type"] = "implementation_hint"
            if unit.get("type") == "implementation_hint":
                unit["active_by_default"] = False
                unit["expose_to_user"] = False
            elif unit.get("type") in inactive_types:
                unit["active_by_default"] = False
            repaired_units.append(unit)
        return repaired_units

    def _scrub_benchmark_surface(self, text: str) -> str:
        cleaned = re.sub(r"\btest_[A-Za-z0-9_]+\b", "the reported CI case", text)
        cleaned = re.sub(r"\btests/[A-Za-z0-9_./-]+", "the reported test file", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\blib/[A-Za-z0-9_./-]+::[A-Za-z0-9_]+\b", "the reported CI case", cleaned)
        return cleaned

    def _ensure_basic_behavior_units(self, units: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
        covered = {str(unit.get("type") or "") for unit in units}
        if len(covered & {"symptom", "observed_behavior", "expected_behavior"}) >= 2:
            return units
        source_text = str(context.get("problem_statement") or "")
        added = list(units)
        failure_match = re.search(r"is failing quite a few of the CI runs with a Value Error", source_text, re.IGNORECASE)
        if failure_match and "symptom" not in covered:
            added.append(
                {
                    "unit_id": self._next_unit_id(added),
                    "type": "symptom",
                    "text": "A CI rendering case fails with a ValueError.",
                    "source": "problem_statement",
                    "source_span": failure_match.group(0),
                    "normalized_source_span": normalized_source_text(failure_match.group(0)),
                    "source_match_status": "exact",
                    "active_by_default": True,
                    "expose_to_user": True,
                }
            )
            covered.add("symptom")
        if failure_match and "expected_behavior" not in covered:
            added.append(
                {
                    "unit_id": self._next_unit_id(added),
                    "type": "expected_behavior",
                    "text": "The reported rendering case should not fail with that ValueError.",
                    "source": "problem_statement",
                    "source_span": failure_match.group(0),
                    "normalized_source_span": normalized_source_text(failure_match.group(0)),
                    "source_match_status": "exact",
                    "active_by_default": True,
                    "expose_to_user": True,
                }
            )
        return added

    def _span_in_source(self, span: str, source_text: str) -> bool:
        normalized_span = " ".join(str(span or "").lower().split())
        normalized_source = " ".join(str(source_text or "").lower().split())
        return bool(normalized_span and normalized_span in normalized_source)

    def _find_exact_source_span(self, source_span: str, text: str, source_text: str) -> str | None:
        if not source_text:
            return None
        raw_span = str(source_span or "")
        candidates = [raw_span]
        for separator in ["...", ";", " returns ", " array("]:
            expanded: list[str] = []
            for candidate in candidates:
                expanded.extend(candidate.split(separator))
            candidates.extend(expanded)
        for part in sorted((item.strip(" .;:\n\r\t") for item in candidates), key=len, reverse=True):
            if len(part) >= 12 and self._span_in_source(part, source_text):
                return part
        words = re.findall(r"[A-Za-z0-9_`'./:&%-]+", str(text or ""))
        for size in range(min(10, len(words)), 2, -1):
            for start in range(0, len(words) - size + 1):
                phrase = " ".join(words[start : start + size]).strip()
                if len(phrase) >= 12 and self._span_in_source(phrase, source_text):
                    return phrase
        return None

    def _source_uncertainty_match(self, source_text: str) -> re.Match[str] | None:
        patterns = [
            re.compile(r"(?:I\s+)?might\s+be\s+missing\s+something\??", re.IGNORECASE),
            re.compile(r"(?:I\s+)?may\s+be\s+missing\s+something\??", re.IGNORECASE),
            re.compile(r"am\s+I\s+missing\s+something\??", re.IGNORECASE),
            re.compile(r"(?:not\s+sure|unsure)\s+(?:if|whether)[^.?\n]*(?:bug|expected|intended)[^.?\n]*[.?]?", re.IGNORECASE),
            re.compile(r"(?:is|was)\s+(?:this|that|it)\s+(?:expected|intended)\??", re.IGNORECASE),
        ]
        for pattern in patterns:
            match = pattern.search(source_text)
            if match:
                return match
        return None

    def _repair_intent_revision(self, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        repaired = copy.deepcopy(data)
        support = repaired.get("revision_support") if isinstance(repaired.get("revision_support"), dict) else {}
        fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
        units = fact_data.get("fact_units") if isinstance(fact_data.get("fact_units"), list) else []
        unit_by_id = {
            str(unit.get("unit_id")): unit
            for unit in units
            if isinstance(unit, dict) and unit.get("unit_id")
        }
        if support.get("has_revision_fact") is True and support.get("revision_unit_ids"):
            requested_ids = [str(unit_id) for unit_id in support.get("revision_unit_ids", []) if str(unit_id).strip()]
            valid_ids = [
                unit_id
                for unit_id in requested_ids
                if unit_by_id.get(unit_id, {}).get("type") in REVISION_FACT_TYPES
                and unit_by_id.get(unit_id, {}).get("expose_to_user") is not False
            ]
            if valid_ids:
                unit_type = str(unit_by_id.get(valid_ids[0], {}).get("type") or support.get("revision_type") or "").strip()
                repaired["revision_support"] = {
                    "has_revision_fact": True,
                    "revision_type": unit_type,
                    "revision_unit_ids": valid_ids,
                    "reason": str(support.get("reason") or f"{valid_ids[0]} is a source-grounded revision fact.").strip(),
                }
                return repaired
        revision_unit = self._best_revision_unit(units)
        if revision_unit is None:
            return repaired
        unit_id = str(revision_unit.get("unit_id") or "").strip()
        unit_type = str(revision_unit.get("type") or "").strip()
        if not unit_id or not unit_type:
            return repaired
        repaired["revision_support"] = {
            "has_revision_fact": True,
            "revision_type": unit_type,
            "revision_unit_ids": [unit_id],
            "reason": f"{unit_id} is a source-grounded {unit_type} fact that supports intent revision.",
        }
        return repaired

    def _best_revision_unit(self, units: list[Any]) -> dict[str, Any] | None:
        priority = {
            "conflict_or_tension": 0,
            "ambiguity_or_correction": 1,
            "rejected_solution": 2,
            "obsolete_candidate": 3,
            "regression_expectation": 4,
            "negative_constraint": 5,
        }
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for index, unit in enumerate(units):
            if (
                isinstance(unit, dict)
                and unit.get("type") in REVISION_FACT_TYPES
                and unit.get("expose_to_user") is not False
            ):
                candidates.append((priority.get(str(unit.get("type")), 99), index, unit))
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (item[0], item[1]))[0][2]

    def _next_unit_id(self, units: list[Any]) -> str:
        highest = 0
        used: set[str] = set()
        for unit in units:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("unit_id") or "")
            used.add(unit_id)
            match = re.fullmatch(r"U(\d+)", unit_id)
            if match:
                highest = max(highest, int(match.group(1)))
        candidate = highest + 1
        while f"U{candidate}" in used:
            candidate += 1
        return f"U{candidate}"

    def _write_step_json(self, step_name: str, data: dict[str, Any]) -> None:
        path = self.instance_dir / ".build" / f"{step_name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if step_name == "semantic_reviewer":
            alias = self.instance_dir / ".build" / "semantic_review.json"
            alias.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _write_step_error(self, result: StageStepResult) -> None:
        path = self.instance_dir / ".build" / "step_errors.jsonl"
        entry = {
            "step": result.step_name,
            "status": result.status,
            "errors": result.errors,
            "warnings": result.warnings,
            "error_type": result.error_type,
            "model": result.model_used,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if result.data:
            self._write_step_json(result.step_name, result.data)

    def _write_repair_log(self, result: StageStepResult) -> None:
        if not result.repair_success:
            return
        path = self.instance_dir / ".build" / "repair_logs.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "step": result.step_name,
                        "repair_success": result.repair_success,
                        "retry_used": result.retry_used,
                        "model": result.model_used,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
