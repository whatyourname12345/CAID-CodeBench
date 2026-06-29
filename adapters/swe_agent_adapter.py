"""Adapter placeholder for SWE-agent."""

from __future__ import annotations

from pathlib import Path

from adapters.base import AgentResult


class SWEAgentAdapter:
    name = "swe_agent"

    def run(self, task: dict[str, object], repo_path: Path) -> AgentResult:
        raise NotImplementedError("SWE-agent adapter execution is not configured yet.")
