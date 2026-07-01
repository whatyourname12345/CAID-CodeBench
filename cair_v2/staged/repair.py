from __future__ import annotations

from typing import Any

from cair_v2.llm.json_utils import ParsedModelOutput, parse_model_output, repair_json_candidate


def parse_or_repair_json(raw_text: str) -> ParsedModelOutput:
    """Use the shared JSON parser/repair helpers for staged outputs."""

    parsed = parse_model_output(raw_text)
    if parsed.ok:
        return parsed
    repaired = repair_json_candidate(raw_text)
    if repaired is None:
        return parsed
    return parse_model_output(repaired)


def repair_log_entry(step_name: str, parsed: ParsedModelOutput) -> dict[str, Any]:
    return {
        "step": step_name,
        "ok": parsed.ok,
        "format": parsed.format,
        "error": parsed.error,
        "error_type": parsed.error_type,
    }

