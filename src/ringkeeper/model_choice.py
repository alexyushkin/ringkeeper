"""Which Claude model a session runs on.

Roles: the router, the architect, and workers. A worker the router dispatched
has a complexity (low, medium, high) that picks its tier; one without (from
`task run`) uses the worker model. After two failed check rounds in the
architect loop the worker moves up one tier. The architect plans on its own
model, except for high-complexity tasks, which plan on the high tier (low
ones skip planning: services.tasks.planned). A project's `models` overrides
any role or tier for that project.
"""

from ringkeeper.config import Settings
from ringkeeper.models import Complexity, Project, SessionRole

TIERS = (Complexity.LOW, Complexity.MEDIUM, Complexity.HIGH)
ESCALATE_AFTER_FAILED_ROUNDS = 2


def _setting(settings: Settings, key: str) -> str:
    return str(getattr(settings, f"model_{key}"))


def _pick(settings: Settings, project: Project | None, key: str) -> str:
    override = (project.models if project is not None else {}).get(key)
    return override or _setting(settings, key)


def router_model(settings: Settings) -> str:
    return settings.model_router


def tier(complexity: Complexity | None, failed_check_rounds: int = 0) -> Complexity | None:
    """The worker's tier after escalation. None means the plain worker model."""
    if failed_check_rounds < ESCALATE_AFTER_FAILED_ROUNDS:
        return complexity
    current = TIERS.index(complexity) if complexity is not None else TIERS.index(Complexity.MEDIUM)
    return TIERS[min(current + 1, len(TIERS) - 1)]


def session_model(
    settings: Settings,
    project: Project | None,
    role: SessionRole,
    complexity: Complexity | None = None,
    failed_check_rounds: int = 0,
) -> str:
    if role is SessionRole.ARCHITECT:
        chosen_for_project = (project.models if project is not None else {}).get("architect")
        if complexity is Complexity.HIGH and not chosen_for_project:
            return _pick(settings, project, "high")
        return _pick(settings, project, "architect")
    chosen = tier(complexity, failed_check_rounds)
    return _pick(settings, project, chosen.value if chosen is not None else "worker")
