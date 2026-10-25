"""Setup (first run) and the owner's sign-in: password, sessions, passkeys.

All of these need the admin token (only the web app's server side calls
them). The ones marked with OwnerToken also need a signed-in owner.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ringkeeper import bootstrap
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OWNER, ClientDep, OwnerToken, SessionDep, WriteSessionDep
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
    recovery_saved: bool
    claude: dict[str, Any]
    telegram: dict[str, Any]
    projects: int
    connections: int
    setup_code_hint: str | None  # where to find the code, while setup isn't done


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


class RecoveryKeyIn(Input):
    password: str | None = None


def _stored(ref: str) -> bool:
    try:
        get_vault().resolve(ref)
    except RingkeeperError:
        return False
    return True


def _too_many(error: owner.TooManyAttemptsError) -> HTTPException:
    return HTTPException(429, detail=str(error), headers={"Retry-After": str(error.retry_after)})


@router.get("/setup/status")
def setup_status(session: SessionDep) -> SetupStatus:
    settings = current_settings()
    recovery = session.get(Setting, RECOVERY_SAVED_KEY)
    has_owner = owner.has_owner(session)
    return SetupStatus(
        owner=has_owner,
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
        setup_code_hint=None
        if has_owner
        else (
            "by running `docker compose exec api ringkeeper web setup-code` in the "
            f"ringkeeper folder on the server (it's also in {bootstrap.SETUP_CODE_FILE} "
            "in the .ringkeeper folder)"
        ),
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


@router.post("/setup/recovery-key")
def recovery_key(
    data: RecoveryKeyIn, session: WriteSessionDep, token: OwnerToken
) -> dict[str, str]:
    """The master key, as a file to keep safe. After the first download the password
    is needed again, so a stolen session alone can't take it."""
    del token
    saved = session.get(Setting, RECOVERY_SAVED_KEY) is not None
    if saved:
        row = session.get(owner.Owner, 1)
        if row is None or not data.password or not owner.verify_password(row, data.password):
            raise HTTPException(403, detail="enter your password to download the recovery key")
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


@router.post("/auth/logout", status_code=204)
def logout(session: WriteSessionDep, token: OwnerToken) -> None:
    owner.logout(session, token)


@router.get("/auth/session")
def current_session(session: SessionDep, token: OwnerToken) -> dict[str, Any]:
    row = session.get(OwnerSession, owner.token_hash(token))
    if row is None:
        raise HTTPException(401, detail="sign in first")
    return {"method": row.method, "expires_at": row.expires_at, "created_at": row.created_at}


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


@router.post("/auth/passkeys/register/options")
def passkey_register_options(session: WriteSessionDep, token: OwnerToken) -> dict[str, Any]:
    del token
    return owner.passkey_registration_options(session, get_settings())


@router.post("/auth/passkeys/register", status_code=201)
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
