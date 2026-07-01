from __future__ import annotations

import json
import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cair_v2.construction.sanitizer import BENCHMARK_METADATA_RE
from cair_v2.llm.clients import DeepSeekClient
from cair_v2.llm.prompt_runner import PromptStepResult, run_prompt_step
from cair_v2.staged.schemas import REVISION_FACT_TYPES, StageStepResult
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
        retry = self._run_prompt(step_name, retry_context, suffix="_targeted_retry")
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
        else:
            errors = [retry.error or f"{step_name} targeted retry failed"]

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

    def _local_repair(self, step_name: str, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            return data
        if step_name == "fact_extraction":
            return self._repair_fact_extraction(data, context)
        if step_name == "intent_revision":
            return self._repair_intent_revision(data, context)
        if step_name != "dialogue_skeleton":
            return data
        repaired = copy.deepcopy(data)
        turns = repaired.get("turns") if isinstance(repaired.get("turns"), list) else []
        support = context.get("revision_support") if isinstance(context.get("revision_support"), dict) else {}
        revision_ids = {str(unit_id) for unit_id in support.get("revision_unit_ids", []) if str(unit_id).strip()}
        revision_type = str(support.get("revision_type") or "").strip()
        unit_by_id = {
            str(unit.get("unit_id")): unit
            for unit in context.get("fact_units", [])
            if isinstance(unit, dict) and unit.get("unit_id")
        }
        for index, turn in enumerate(turns):
            if not isinstance(turn, dict):
                continue
            operation = str(turn.get("operation") or "").strip()
            introduced = [str(unit_id) for unit_id in turn.get("introduced_units", []) if str(unit_id).strip()]
            if operation in {"revision", "revise", "intent_revision"}:
                turn["operation"] = self._revision_operation(revision_type)
            elif operation in {"elaborate", "expand", "provide_context"}:
                turn["operation"] = self._operation_for_units(introduced, unit_by_id, revision_ids, revision_type)
            elif index > 0 and operation == "reveal_vague_goal":
                turn["operation"] = self._operation_for_units(introduced, unit_by_id, revision_ids, revision_type)
        return repaired

    def _revision_operation(self, revision_type: str) -> str:
        if revision_type in {"obsolete_candidate"}:
            return "obsolete"
        if revision_type in {"rejected_solution"}:
            return "reject"
        if revision_type in {"conflict_or_tension"}:
            return "resolve_conflict"
        return "correct"

    def _operation_for_units(
        self,
        introduced: list[str],
        unit_by_id: dict[str, dict[str, Any]],
        revision_ids: set[str],
        revision_type: str,
    ) -> str:
        if set(introduced) & revision_ids:
            return self._revision_operation(revision_type)
        types = {str(unit_by_id.get(unit_id, {}).get("type") or "") for unit_id in introduced}
        if "regression_expectation" in types:
            return "add_regression_constraint"
        if "negative_constraint" in types:
            return "add_negative_constraint"
        if types & {"observed_behavior", "expected_behavior", "error_message", "active_constraint"}:
            return "refine"
        return "add_information"

    def _repair_fact_extraction(self, data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        repaired = copy.deepcopy(data)
        units = repaired.get("fact_units") if isinstance(repaired.get("fact_units"), list) else []
        units = self._repair_fact_units(units, context)
        repaired["fact_units"] = units
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
                "active_by_default": True,
                "expose_to_user": True,
            }
        )
        repaired["fact_units"] = units
        return repaired

    def _repair_fact_units(self, units: list[Any], context: dict[str, Any]) -> list[dict[str, Any]]:
        repaired_units: list[dict[str, Any]] = []
        inactive_types = {"non_goal", "obsolete_candidate", "rejected_solution"}
        for raw in units:
            if not isinstance(raw, dict):
                continue
            unit = copy.deepcopy(raw)
            text = str(unit.get("text") or "")
            source_span = str(unit.get("source_span") or "")
            if BENCHMARK_METADATA_RE.search(text) or BENCHMARK_METADATA_RE.search(source_span):
                continue
            source = str(unit.get("source") or "")
            combined = f"{text}\n{source_span}".lower()
            haystack = str(context.get(source) or "")
            if source_span and not self._span_in_source(source_span, haystack):
                exact = self._find_exact_source_span(source_span, text, haystack)
                if exact:
                    unit["source_span"] = exact
                    source_span = exact
                    combined = f"{text}\n{source_span}".lower()
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
            if unit.get("type") == "implementation_hint":
                unit["active_by_default"] = False
                unit["expose_to_user"] = False
            elif unit.get("type") in inactive_types:
                unit["active_by_default"] = False
            repaired_units.append(unit)
        return repaired_units

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
        if support.get("has_revision_fact") is True and support.get("revision_unit_ids"):
            return repaired
        fact_data = context.get("fact_extraction") if isinstance(context.get("fact_extraction"), dict) else {}
        units = fact_data.get("fact_units") if isinstance(fact_data.get("fact_units"), list) else []
        revision_unit = None
        for unit in units:
            if isinstance(unit, dict) and unit.get("type") in REVISION_FACT_TYPES:
                revision_unit = unit
                break
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
