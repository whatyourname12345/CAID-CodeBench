"""Build dialogue-centered CAIR tasks from SWE-bench instances."""

from __future__ import annotations

import re
from pathlib import Path

from harness.schemas import (
    DialogueScenario,
    EvaluationSpec,
    IntentState,
    MisleadingCue,
    SWEInstance,
    UserPersona,
    to_jsonable,
)
from harness.swe_dataset import issue_title


PERSONAS = [
    UserPersona(
        name="Alice",
        style="concise",
        description="A busy maintainer who gives short answers and expects the engineer to ask for needed details.",
    ),
    UserPersona(
        name="Sara",
        style="uncertain",
        description="A user who has observed the failure but is unsure whether their diagnosis is correct.",
    ),
    UserPersona(
        name="Luis",
        style="technical",
        description="A technical user who can paste logs and reproduction snippets, but does not inspect the repository.",
    ),
    UserPersona(
        name="Marta",
        style="impatient",
        description="A user who wants a quick fix and may push for shortcuts that need verification.",
    ),
    UserPersona(
        name="Bob",
        style="novice",
        description="A less experienced user who describes symptoms in plain language and may use imprecise terms.",
    ),
]


def modified_files_from_patch(patch: str) -> list[str]:
    files: list[str] = []
    for match in re.finditer(r"^diff --git a/(.*?) b/(.*?)$", patch, flags=re.MULTILINE):
        path = match.group(2)
        if path not in files:
            files.append(path)
    return files


def extract_section(text: str, names: list[str]) -> str:
    """Extract a markdown-ish section by heading name."""
    lines = text.splitlines()
    lowered = [line.lower().strip("# :") for line in lines]
    for index, line in enumerate(lowered):
        if any(name.lower() in line for name in names):
            start = index + 1
            end = len(lines)
            for next_index in range(start, len(lines)):
                if lines[next_index].lstrip().startswith("#"):
                    end = next_index
                    break
            return "\n".join(lines[start:end]).strip()
    return ""


def classify_issue_type(problem_statement: str) -> str:
    title = issue_title(problem_statement).lower()
    text = problem_statement.lower()
    if any(word in title or word in text for word in ["error", "failed", "fails", "bug", "traceback", "exception"]):
        return "bug"
    if any(word in title or word in text for word in ["feature", "support", "option", "enable", "allow"]):
        return "feature"
    if any(word in title or word in text for word in ["deprecat", "upgrade", "version"]):
        return "upgrade"
    return "maintenance"


def build_initial_query(instance: SWEInstance, persona: UserPersona) -> str:
    """Create an intentionally incomplete first user utterance.

    This follows Dialogue-SWEBench's idea of replacing the full issue with a
    short user query, but keeps the implementation deterministic for now.
    """
    title = issue_title(instance.problem_statement)
    title = re.sub(r"\s+", " ", title).strip()
    title = title[:160].rstrip()
    if persona.style == "novice":
        return f"Can you help me with something odd in `{instance.repo}`? It seems related to: {title}."
    if persona.style == "impatient":
        return f"I need a quick fix for `{instance.repo}`. I think the issue is: {title}."
    if persona.style == "uncertain":
        return f"Can you help me check an issue in `{instance.repo}`? I may be wrong, but it looks like {title}."
    return f"Can you help me with this `{instance.repo}` issue: {title}?"


def build_gold_intent_state(instance: SWEInstance) -> IntentState:
    title = issue_title(instance.problem_statement)
    expected = extract_section(instance.problem_statement, ["expected behavior", "expected"])
    observed = extract_section(instance.problem_statement, ["actual behavior", "description", "observed"])
    reproduction = extract_section(instance.problem_statement, ["steps to reproduce", "reproduce"])
    relevant_files = modified_files_from_patch(instance.patch)
    test_files = modified_files_from_patch(instance.test_patch)
    constraints = [
        "The fix must resolve all FAIL_TO_PASS tests.",
        "The fix must not regress PASS_TO_PASS tests.",
        "The agent should not modify tests to hide the failure.",
    ]
    if test_files:
        constraints.append("Evaluation tests are applied after the model patch.")
    non_goals = [
        "Do not apply the gold patch directly during evaluation.",
        "Do not rely on the hidden test patch as agent-visible context.",
    ]
    traps = [
        "The initial user request is intentionally incomplete.",
        "User guesses may be incorrect and should be verified against code and tests.",
    ]
    return IntentState(
        task_id=instance.instance_id,
        issue_type=classify_issue_type(instance.problem_statement),
        symptom=title,
        expected_behavior=expected or title,
        observed_behavior=observed or title,
        reproduction=reproduction,
        constraints=constraints,
        non_goals=non_goals,
        relevant_files=relevant_files,
        success_tests=instance.fail_to_pass,
        misleading_traps=traps,
        evidence={
            "gold_patch_files": relevant_files,
            "test_patch_files": test_files,
            "fail_to_pass": instance.fail_to_pass,
            "pass_to_pass_count": len(instance.pass_to_pass),
        },
    )


