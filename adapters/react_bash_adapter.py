"""Adapter placeholder for a ReAct bash agent."""

from __future__ import annotations

from pathlib import Path

from adapters.base import AgentResult


class ReActBashAdapter:
    name = "react_bash"

    def run(self, task: dict[str, object], repo_path: Path) -> AgentResult:
        raise NotImplementedError("ReAct bash adapter execution is not configured yet.")
