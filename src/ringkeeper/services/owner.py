"""The web app's single owner account: setup, sign-in, sessions and passkeys.

- Setup: the owner is created once, and only with the one-time setup code the
  api prints on first start (and writes to the data dir). Without it, anyone
  who reached a freshly exposed server first could claim it.
- Passwords are hashed with argon2id; at least 12 characters.
- Sessions are random tokens; only their SHA-256 is stored. They end after
  `owner_session_idle_hours` without use, or `owner_session_max_hours` at most.
- Password and setup-code guesses are slowed down overall and blocked per
  client (when the client's address is known): an attacker can't lock the
  owner out, and a passkey sign-in is never held up.
- Sensitive actions need a recent confirmation (password, or a passkey with
  user verification) on the session: a stolen session cookie alone can't
  add a passkey, take the recovery key or redirect approvals.
- Passkeys (WebAuthn) are optional, need the web app's HTTPS origin and user
  verification, and are all removed when the password changes.
"""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import webauthn
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import DateTime, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import LoginAttempt, Owner, OwnerSession, Passkey, Setting
from ringkeeper.services import admin_audit

SETUP_CODE_KEY = "setup.code"
CHALLENGE_KEY = "webauthn.challenge."
MIN_PASSWORD_CHARS = 12
# Per known client: 5 failures in 15 minutes, then blocked. Overall: after 5
# failures, one guess every few seconds at most (answered "try again shortly",
# never a lockout, and no request is held waiting). Clients without a known
# address ("direct") only get the overall rule.
CLIENT_LIMIT = 5
SLOW_AFTER = 5
SLOW_SECONDS = 3
WINDOW = timedelta(minutes=15)
CONFIRM_FOR = timedelta(minutes=10)  # how long a confirmation lets sensitive actions run
MAX_CHALLENGES = 20
ATTEMPTS_LOCK = 0x52_4B_4C_47  # pg advisory lock: one guess is checked at a time
UNKNOWN_CLIENT = "direct"
CHALLENGE_TTL = timedelta(minutes=5)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
OWNER = "owner"

_hasher = PasswordHasher()


class AuthError(RingkeeperError):
    """Wrong password, unknown session, a bad setup code: always the same vague message."""


class TooManyAttemptsError(RingkeeperError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("too many attempts; try again later")
        self.retry_after = retry_after


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


# --- Setup code ---


def new_setup_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    flat = "".join(c for c in code.upper() if c.isalnum())
    return f"{flat[:4]}-{flat[4:]}"


def store_setup_code(session: Session, code: str) -> None:
    statement = insert(Setting).values(key=SETUP_CODE_KEY, value={"hash": token_hash(code)})
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["key"], set_={"value": statement.excluded.value}
        )
    )


def setup_code_matches(session: Session, code: str) -> bool:
    row = session.get(Setting, SETUP_CODE_KEY)
    if row is None:
        return False
    return hmac.compare_digest(str(row.value.get("hash", "")), token_hash(normalize_code(code)))


# --- Rate limiting ---


