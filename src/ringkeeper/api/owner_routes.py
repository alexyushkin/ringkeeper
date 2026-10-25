"""Setup (first run) and the owner's sign-in: password, sessions, passkeys.

All of these need a token (only the web app's server side calls them).
The ones marked with OwnerToken also need a signed-in owner, and the ones
with RecentAuth a recent confirmation (POST /auth/confirm).

Without a signed-in owner, /setup/status tells only what the wizard's first
step needs (is there an owner yet, and where's the setup code).
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ringkeeper import bootstrap
from ringkeeper.api.auth import AdminDep, Caller, caller
from ringkeeper.api.deps import (
    OWNER,
    ClientDep,
    OwnerToken,
    RecentAuth,
    SessionDep,
    WriteSessionDep,
)
from ringkeeper.claude_auth import credential_ref
from ringkeeper.config import get_settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.keys import load_master_key
from ringkeeper.models import AuthMode, Instance, OwnerSession, Passkey, Project, Setting
from ringkeeper.schemas import Input
from ringkeeper.services import admin_audit, owner
from ringkeeper.services.runtime import current_settings
from ringkeeper.vault import get_vault

router = APIRouter(dependencies=[AdminDep])

RECOVERY_SAVED_KEY = "setup.recovery_saved"
RECOVERY_NOTE = "If you lose this file, you'll need to re-enter your connection secrets."


class SessionOut(BaseModel):
    token: str
    expires_at: datetime


class SetupStatus(BaseModel):
    owner: bool
    signed_in: bool
    setup_code_hint: str | None  # where to find the code, while setup isn't done
    # The rest only for a signed-in owner (or the admin token).
    recovery_saved: bool | None = None
    claude: dict[str, Any] | None = None
    telegram: dict[str, Any] | None = None
    projects: int | None = None
    connections: int | None = None


class OwnerIn(Input):
    password: str
    setup_code: str


class PasswordIn(Input):
    password: str


class PasswordChangeIn(Input):
    current: str
    new: str


class PasskeyIn(Input):
    challenge_id: str
    credential: dict[str, Any]
    name: str = Field(default="Passkey", max_length=80)


def _stored(ref: str) -> bool:
    try:
        get_vault().resolve(ref)
    except RingkeeperError:
        return False
    return True


def _too_many(error: owner.TooManyAttemptsError) -> HTTPException:
    return HTTPException(429, detail=str(error), headers={"Retry-After": str(error.retry_after)})


def _signed_in(session: SessionDep, token: str | None) -> bool:
    if not token:
        return False
    try:
        owner.verify_session(session, get_settings(), token)
    except owner.AuthError:
        return False
    return True


@router.get("/setup/status")
def setup_status(
    request: Request,
    session: SessionDep,
    x_ringkeeper_session: Annotated[str | None, Header()] = None,
) -> SetupStatus:
    settings = current_settings()
    has_owner = owner.has_owner(session)
    signed_in = _signed_in(session, x_ringkeeper_session)
    hint = (
        None
        if has_owner
        else (
            "by running `docker compose exec api ringkeeper web setup-code` in the "
            f"ringkeeper folder on the server (it's also in {bootstrap.SETUP_CODE_FILE} "
            "in the .ringkeeper folder)"
        )
    )
    if not signed_in and caller(request) is not Caller.ADMIN:
        return SetupStatus(owner=has_owner, signed_in=False, setup_code_hint=hint)
    recovery = session.get(Setting, RECOVERY_SAVED_KEY)
    return SetupStatus(
        owner=has_owner,
        signed_in=signed_in,
        setup_code_hint=hint,
        recovery_saved=recovery is not None,
        claude={
            "mode": settings.claude_auth_mode.value,
            "api_key": _stored(credential_ref(settings, AuthMode.API_KEY)),
            "subscription": _stored(credential_ref(settings, AuthMode.SUBSCRIPTION)),
        },
        telegram={
            "token": _stored(settings.telegram_bot_token_ref),
            "enabled": settings.telegram,
            "chats": settings.telegram_chat_ids,
        },
        projects=session.scalar(select(func.count()).select_from(Project)) or 0,
        connections=session.scalar(select(func.count()).select_from(Instance)) or 0,
    )


@router.post("/setup/owner", status_code=201)
def create_owner(
    data: OwnerIn,
    session: WriteSessionDep,
    client: ClientDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionOut:
    """Create the owner (once, with the setup code) and sign them in."""
    settings = get_settings()
    try:
        owner.create_owner(session, data.password, data.setup_code, client)
    except owner.TooManyAttemptsError as error:
        session.commit()  # keep the attempt on record
        raise _too_many(error) from None
    except owner.AuthError as error:
        session.commit()
        raise HTTPException(403, detail=str(error)) from None
    bootstrap.clear_setup_code(settings)
    opened = owner.open_session(session, settings, "password", user_agent)
    return SessionOut(token=opened.token, expires_at=opened.expires_at)


@router.post("/setup/recovery-key", dependencies=[RecentAuth])
def recovery_key(session: WriteSessionDep, token: OwnerToken) -> dict[str, str]:
    """The master key, as a file to keep safe. Needs a recent confirmation, so a
    stolen session alone can't take it."""
    del token
    import base64  # noqa: PLC0415

    key = base64.urlsafe_b64encode(load_master_key(get_settings(), create=False)).decode()
    admin_audit.record(session, "recovery_key.download", "downloaded the recovery key", by=OWNER)
    return {"filename": "ringkeeper-recovery-key.txt", "key": key, "note": RECOVERY_NOTE}


