"""Trace recording utilities."""

from __future__ import annotations

import json
from pathlib import Path

from harness.schemas import TraceEvent, to_jsonable


class TraceRecorder:
    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    def append(
        self,
        actor: str,
        event_type: str,
        content: str = "",
        turn_id: int | None = None,
        tool: str | None = None,
        command: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            event_id=len(self._events) + 1,
            turn_id=turn_id if turn_id is not None else len(self._events) + 1,
            actor=actor,  # type: ignore[arg-type]
            event_type=event_type,  # type: ignore[arg-type]
            content=content,
            tool=tool,
            command=command,
            metadata=dict(metadata or {}),
        )
        self._events.append(event)
        return event

    def write_jsonl(self, path: str | Path) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            for event in self._events:
                file.write(json.dumps(to_jsonable(event), ensure_ascii=False) + "\n")


def load_trace(path: str | Path) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                events.append(TraceEvent(**json.loads(line)))
    return events
