import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ringkeeper.schemas import SECRET_REF_ERROR, InstanceIn, validate_secret_ref

VALID = [
    "file:///run/secrets/acme-supabase-dev",
    "file://secrets/acme.json",
    "aws-sm://ringkeeper/acme/supabase-dev",
    "aws-sm://arn:aws:secretsmanager:eu-west-1:000000000000:secret:acme-dev",
]

INVALID = [
    "",
    "sk-live-0000000000000000",  # a bare value
    "hunter2",
    "eyJhbGciOiJIUzI1NiJ9.e30.ZmFrZQ",  # JWT-shaped
    "https://vault.example/acme",  # wrong scheme
    "env://ACME_KEY",
    "vault://acme",
    "FILE:///run/secrets/x",  # schemes are case-sensitive
    "file:",
    "file://",
    "file:///",
    "aws-sm://",
    "aws-sm:/ringkeeper/acme",
    "aws-sm://user:pass@ringkeeper/acme",  # userinfo
    "file:///run/secrets/x?token=abc",  # query
    "file:///run/secrets/x#abc",  # fragment
    "aws-sm://ringkeeper/acme dev",  # whitespace
    "aws-sm://" + "a" * 2000,  # absurdly long
]


@pytest.mark.parametrize("value", VALID)
def test_accepts_pointers(value: str) -> None:
    assert validate_secret_ref(value) == value


@pytest.mark.parametrize("value", INVALID)
def test_rejects_anything_that_is_not_a_pointer(value: str) -> None:
    with pytest.raises(ValueError, match="must be a pointer"):
        validate_secret_ref(value)


def test_error_never_echoes_the_rejected_value() -> None:
    leaked = "sk-live-do-not-print-me"
    with pytest.raises(ValidationError) as excinfo:
        InstanceIn.model_validate(
            {"name": "x", "type": "supabase", "env": "dev", "owner": "acme", "secret_ref": leaked}
        )
    messages = [error["msg"] for error in excinfo.value.errors(include_input=False)]
    assert messages == [f"Value error, {SECRET_REF_ERROR}"]
    assert all(leaked not in message for message in messages)


def test_database_rejects_non_pointer_even_without_services(session: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_instances_secret_ref_pointer"):
        session.execute(
            text(
                "INSERT INTO instances (name, type, env, secret_ref, owner) "
                "VALUES ('raw', 'supabase', 'dev', 'sk-live-raw', 'acme')"
            )
        )