def _check_rate(session: Session, client: str) -> None:
    """Before checking a guess: one at a time, block a known client, pace everyone."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ATTEMPTS_LOCK})
    now = _now()
    failed = (LoginAttempt.ok.is_(False), LoginAttempt.at >= now - WINDOW)
    if client != UNKNOWN_CLIENT:
        mine = session.scalar(
            select(func.count()).where(*failed, LoginAttempt.key == f"client:{client}")
        )
        if (mine or 0) >= CLIENT_LIMIT:
            raise TooManyAttemptsError(int(WINDOW.total_seconds()))
    count, last = session.execute(
        select(func.count(), func.max(LoginAttempt.at)).where(*failed)
    ).one()
    if count >= SLOW_AFTER and now - last < timedelta(seconds=SLOW_SECONDS):
        raise TooManyAttemptsError(SLOW_SECONDS)


def _attempt(session: Session, client: str, ok: bool) -> None:
    session.add(LoginAttempt(key=f"client:{client}", ok=ok))
    session.flush()


# --- Owner ---


def has_owner(session: Session) -> bool:
    return session.get(Owner, 1) is not None


def check_password_rules(password: str) -> None:
    if len(password) < MIN_PASSWORD_CHARS:
        raise RuleViolationError(f"use at least {MIN_PASSWORD_CHARS} characters")
    if len(password) > 1024:
        raise RuleViolationError("that password is too long")


def create_owner(session: Session, password: str, setup_code: str, client: str) -> None:
    """The first and only owner. Needs the setup code; the code is gone afterwards."""
    if has_owner(session):
        raise RuleViolationError("setup is already done")
    _check_rate(session, client)
    if not setup_code_matches(session, setup_code):
        _attempt(session, client, False)
        raise AuthError("that setup code isn't right")
    check_password_rules(password)
    session.add(Owner(id=1, password_hash=_hasher.hash(password)))
    session.execute(delete(Setting).where(Setting.key == SETUP_CODE_KEY))
    _attempt(session, client, True)
    admin_audit.record(session, "owner.create", "created the owner account", by=OWNER)
    session.flush()


def verify_password(owner: Owner, password: str) -> bool:
    try:
        return _hasher.verify(owner.password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def change_password(session: Session, current_token: str, old: str, new: str) -> None:
    owner = session.get(Owner, 1)
    if owner is None or not verify_password(owner, old):
        raise AuthError("the current password isn't right")
    check_password_rules(new)
    owner.password_hash = _hasher.hash(new)
    owner.password_changed_at = _now()
    # Every other browser is signed out, and every passkey goes: one added with a
    # stolen session mustn't outlive the password change.
    session.execute(
        delete(OwnerSession).where(OwnerSession.token_hash != token_hash(current_token))
    )
    session.execute(delete(Passkey))
    admin_audit.record(
        session, "owner.password", "changed the password (passkeys removed)", by=OWNER
    )
    session.flush()


def reset_owner(session: Session) -> None:
    """Forget the login (password, sessions, passkeys); setup can run again.

    Everything else (projects, connections, secrets, history) stays.
    """
    session.execute(delete(OwnerSession))
    session.execute(delete(Passkey))
    session.execute(delete(Owner))
    admin_audit.record(session, "owner.reset", "removed the login (password reset)", by="cli")
    session.flush()


# --- Sessions ---


@dataclass(frozen=True)
class NewSession:
    token: str
    expires_at: datetime


def open_session(
    session: Session, settings: Settings, method: str, user_agent: str | None
) -> NewSession:
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(hours=settings.owner_session_max_hours)
    session.add(
        OwnerSession(
            token_hash=token_hash(token), expires_at=expires, method=method,
            user_agent=(user_agent or "")[:300] or None,
            confirmed_at=_now(),  # signing in proves it's the owner, for a few minutes
        )
    )  # fmt: skip
    session.flush()
    return NewSession(token, expires)


def login(
    session: Session, settings: Settings, password: str, client: str, user_agent: str | None
) -> NewSession:
    _check_rate(session, client)
    owner = session.get(Owner, 1)
    if owner is None or not verify_password(owner, password):
        _attempt(session, client, False)
        raise AuthError("wrong password")
    if _hasher.check_needs_rehash(owner.password_hash):
        owner.password_hash = _hasher.hash(password)
    _attempt(session, client, True)
    return open_session(session, settings, "password", user_agent)


def verify_session(session: Session, settings: Settings, token: str) -> OwnerSession:
    row = session.get(OwnerSession, token_hash(token), with_for_update=True)
    now = _now()
    idle = timedelta(hours=settings.owner_session_idle_hours)
    if row is None or row.expires_at <= now or row.last_seen_at + idle <= now:
        if row is not None:
            session.delete(row)
            session.flush()
        raise AuthError("not signed in")
    if now - row.last_seen_at > timedelta(minutes=1):  # don't write on every request
        row.last_seen_at = now
    return row


def confirm(session: Session, token: str, password: str, client: str) -> None:
    """The owner proves it's them again, before a sensitive action."""
    row = session.get(OwnerSession, token_hash(token), with_for_update=True)
    owner = session.get(Owner, 1)
    if row is None or owner is None:
        raise AuthError("not signed in")
    _check_rate(session, client)
    if not verify_password(owner, password):
        _attempt(session, client, False)
        raise AuthError("wrong password")
    _attempt(session, client, True)
    row.confirmed_at = _now()
    session.flush()


def recently_confirmed(session: Session, token: str) -> bool:
    row = session.get(OwnerSession, token_hash(token))
    return bool(row and row.confirmed_at and row.confirmed_at + CONFIRM_FOR > _now())


def logout(session: Session, token: str) -> None:
    session.execute(delete(OwnerSession).where(OwnerSession.token_hash == token_hash(token)))


# --- Passkeys ---


def _rp(settings: Settings) -> tuple[str, str]:
    """The relying party id (host) and origin. Passkeys need the HTTPS origin set."""
    origin = (settings.web_origin or "").rstrip("/")
    if not origin.startswith("https://") and not origin.startswith("http://localhost"):
        raise RuleViolationError(
            "passkeys need the web app's HTTPS address: set RINGKEEPER_WEB_ORIGIN"
        )
    host = origin.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return host, origin


