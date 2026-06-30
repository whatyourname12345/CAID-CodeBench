from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass
class ParsedModelOutput:
    ok: bool
    data: Any | None
    format: str | None
    error: str | None = None
    extracted_text: str | None = None


CODE_BLOCK_RE = re.compile(r"```(?P<lang>json|yaml|yml)?\s*(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)


def strip_code_fence(text: str) -> str:
    match = CODE_BLOCK_RE.search(text)
    if match:
        return match.group("body").strip()
    return text.strip()


def code_fence_language(text: str) -> str | None:
    match = CODE_BLOCK_RE.search(text)
    if not match:
        return None
    lang = match.group("lang")
    return lang.lower() if lang else None


def balanced_json_candidate(text: str) -> str | None:
    starts = [index for index, char in enumerate(text) if char in "[{"]
    for start in starts:
        stack: list[str] = []
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in "{[":
                stack.append("}" if char == "{" else "]")
            elif char in "}]":
                if not stack or char != stack[-1]:
                    break
                stack.pop()
                if not stack:
                    return text[start : index + 1]
    return None


def yaml_candidate(text: str) -> str:
    fenced = strip_code_fence(text)
    # Drop leading prose before the first likely YAML mapping/list line.
    lines = fenced.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:", stripped) or stripped.startswith("- "):
            return "\n".join(lines[index:]).strip()
    return fenced


def sanitize_yaml_text(text: str) -> str:
    # Unquoted issue references such as #22414 are legal in prose but start YAML
    # comments. Normalize them before parsing so line continuations remain intact.
    return re.sub(r"#(\d+)", r"issue-\1", text)


def parse_model_output(raw_text: str) -> ParsedModelOutput:
    fenced = strip_code_fence(raw_text)
    fence_lang = code_fence_language(raw_text)

    if fence_lang in {"yaml", "yml"}:
        yml = sanitize_yaml_text(yaml_candidate(raw_text))
        try:
            data = yaml.safe_load(yml)
            if data is None:
                raise ValueError("YAML parsed to null")
            if not isinstance(data, (dict, list)):
                raise ValueError(f"YAML parsed to {type(data).__name__}, expected mapping or list")
            return ParsedModelOutput(ok=True, data=data, format="yaml", extracted_text=yml)
        except Exception as exc:
            # Continue to JSON fallback only if the explicitly fenced YAML is malformed.
            yaml_error = f"YAML parse failed: {exc}"
    else:
        yaml_error = ""

    json_candidates = [fenced]
    # Avoid extracting incidental JSON fragments like [] from YAML mappings.
    starts_like_full_json = fenced.lstrip().startswith(("{", "["))
    balanced = None if fence_lang in {"yaml", "yml"} or starts_like_full_json else balanced_json_candidate(raw_text)
    if balanced and balanced not in json_candidates:
        json_candidates.append(balanced)

    errors: list[str] = []
    if yaml_error:
        errors.append(yaml_error)
    for candidate in json_candidates:
        try:
            return ParsedModelOutput(ok=True, data=json.loads(candidate), format="json", extracted_text=candidate)
        except Exception as exc:
            errors.append(f"JSON parse failed: {exc}")

    yml = sanitize_yaml_text(yaml_candidate(raw_text))
    try:
        data = yaml.safe_load(yml)
        if data is None:
            raise ValueError("YAML parsed to null")
        if not isinstance(data, (dict, list)):
            raise ValueError(f"YAML parsed to {type(data).__name__}, expected mapping or list")
        return ParsedModelOutput(ok=True, data=data, format="yaml", extracted_text=yml)
    except Exception as exc:
        errors.append(f"YAML parse failed: {exc}")

    return ParsedModelOutput(ok=False, data=None, format=None, error="; ".join(errors), extracted_text=fenced)


def to_yaml_text(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
