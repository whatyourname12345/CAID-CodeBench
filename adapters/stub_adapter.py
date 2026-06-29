"""Deterministic adapter used for framework smoke tests."""

from __future__ import annotations

from pathlib import Path

from adapters.base import AgentResult


class StubAdapter:
    name = "stub"

    def run(self, task: dict[str, object], repo_path: Path) -> AgentResult:
        query = str(task.get("initial_user_query", ""))
        trace = (
            f"I received the user request: {query}\n"
            "I would ask for reproduction details, inspect relevant files, run targeted tests, "
            "and then submit a minimal patch."
        )
        return AgentResult(
            patch="",
            trace=trace,
            metadata={
                "trace_events": [
                    {
                        "actor": "agent",
                        "event_type": "message",
                        "content": "Can you share the exact reproduction steps and expected behavior?",
                        "turn_id": 2,
                    },
                    {
                        "actor": "agent",
                        "event_type": "tool_call",
                        "tool": "bash",
                        "command": "rg -n \"TODO|error|quiet\" .",
                        "turn_id": 3,
                    },
                ]
            },
        )
