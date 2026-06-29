"""User simulator interfaces for dialogue-driven SWE tasks."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

from harness.schemas import DialogueScenario, TraceEvent


class UserSimulator(ABC):
    """Interface for simulated users.

    Implementations may be deterministic, LLM-backed, or human-in-the-loop.
    """

    @abstractmethod
    def initial_message(self, scenario: DialogueScenario) -> str:
        """Return the first user message."""

    @abstractmethod
    def reply(self, scenario: DialogueScenario, trace: list[TraceEvent], agent_message: str) -> str:
        """Return a follow-up user message for the latest agent message."""


class RuleBasedUserSimulator(UserSimulator):
    """Small deterministic simulator used for smoke tests and offline plumbing.

    It does not replace the LLM simulator. It provides enough behavior for
    framework tests without API access.
    """

    def initial_message(self, scenario: DialogueScenario) -> str:
        return scenario.initial_user_query

    def reply(self, scenario: DialogueScenario, trace: list[TraceEvent], agent_message: str) -> str:
        problem = scenario.hidden_problem_statement
        message = agent_message.lower()
        if any(word in message for word in ["reproduce", "repro", "steps", "example", "code"]):
            return extract_reproduction(problem) or "I do not have a smaller repro handy beyond what I already described."
        if any(word in message for word in ["error", "traceback", "exception", "stack"]):
            return extract_error(problem) or "I do not have the full traceback handy."
        if any(word in message for word in ["expected", "want", "should"]):
            return extract_expected(problem) or "I mainly want the issue fixed without breaking existing behavior."
        if any(word in message for word in ["run", "test", "verify"]):
            return "I cannot run code right now; please run the relevant tests in the repository."
        if is_check_in(agent_message):
            return "Sounds good."
        return "I am not sure. What specific detail would help you?"


def extract_reproduction(problem_statement: str) -> str:
    return extract_section(problem_statement, ["steps to reproduce", "reproduce", "code to reproduce"])


def extract_expected(problem_statement: str) -> str:
    return extract_section(problem_statement, ["expected behavior", "expected"])


def extract_error(problem_statement: str) -> str:
    fence = re.search(r"```(.*?)```", problem_statement, flags=re.DOTALL)
    if fence and any(token in fence.group(1).lower() for token in ["error", "traceback", "exception", "failed"]):
        return fence.group(1).strip()
    lines = [
        line
        for line in problem_statement.splitlines()
        if any(token in line.lower() for token in ["error", "traceback", "exception", "failed", "fail"])
    ]
    return "\n".join(lines[:12]).strip()


def extract_section(text: str, names: list[str]) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        normalized = line.lower().strip("# :")
        if any(name in normalized for name in names):
            start = index + 1
            end = len(lines)
            for next_index in range(start, len(lines)):
                if lines[next_index].lstrip().startswith("#"):
                    end = next_index
                    break
            return "\n".join(lines[start:end]).strip()
    return ""


def is_check_in(message: str) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in ["i will", "i'm going to", "i found", "i think", "next i"])


def load_scenario(path: str | Path) -> DialogueScenario:
    from harness.schemas import MisleadingCue, UserPersona

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    persona = UserPersona(**data["persona"])
    cues = [MisleadingCue(**item) for item in data.get("misleading_cues", [])]
    return DialogueScenario(
        task_id=data["task_id"],
        repo=data["repo"],
        base_commit=data["base_commit"],
        persona=persona,
        initial_user_query=data["initial_user_query"],
        hidden_problem_statement=data["hidden_problem_statement"],
        gold_intent_state_path=data["gold_intent_state_path"],
        misleading_cues=cues,
        disclosure_policy=data.get("disclosure_policy", {}),
        metadata=data.get("metadata", {}),
    )
