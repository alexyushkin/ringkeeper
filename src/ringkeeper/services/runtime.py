"""Settings you can change while Ringkeeper runs (the web app's Settings screen).

`.env` gives the defaults; values saved here win over it. Only the keys in
`RuntimeIn` can be changed this way: paths, URLs, the database and secret
refs stay in the environment. A change is audited and announced with NOTIFY,
and the api's supervisor applies it without a restart (it restarts the
Telegram bridge or the router when their settings change; the session
manager reads the effective settings on every tick).
"""

import logging
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ringkeeper.config import Effort, Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import AuthMode, Setting
from ringkeeper.schemas import Input, ModelId, WorkspaceId
from ringkeeper.services import admin_audit
from ringkeeper.storage import events
from ringkeeper.storage.sql import insert
from ringkeeper.voice import AUTO, LANGUAGES

logger = logging.getLogger(__name__)

RUNTIME_KEY = "runtime"
SETTINGS_CHANNEL = "ringkeeper_settings"

Positive = Annotated[int, Field(ge=1)]


def _spoken(code: str) -> str:
    if code != AUTO and code not in LANGUAGES:
        raise ValueError("isn't a language the speech model knows")
    return code


SpokenLanguage = Annotated[str, AfterValidator(_spoken)]


class RuntimeIn(Input):
    """Every field optional: a PATCH changes only what it names. null resets to .env."""

    claude_auth_mode: AuthMode | None = None
    allow_subscription: bool | None = None
    subscription_source: Literal["setup-token", "claude-code"] | None = None
    anthropic_workspace_id: WorkspaceId | None = None
    telegram: bool | None = None
    telegram_chat_ids: list[int] | None = None
    telegram_requests: bool | None = None
    telegram_summary_chars: Annotated[int, Field(ge=40, le=1000)] | None = None
    telegram_question_chars: Annotated[int, Field(ge=160, le=3500)] | None = None
    telegram_confirm_minutes: Annotated[int, Field(ge=1, le=120)] | None = None
    model_router: ModelId | None = None
    model_architect: ModelId | None = None
    model_worker: ModelId | None = None
    model_low: ModelId | None = None
    model_medium: ModelId | None = None
    model_high: ModelId | None = None
    effort_router: Effort | None = None
    effort_architect: Effort | None = None
    effort_worker: Effort | None = None
    task_budget_usd: Annotated[float, Field(gt=0, le=1000)] | None = None
    default_max_turns: Annotated[int, Field(ge=1, le=1000)] | None = None
    max_concurrent_workers: Annotated[int, Field(ge=1, le=64)] | None = None
    retention_days: Annotated[int, Field(ge=1, le=3650)] | None = None
    paused: bool | None = None
    start_at_login: bool | None = None  # desktop mode only
    check_updates: bool | None = None  # desktop mode only
    stt_language: SpokenLanguage | None = None  # a Whisper code, or "auto"


EDITABLE = tuple(RuntimeIn.model_fields)


def overrides(session: Session) -> dict[str, Any]:
    row = session.get(Setting, RUNTIME_KEY)
    saved = dict(row.value) if row else {}
    # Drop anything no longer editable (a key removed in a later version).
    return {k: v for k, v in saved.items() if k in EDITABLE}


def apply(base: Settings, saved: dict[str, Any]) -> Settings:
    checked = RuntimeIn.model_validate(saved).model_dump(exclude_none=True)
    return base.model_copy(update=checked)


def effective(session: Session, base: Settings | None = None) -> Settings:
    return apply(base or get_settings(), overrides(session))


def current_settings(base: Settings | None = None) -> Settings:
    """.env plus saved changes. Falls back to .env alone if the database can't be read."""
    base = base or get_settings()
    try:
        with session_scope(commit=False) as session:  # only reads
            return effective(session, base)
    except SQLAlchemyError:
        logger.warning("runtime settings unavailable; using the environment's")
        return base


def update(session: Session, changes: dict[str, Any], *, by: str) -> dict[str, Any]:
    """Merge `changes` (validated by RuntimeIn; None removes an override). Returns all saved."""
    RuntimeIn.model_validate(changes)  # unknown keys and bad values are refused
    saved = overrides(session)
    for key, value in changes.items():
        if value is None:
            saved.pop(key, None)
        else:
            saved[key] = value
    statement = insert(session, Setting).values(key=RUNTIME_KEY, value=saved)
    session.execute(statement.on_conflict_do_update(index_elements=["key"], set_={"value": saved}))
    admin_audit.record(
        session, "settings.update", f"changed settings: {', '.join(sorted(changes))}", by=by,
        details={k: changes[k] for k in sorted(changes)},
    )  # fmt: skip
    events.notify(session, SETTINGS_CHANNEL)
    session.flush()
    return saved
