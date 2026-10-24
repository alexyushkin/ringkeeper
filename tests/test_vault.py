import json
import logging
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from pydantic import SecretStr
from typer.testing import CliRunner

from interlock import scrub
from interlock.cli.main import app
from interlock.config import Settings
from interlock.keys import MasterKeyError, derive_key, generate_master_key, load_master_key
from interlock.vault import Secret, Vault, VaultError, get_vault
from interlock.vault.aws import AwsSecretsManagerBackend
from interlock.vault.file import FileBackend
from tests.helpers import make_instance

runner = CliRunner()
CANARY = "canary-v4ult-0b7c9e"  # a fake value; tests assert it never escapes


# --- Master key ---


def test_key_file_is_created_once(data_dir: Path) -> None:
    settings = Settings()
    first = load_master_key(settings)
    assert (data_dir / "master.key").exists()
    assert load_master_key(settings) == first


def test_env_key_wins_over_file() -> None:
    key = generate_master_key()
    settings = Settings(master_key=SecretStr(key))
    assert load_master_key(settings) == load_master_key(Settings(master_key=SecretStr(key)))


@pytest.mark.parametrize("bad", ["not base64 !!", "c2hvcnQ="])
def test_invalid_env_key(bad: str) -> None:
    with pytest.raises(MasterKeyError, match="INTERLOCK_MASTER_KEY"):
        load_master_key(Settings(master_key=SecretStr(bad)))


def test_missing_key_without_create() -> None:
    with pytest.raises(MasterKeyError, match="no master key"):
        load_master_key(Settings(), create=False)


def test_derived_keys_differ_by_purpose() -> None:
    master = b"\x01" * 32
    assert derive_key(master, "vault") != derive_key(master, "tokens")


# --- file:// backend ---


def test_file_round_trip_and_ciphertext_hides_value(data_dir: Path) -> None:
    vault = get_vault()
    vault.store("file://acme/supabase-dev", CANARY)
    assert vault.resolve("file://acme/supabase-dev").reveal() == CANARY
    assert CANARY not in (data_dir / "vault.json").read_text(encoding="utf-8")


def test_file_unknown_ref() -> None:
    with pytest.raises(VaultError, match="is not in the vault; store it with"):
        get_vault().resolve("file://nope")


def test_file_wrong_master_key(data_dir: Path) -> None:
    path = data_dir / "vault.json"
    FileBackend(path, b"\x01" * 32).put("file://x", CANARY)
    with pytest.raises(VaultError, match="wrong master key") as excinfo:
        FileBackend(path, b"\x02" * 32).get("file://x")
    assert CANARY not in str(excinfo.value)


def test_file_entry_is_bound_to_its_ref(data_dir: Path) -> None:
    path = data_dir / "vault.json"
    backend = FileBackend(path, b"\x01" * 32)
    backend.put("file://acme", CANARY)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["entries"]["file://globex"] = data["entries"]["file://acme"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(VaultError, match="cannot decrypt"):
        backend.get("file://globex")


def test_vault_rejects_non_pointer_refs() -> None:
    with pytest.raises(VaultError, match="must be a pointer"):
        get_vault().resolve(CANARY)


# --- aws-sm:// backend ---


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[AwsSecretsManagerBackend]:
    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",  # moto's documented placeholder
        "AWS_DEFAULT_REGION": "eu-west-1",
    }.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        yield AwsSecretsManagerBackend(boto3.client("secretsmanager"))  # pyright: ignore[reportUnknownMemberType]


def test_aws_create_update_and_read(aws: AwsSecretsManagerBackend) -> None:
    vault = Vault({"aws-sm": lambda: aws})
    vault.store("aws-sm://interlock/acme/n8n", "first-value")
    vault.store("aws-sm://interlock/acme/n8n", CANARY)
    assert vault.resolve("aws-sm://interlock/acme/n8n").reveal() == CANARY


def test_aws_missing_secret_shows_code_only(aws: AwsSecretsManagerBackend) -> None:
    with pytest.raises(VaultError, match="ResourceNotFoundException"):
        aws.get("aws-sm://interlock/missing")


# --- Secret handling ---


def test_secret_never_prints_its_value() -> None:
    secret = Secret("file://x", CANARY)
    assert CANARY not in repr(secret)
    assert CANARY not in str(secret)
    assert CANARY not in f"{secret}"
    assert scrub.scrub(f"connect failed for {CANARY}") == "connect failed for [redacted]"


def test_log_records_are_scrubbed(caplog: pytest.LogCaptureFixture) -> None:
    scrub.install_log_scrubbing()
    Secret("file://x", CANARY)
    logger = logging.getLogger("interlock.test")
    with caplog.at_level(logging.INFO):
        logger.info("value is %s", CANARY)
        try:
            raise RuntimeError(CANARY)
        except RuntimeError:
            logger.exception("boom")
    assert "value is [redacted]" in caplog.text
    assert CANARY not in caplog.text


# --- CLI ---


def test_secret_set_reads_stdin_and_never_echoes(data_dir: Path) -> None:
    result = runner.invoke(app, ["secret", "set", "file://acme/n8n"], input=CANARY + "\n")
    assert result.exit_code == 0, result.output
    assert CANARY not in result.output
    assert get_vault().resolve("file://acme/n8n").reveal() == CANARY


def test_secret_set_has_no_value_argument() -> None:
    result = runner.invoke(app, ["secret", "set", "file://acme/n8n", CANARY])
    assert result.exit_code == 2  # usage error: values never come from argv
    with pytest.raises(VaultError, match="is not in the vault"):
        get_vault().resolve("file://acme/n8n")


def test_secret_set_rejects_empty_value() -> None:
    result = runner.invoke(app, ["secret", "set", "file://acme/n8n"], input="")
    assert result.exit_code == 1
    assert "no value given" in result.stderr


def test_secret_check(database_url: str) -> None:
    from interlock.db import session_scope  # noqa: PLC0415

    with session_scope() as session:
        make_instance(session, "acme-supabase-dev", secret_ref="file://acme/supabase-dev")
    missing = runner.invoke(app, ["secret", "check", "acme-supabase-dev"])
    assert missing.exit_code == 1
    assert "is not in the vault" in missing.stderr

    get_vault().store("file://acme/supabase-dev", CANARY)
    ok = runner.invoke(app, ["secret", "check", "acme-supabase-dev"])
    assert ok.exit_code == 0, ok.output
    assert "ok: secret for acme-supabase-dev resolves" in ok.stdout
    assert CANARY not in ok.output
