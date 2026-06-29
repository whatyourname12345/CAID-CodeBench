"""Dialogue turn control for interactive coding agents."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Turn:
    role: str
    content: str


@dataclass
class TurnController:
    max_turns: int = 20
    turns: list[Turn] = field(default_factory=list)

    def add_turn(self, role: str, content: str) -> None:
        if len(self.turns) >= self.max_turns:
            raise RuntimeError(f"maximum turn count exceeded: {self.max_turns}")
        self.turns.append(Turn(role=role, content=content))

    def transcript(self) -> list[dict[str, str]]:
        return [{"role": turn.role, "content": turn.content} for turn in self.turns]
