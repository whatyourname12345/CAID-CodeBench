from __future__ import annotations

from cair_v2.staged.schemas import StagedConstructionResult


def should_use_template_fallback(result: StagedConstructionResult) -> bool:
    return False
