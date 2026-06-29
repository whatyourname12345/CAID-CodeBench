"""Self-revision hooks for simulated user messages."""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.schemas import DialogueScenario


@dataclass(frozen=True)
class RevisionIssue:
    violation_type: str
    explanation: str


@dataclass
class RevisionResult:
    original: str
    revised: str
    issues: list[RevisionIssue] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original != self.revised


class UserRevisionPolicy:
    """Detect and repair obvious simulated-user rule violations.

    LLM-backed self-revision should implement the same interface and can use
    `prompts/user_self_revision.md`.
    """

    def revise(self, scenario: DialogueScenario, candidate: str) -> RevisionResult:
        issues: list[RevisionIssue] = []
        revised = candidate.strip()

        lower = revised.lower()
        if len(revised.split()) > 180:
            issues.append(RevisionIssue("REMAIN_CONCISE", "User reply is too long for a realistic chat turn."))
            revised = " ".join(revised.split()[:120])

        if any(phrase in lower for phrase in ["i ran", "i will run", "i'll run", "i can test that"]):
            issues.append(RevisionIssue("BREAKING_ENVIRONMENT", "User claimed they can run code or verify changes."))
            revised = "I cannot run code right now; please run the relevant check on your side."

        if "problem statement" in lower or "as a simulator" in lower:
            issues.append(RevisionIssue("BREAKING_IMMERSION", "User exposed benchmark or simulator context."))
            revised = "I can only share the details I have from seeing the issue."

        return RevisionResult(original=candidate, revised=revised, issues=issues)
