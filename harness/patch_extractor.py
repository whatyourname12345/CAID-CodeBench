"""Utilities for extracting unified diffs from agent traces."""

from __future__ import annotations

import re


_DIFF_START = re.compile(r"^(diff --git |--- |\*\*\* Begin Patch)", re.MULTILINE)


def extract_patch(text: str) -> str:
    """Return the first patch-like block from text.

    If no explicit patch marker is found, an empty string is returned.
    """
    match = _DIFF_START.search(text)
    if not match:
        return ""
    return text[match.start() :].strip() + "\n"
