"""Adapter placeholder for Claude Code."""

from __future__ import annotations

from pathlib import Path

from adapters.base import AgentResult


class ClaudeCodeAdapter:
    name = "claude_code"

    def run(self, task: dict[str, object], repo_path: Path) -> AgentResult:
        raise NotImplementedError("Claude Code adapter execution is not configured yet.")
