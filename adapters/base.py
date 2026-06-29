"""Shared adapter interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AgentResult:
    patch: str
    trace: str
    metadata: dict[str, object]


class AgentAdapter(Protocol):
    name: str

    def run(self, task: dict[str, object], repo_path: Path) -> AgentResult:
        """Run the agent on a task and return its patch and trace."""
        ...