@router.post("/setup/recovery-key/saved", status_code=204)
def recovery_key_saved(session: WriteSessionDep, token: OwnerToken) -> None:
    del token
    statement = insert(Setting).values(key=RECOVERY_SAVED_KEY, value={"saved": True})
    session.execute(statement.on_conflict_do_nothing(index_elements=["key"]))


# --- Sign-in ---


@router.post("/auth/login")
def login(
    data: PasswordIn,
    session: WriteSessionDep,
    client: ClientDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionOut:
    try:
        opened = owner.login(session, get_settings(), data.password, client, user_agent)
    except owner.TooManyAttemptsError as error:
        raise _too_many(error) from None
    except owner.AuthError:
        session.commit()  # the failed attempt counts
        raise HTTPException(401, detail="wrong password") from None
    return SessionOut(token=opened.token, expires_at=opened.expires_at)


@router.post("/auth/confirm", status_code=204)
def confirm(
    data: PasswordIn, session: WriteSessionDep, token: OwnerToken, client: ClientDep
) -> None:
    """The password again, before a sensitive change (valid for a few minutes)."""
    try:
        owner.confirm(session, token, data.password, client)
    except owner.TooManyAttemptsError as error:
        raise _too_many(error) from None
    except owner.AuthError:
        session.commit()  # the failed attempt counts
        raise HTTPException(401, detail="wrong password") from None


@router.post("/auth/logout", status_code=204)
def logout(session: WriteSessionDep, token: OwnerToken) -> None:
    owner.logout(session, token)


@router.get("/auth/session")
def current_session(session: SessionDep, token: OwnerToken) -> dict[str, Any]:
    row = session.get(OwnerSession, owner.token_hash(token))
    if row is None:
        raise HTTPException(401, detail="sign in first")
    return {
        "method": row.method,
        "expires_at": row.expires_at,
        "created_at": row.created_at,
        "confirmed": owner.recently_confirmed(session, token),
    }


@router.post("/auth/password", status_code=204)
def change_password(data: PasswordChangeIn, session: WriteSessionDep, token: OwnerToken) -> None:
    try:
        owner.change_password(session, token, data.current, data.new)
    except owner.AuthError as error:
        raise HTTPException(403, detail=str(error)) from None


# --- Passkeys ---


@router.get("/auth/passkeys")
def list_passkeys(session: SessionDep, token: OwnerToken) -> list[dict[str, Any]]:
    del token
    return [
        {
            "id": str(p.id),
            "name": p.name,
            "created_at": p.created_at,
            "last_used_at": p.last_used_at,
        }
        for p in session.scalars(select(Passkey).order_by(Passkey.created_at))
    ]


@router.post("/auth/passkeys/register/options", dependencies=[RecentAuth])
def passkey_register_options(session: WriteSessionDep, token: OwnerToken) -> dict[str, Any]:
    del token
    return owner.passkey_registration_options(session, get_settings())


@router.post("/auth/passkeys/register", status_code=201, dependencies=[RecentAuth])
def passkey_register(
    data: PasskeyIn, session: WriteSessionDep, token: OwnerToken
) -> dict[str, str]:
    del token
    try:
        passkey = owner.register_passkey(
            session, get_settings(), data.challenge_id, data.credential, data.name
        )
    except RuleViolationError as error:
        session.commit()  # the challenge is used up either way
        raise HTTPException(409, detail=str(error)) from None
    return {"id": str(passkey.id), "name": passkey.name}


@router.delete("/auth/passkeys/{ident}", status_code=204)
def passkey_remove(ident: str, session: WriteSessionDep, token: OwnerToken) -> None:
    del token
    owner.remove_passkey(session, ident)


@router.post("/auth/passkeys/login/options")
def passkey_login_options(session: WriteSessionDep) -> dict[str, Any]:
    return owner.passkey_login_options(session, get_settings())


@router.post("/auth/passkeys/login")
def passkey_login(
    data: PasskeyIn,
    session: WriteSessionDep,
    client: ClientDep,
    user_agent: Annotated[str | None, Header()] = None,
) -> SessionOut:
    try:
        opened = owner.passkey_login(
            session, get_settings(), data.challenge_id, data.credential, client, user_agent
        )
    except owner.TooManyAttemptsError as error:
        raise _too_many(error) from None
    except owner.AuthError as error:
        session.commit()
        raise HTTPException(401, detail=str(error)) from None
    return SessionOut(token=opened.token, expires_at=opened.expires_at)
