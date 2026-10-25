"""The web app's single owner account: setup, sign-in, sessions and passkeys.

- Setup: the owner is created once, and only with the one-time setup code the
  api prints on first start (and writes to the data dir). Without it, anyone
  who reached a freshly exposed server first could claim it.
- Passwords are hashed with argon2id; at least 12 characters.
- Sessions are random tokens; only their SHA-256 is stored. They end after
  `owner_session_idle_hours` without use, or `owner_session_max_hours` at most.
- Sign-in and setup-code attempts are rate limited per client and overall.
- Passkeys (WebAuthn) are optional and need the web app's HTTPS origin.
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
from sqlalchemy import delete, func, select
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
# Per client: 5 failures in 15 minutes. Overall: 50 (a spread-out guessing attempt).
CLIENT_LIMIT, GLOBAL_LIMIT = 5, 50
WINDOW = timedelta(minutes=15)
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
    since = _now() - WINDOW
    failures = select(func.count()).where(LoginAttempt.ok.is_(False), LoginAttempt.at >= since)
    mine = session.scalar(failures.where(LoginAttempt.key == f"client:{client}")) or 0
    everyone = session.scalar(failures) or 0
    if mine >= CLIENT_LIMIT or everyone >= GLOBAL_LIMIT:
        raise TooManyAttemptsError(int(WINDOW.total_seconds()))


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
    # Every other browser is signed out.
    session.execute(
        delete(OwnerSession).where(OwnerSession.token_hash != token_hash(current_token))
    )
    admin_audit.record(session, "owner.password", "changed the password", by=OWNER)
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
            user_verification=UserVerificationRequirement.PREFERRED,
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
            expected_origin=origin,
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
        rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED
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
    rp_id, origin = _rp(settings)
    _check_rate(session, client)
    challenge = _take_challenge(session, challenge_id, "login")
    raw_id = base64url_to_bytes(str(credential.get("rawId") or credential.get("id") or ""))
    passkey = session.scalars(select(Passkey).where(Passkey.credential_id == raw_id)).one_or_none()
    if passkey is None:
        _attempt(session, client, False)
        raise AuthError("that passkey isn't registered here")
    try:
        verified = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
        )  # fmt: skip
    except WebAuthnException:
        _attempt(session, client, False)
        raise AuthError("the passkey didn't check out") from None
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = _now()
    _attempt(session, client, True)
    return open_session(session, settings, "passkey", user_agent)


def remove_passkey(session: Session, ident: str) -> None:
    passkey = session.get(Passkey, ident)
    if passkey is None:
        raise RuleViolationError("no such passkey")
    session.delete(passkey)
    admin_audit.record(session, "passkey.remove", f"removed passkey '{passkey.name}'", by=OWNER)
    session.flush()
