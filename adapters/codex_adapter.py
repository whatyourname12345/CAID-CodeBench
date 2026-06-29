"""Adapter placeholder for Codex."""

from __future__ import annotations

from pathlib import Path

from adapters.base import AgentResult


class CodexAdapter:
    name = "codex"

    def run(self, task: dict[str, object], repo_path: Path) -> AgentResult:
        raise NotImplementedError("Codex adapter execution is not configured yet.")
