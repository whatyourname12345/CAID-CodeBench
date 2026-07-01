from __future__ import annotations

from cair_v2.staged.schemas import StagedConstructionResult


DIALOGUE_FALLBACK_STEPS = {"dialogue_skeleton", "utterance_realization"}


def has_revision_support(capsule: dict) -> bool:
    support = capsule.get("revision_support") if isinstance(capsule.get("revision_support"), dict) else {}
    return support.get("has_revision_fact") is True and bool(support.get("revision_unit_ids"))


def should_use_template_fallback(result: StagedConstructionResult) -> bool:
    if result.ok:
        return False
    if result.failed_step not in DIALOGUE_FALLBACK_STEPS:
        return False
    return has_revision_support(result.semantic_capsule)