def _save_challenge(session: Session, challenge: bytes, purpose: str) -> str:
    # Expired ones go; a flood of new ones (this is reachable signed out) is refused.
    session.execute(
        delete(Setting).where(
            Setting.key.startswith(CHALLENGE_KEY),
            Setting.value["expires"].astext.cast(DateTime(timezone=True)) <= _now(),
        )
    )
    outstanding = session.scalar(
        select(func.count()).select_from(Setting).where(Setting.key.startswith(CHALLENGE_KEY))
    )
    if (outstanding or 0) >= MAX_CHALLENGES:
        raise TooManyAttemptsError(int(CHALLENGE_TTL.total_seconds()))
    ident = secrets.token_urlsafe(16)
    session.add(
        Setting(
            key=CHALLENGE_KEY + ident,
            value={
                "challenge": bytes_to_base64url(challenge), "purpose": purpose,
                "expires": (_now() + CHALLENGE_TTL).isoformat(),
            },
        )
    )  # fmt: skip
    session.flush()
    return ident


def _take_challenge(session: Session, ident: str, purpose: str) -> bytes:
    row = session.get(Setting, CHALLENGE_KEY + ident)
    if row is None:
        raise AuthError("that passkey request expired; try again")
    session.delete(row)
    session.flush()
    value = row.value
    if value.get("purpose") != purpose or datetime.fromisoformat(value["expires"]) <= _now():
        raise AuthError("that passkey request expired; try again")
    return base64url_to_bytes(value["challenge"])


def passkey_registration_options(session: Session, settings: Settings) -> dict[str, Any]:
    rp_id, _ = _rp(settings)
    existing = session.scalars(select(Passkey)).all()
    options = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name="Ringkeeper", user_name="owner", user_id=b"ringkeeper-owner",
        user_display_name="Ringkeeper owner",
        exclude_credentials=[PublicKeyCredentialDescriptor(id=p.credential_id) for p in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )  # fmt: skip
    ident = _save_challenge(session, options.challenge, "register")
    return {"challenge_id": ident, "options": json.loads(webauthn.options_to_json(options))}


def register_passkey(
    session: Session, settings: Settings, challenge_id: str, credential: dict[str, Any], name: str
) -> Passkey:
    rp_id, origin = _rp(settings)
    challenge = _take_challenge(session, challenge_id, "register")
    try:
        verified = webauthn.verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, require_user_verification=True,
        )  # fmt: skip
    except WebAuthnException as error:
        raise RuleViolationError(f"the passkey couldn't be added: {error}") from None
    response = cast(dict[str, Any], credential.get("response") or {})
    transports = cast(list[object], response.get("transports") or [])
    passkey = Passkey(
        credential_id=verified.credential_id, public_key=verified.credential_public_key,
        sign_count=verified.sign_count, name=(name.strip() or "Passkey")[:80],
        transports=[str(t) for t in transports],
    )  # fmt: skip
    session.add(passkey)
    admin_audit.record(session, "passkey.add", f"added passkey '{passkey.name}'", by=OWNER)
    session.flush()
    return passkey


def passkey_login_options(session: Session, settings: Settings) -> dict[str, Any]:
    rp_id, _ = _rp(settings)
    if session.scalar(select(func.count()).select_from(Passkey)) == 0:
        raise RuleViolationError("no passkey has been added yet")
    options = webauthn.generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED
    )
    ident = _save_challenge(session, options.challenge, "login")
    return {"challenge_id": ident, "options": json.loads(webauthn.options_to_json(options))}


def passkey_login(
    session: Session,
    settings: Settings,
    challenge_id: str,
    credential: dict[str, Any],
    client: str,
    user_agent: str | None,
) -> NewSession:
    # No guess counting here: a passkey can't be guessed, and a password
    # attacker's failures mustn't hold the owner's passkey sign-in up.
    del client
    rp_id, origin = _rp(settings)
    challenge = _take_challenge(session, challenge_id, "login")
    raw_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id") or ""))
    passkey = session.scalars(select(Passkey).where(Passkey.credential_id == raw_id)).one_or_none()
    if passkey is None:
        raise AuthError("that passkey isn't registered here")
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )  # fmt: skip
    except WebAuthnException:
        raise AuthError("the passkey didn't check out") from None
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = _now()
    return open_session(session, settings, "passkey", user_agent)


def remove_passkey(session: Session, ident: str) -> None:
    passkey = session.get(Passkey, ident)
    if passkey is None:
        raise RuleViolationError("no such passkey")
    session.delete(passkey)
    admin_audit.record(session, "passkey.remove", f"removed passkey '{passkey.name}'", by=OWNER)
    session.flush()