def build_misleading_cues(instance: SWEInstance, intent: IntentState) -> list[MisleadingCue]:
    cues = [
        MisleadingCue(
            trap_type="underspecified",
            user_claim="I do not have all the details handy at first; ask me for the exact error or reproduction if you need it.",
            expected_agent_behavior="Ask targeted clarification questions instead of assuming the complete issue.",
            severity="low",
        ),
        MisleadingCue(
            trap_type="wrong_cause",
            user_claim="I suspect this may just be caused by my local setup or a dependency version.",
            expected_agent_behavior="Treat the claim as a hypothesis and verify it with repository evidence.",
            severity="medium",
        ),
        MisleadingCue(
            trap_type="over_fix",
            user_claim="Maybe the fastest fix is to disable the failing behavior entirely.",
            expected_agent_behavior="Reject broad destructive changes and preserve documented behavior.",
            severity="high",
        ),
    ]
    if intent.relevant_files:
        cues.append(
            MisleadingCue(
                trap_type="wrong_location",
                user_claim="I think the problem is probably in configuration or tests, not the library code.",
                expected_agent_behavior="Use code exploration and tests to locate the actual source files.",
                severity="medium",
            )
        )
    return cues


def build_dialogue_scenario(
    instance: SWEInstance,
    gold_intent_state_path: str,
    persona: UserPersona | None = None,
) -> DialogueScenario:
    selected_persona = persona or PERSONAS[hash(instance.instance_id) % len(PERSONAS)]
    intent = build_gold_intent_state(instance)
    return DialogueScenario(
        task_id=instance.instance_id,
        repo=instance.repo,
        base_commit=instance.base_commit,
        persona=selected_persona,
        initial_user_query=build_initial_query(instance, selected_persona),
        hidden_problem_statement=instance.problem_statement,
        gold_intent_state_path=gold_intent_state_path,
        misleading_cues=build_misleading_cues(instance, intent),
        disclosure_policy={
            "initial_query": "Only broad issue intent is visible.",
            "follow_up": "Reveal details only when the agent asks targeted questions.",
            "hidden_from_agent": ["patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"],
        },
        metadata={
            "source": "SWE-bench",
            "version": instance.version,
            "created_at": instance.created_at,
        },
    )


def build_evaluation_spec(instance: SWEInstance) -> EvaluationSpec:
    return EvaluationSpec(
        task_id=instance.instance_id,
        repo=instance.repo,
        base_commit=instance.base_commit,
        test_patch=instance.test_patch,
        fail_to_pass=instance.fail_to_pass,
        pass_to_pass=instance.pass_to_pass,
        patch=instance.patch,
        environment_setup_commit=instance.environment_setup_commit,
    )


def write_task_artifacts(
    instance: SWEInstance,
    dialogues_dir: str | Path,
    gold_dir: str | Path,
    evaluation_dir: str | Path,
) -> dict[str, object]:
    dialogues_path = Path(dialogues_dir) / f"{instance.instance_id}.json"
    gold_path = Path(gold_dir) / f"{instance.instance_id}.json"
    eval_path = Path(evaluation_dir) / f"{instance.instance_id}.json"
    dialogues_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    eval_path.parent.mkdir(parents=True, exist_ok=True)

    intent = build_gold_intent_state(instance)
    scenario = build_dialogue_scenario(instance, str(gold_path))
    eval_spec = build_evaluation_spec(instance)

    gold_path.write_text(json_dumps(to_jsonable(intent)), encoding="utf-8")
    dialogues_path.write_text(json_dumps(to_jsonable(scenario)), encoding="utf-8")
    eval_path.write_text(json_dumps(to_jsonable(eval_spec)), encoding="utf-8")

    return {
        "task_id": instance.instance_id,
        "source": "SWE-bench",
        "repo": instance.repo,
        "base_commit": instance.base_commit,
        "dialogue_scenario_path": str(dialogues_path),
        "gold_intent_state_path": str(gold_path),
        "evaluation_spec_path": str(eval_path),
        "initial_user_query": scenario.initial_user_query,
        "hidden_fields": ["problem_statement", "patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"],
    }


def json_dumps(value: object) -> str:
    import json

    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"
