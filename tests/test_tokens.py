import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from interlock.cli.main import app
from interlock.config import Settings
from interlock.keys import generate_master_key
from interlock.tokens import TokenError, issue_token, parse_ttl, verify_token
from tests.helpers import make_project

runner = CliRunner()


def test_round_trip() -> None:
    token, issued = issue_token("acme-web", timedelta(hours=1), session_id="s-1")
    claims = verify_token(token)
    assert claims == issued
    assert claims.project == "acme-web"
    assert claims.session_id == "s-1"


def test_expired_token_is_refused() -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    token, _ = issue_token("acme-web", timedelta(hours=1), now=past)
    with pytest.raises(TokenError, match="expired; ask for a new one"):
        verify_token(token)


def _tamper(token: str, **changes: object) -> str:
    header, payload, signature = token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data.update(changes)
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{header}.{forged}.{signature}"


def test_tampered_project_is_refused() -> None:
    token, _ = issue_token("acme-web", timedelta(hours=1))
    with pytest.raises(TokenError, match="signature is invalid"):
        verify_token(_tamper(token, sub="globex-portal"))


def test_tampered_expiry_is_refused() -> None:
    token, _ = issue_token("acme-web", timedelta(minutes=5))
    later = int((datetime.now(UTC) + timedelta(days=30)).timestamp())
    with pytest.raises(TokenError, match="signature is invalid"):
        verify_token(_tamper(token, exp=later))


def test_token_from_another_master_key_is_refused() -> None:
    other = Settings(master_key=SecretStr(generate_master_key()))
    token, _ = issue_token("acme-web", timedelta(hours=1), settings=other)
    with pytest.raises(TokenError, match="signature is invalid"):
        verify_token(token)


def _b64(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def test_alg_none_is_refused() -> None:
    claims: dict[str, object] = {
        "sub": "acme-web", "sid": "s", "jti": "j", "iss": "interlock",
        "aud": "interlock-gateway", "iat": 0, "exp": 2**33,
    }  # fmt: skip
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(claims)}."
    with pytest.raises(TokenError, match="malformed"):
        verify_token(unsigned)


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
def test_garbage_is_refused(token: str) -> None:
    with pytest.raises(TokenError, match="malformed"):
        verify_token(token)


def test_ttl_limits() -> None:
    with pytest.raises(TokenError, match="at most"):
        issue_token("acme-web", timedelta(days=2))
    with pytest.raises(TokenError, match="positive"):
        issue_token("acme-web", timedelta(0))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30m", timedelta(minutes=30)), ("1h", timedelta(hours=1)), ("2d", timedelta(days=2))],
)
def test_parse_ttl(text: str, expected: timedelta) -> None:
    assert parse_ttl(text) == expected


def test_parse_ttl_rejects_nonsense() -> None:
    with pytest.raises(TokenError, match="invalid ttl"):
        parse_ttl("soon")


def test_cli_issue(database_url: str) -> None:
    from interlock.db import session_scope  # noqa: PLC0415

    with session_scope() as session:
        make_project(session, "acme-web")
    result = runner.invoke(app, ["token", "issue", "--project", "acme-web", "--ttl", "30m"])
    assert result.exit_code == 0, result.output
    token = result.stdout.strip()
    assert verify_token(token).project == "acme-web"
    assert "expires" in result.stderr


def test_cli_issue_unknown_project(database_url: str) -> None:
    result = runner.invoke(app, ["token", "issue", "--project", "ghost"])
    assert result.exit_code == 1
    assert "project 'ghost' does not exist" in result.stderr
