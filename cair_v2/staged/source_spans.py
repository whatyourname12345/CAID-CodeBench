from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any


SOURCE_MATCH_OK = {"exact", "normalized", "fuzzy"}
SOURCE_MATCH_UNMATCHED = {"empty", "invalid_source", "unmatched"}

CRITICAL_SOURCE_FACT_TYPES = {
    "symptom",
    "observed_behavior",
    "expected_behavior",
    "reproduction",
    "error_message",
    "environment",
    "affected_component",
    "active_constraint",
    "negative_constraint",
    "regression_expectation",
    "ambiguity_or_correction",
    "conflict_or_tension",
}


def _normalize_char(char: str) -> str:
    char = unicodedata.normalize("NFKC", char)
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2026": "...",
        "\xa0": " ",
        "`": " ",
        "*": " ",
    }
    return replacements.get(char, char)


def normalized_source_text(text: Any) -> str:
    normalized, _ = normalized_source_text_with_map(text)
    return normalized


def normalized_source_text_with_map(text: Any) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    previous_space = True
    for original_index, raw_char in enumerate(str(text or "")):
        mapped = _normalize_char(raw_char)
        for char in mapped:
            if char.isspace():
                if not previous_space:
                    chars.append(" ")
                    index_map.append(original_index)
                    previous_space = True
                continue
            lowered = char.lower()
            chars.append(lowered)
            index_map.append(original_index)
            previous_space = False
    while chars and chars[-1] == " ":
        chars.pop()
        index_map.pop()
    return "".join(chars), index_map


def is_critical_fact_unit(unit: dict[str, Any]) -> bool:
    unit_type = str(unit.get("type") or "")
    if unit_type not in CRITICAL_SOURCE_FACT_TYPES:
        return False
    if unit.get("active_by_default") is False:
        return False
    if unit.get("expose_to_user") is False:
        return False
    return True


def _candidate_spans(source_span: str, text: str | None = None) -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        cleaned = str(value or "").strip(" \t\r\n.;:")
        if len(cleaned) >= 8 and cleaned not in candidates:
            candidates.append(cleaned)

    add(source_span)
    pieces = [source_span]
    for separator in ["...", "\u2026", ";", "\n", " returns ", " raises ", " with "]:
        expanded: list[str] = []
        for piece in pieces:
            expanded.extend(piece.split(separator))
        pieces.extend(expanded)
    for piece in pieces:
        add(piece)
    if text:
        add(text)
        words = re.findall(r"[A-Za-z0-9_`'./:&%+=<>-]+", str(text))
        for size in range(min(12, len(words)), 3, -1):
            for start in range(0, len(words) - size + 1):
                add(" ".join(words[start : start + size]))
    return sorted(candidates, key=len, reverse=True)


def _normalized_substring_match(needle: str, source_text: str) -> str | None:
    normalized_source, source_map = normalized_source_text_with_map(source_text)
    normalized_needle = normalized_source_text(needle)
    if not normalized_needle or not normalized_source:
        return None
    position = normalized_source.find(normalized_needle)
    if position < 0:
        return None
    start = source_map[position]
    end = source_map[position + len(normalized_needle) - 1] + 1
    return str(source_text)[start:end].strip()


def _source_tokens_with_spans(source_text: str) -> list[tuple[str, int, int]]:
    normalized_source, source_map = normalized_source_text_with_map(source_text)
    tokens: list[tuple[str, int, int]] = []
    for match in re.finditer(r"\S+", normalized_source):
        token = match.group(0).strip(".,;:!?()[]{}\"'")
        if not token:
            continue
        start = source_map[match.start()]
        end = source_map[match.end() - 1] + 1
        tokens.append((token, start, end))
    return tokens


def _fuzzy_match(needle: str, source_text: str) -> tuple[str | None, float]:
    needle_norm = normalized_source_text(needle)
    needle_tokens = [token.strip(".,;:!?()[]{}\"'") for token in re.findall(r"\S+", needle_norm)]
    needle_tokens = [token for token in needle_tokens if token]
    if len(needle_tokens) < 4:
        return None, 0.0
    source_tokens = _source_tokens_with_spans(source_text)
    if not source_tokens:
        return None, 0.0
    best_span: str | None = None
    best_score = 0.0
    target_len = len(needle_tokens)
    min_size = max(4, int(target_len * 0.75))
    max_size = min(len(source_tokens), max(target_len + 4, int(target_len * 1.35)))
    needle_set = set(needle_tokens)
    for size in range(min_size, max_size + 1):
        for start_index in range(0, len(source_tokens) - size + 1):
            window = source_tokens[start_index : start_index + size]
            window_tokens = [item[0] for item in window]
            window_text = " ".join(window_tokens)
            seq_score = SequenceMatcher(None, needle_norm, window_text).ratio()
            overlap = len(needle_set & set(window_tokens)) / max(len(needle_set), 1)
            score = min(seq_score, overlap)
            if score > best_score:
                start = window[0][1]
                end = window[-1][2]
                best_span = str(source_text)[start:end].strip()
                best_score = score
    if best_span and best_score >= 0.88:
        return best_span, best_score
    return None, best_score


def find_source_span_match(source_span: Any, source_text: Any, *, fact_text: Any = "") -> dict[str, Any]:
    raw_span = str(source_span or "").strip()
    haystack = str(source_text or "")
    if not raw_span:
        return {"status": "empty", "matched_span": "", "score": 0.0}
    if not haystack:
        return {"status": "invalid_source", "matched_span": "", "score": 0.0}
    if raw_span in haystack:
        return {"status": "exact", "matched_span": raw_span, "score": 1.0}
    stripped = raw_span.strip()
    if stripped and stripped in haystack:
        return {"status": "exact", "matched_span": stripped, "score": 1.0}

    best_fuzzy: tuple[str | None, float] = (None, 0.0)
    for candidate in _candidate_spans(raw_span, str(fact_text or "")):
        matched = _normalized_substring_match(candidate, haystack)
        if matched:
            return {"status": "normalized", "matched_span": matched, "score": 1.0}
        fuzzy_span, fuzzy_score = _fuzzy_match(candidate, haystack)
        if fuzzy_score > best_fuzzy[1]:
            best_fuzzy = (fuzzy_span, fuzzy_score)
    if best_fuzzy[0]:
        return {"status": "fuzzy", "matched_span": best_fuzzy[0], "score": round(best_fuzzy[1], 3)}
    return {"status": "unmatched", "matched_span": "", "score": round(best_fuzzy[1], 3)}


def summarize_source_matches(units: list[Any]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    repaired = 0
    unmatched = 0
    for unit in units:
        if not isinstance(unit, dict):
            continue
        status = str(unit.get("source_match_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        original = str(unit.get("original_source_span") or unit.get("source_span") or "")
        current = str(unit.get("source_span") or "")
        if status in SOURCE_MATCH_OK and original and current and original != current:
            repaired += 1
        if status in SOURCE_MATCH_UNMATCHED:
            unmatched += 1
    return {
        "source_span_repaired_count": repaired,
        "source_span_unmatched_count": unmatched,
        "source_match_status": status_counts,
    }
