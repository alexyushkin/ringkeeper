import json
import logging
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from pydantic import SecretStr
from typer.testing import CliRunner

from ringkeeper import scrub
from ringkeeper.cli.main import app
from ringkeeper.config import Settings
from ringkeeper.keys import MasterKeyError, derive_key, generate_master_key, load_master_key
from ringkeeper.vault import Secret, Vault, VaultError, get_vault
from ringkeeper.vault.aws import AwsSecretsManagerBackend
from ringkeeper.vault.file import FileBackend
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
    with pytest.raises(MasterKeyError, match="RINGKEEPER_MASTER_KEY"):
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
    vault.store("aws-sm://ringkeeper/acme/n8n", "first-value")
    vault.store("aws-sm://ringkeeper/acme/n8n", CANARY)
    assert vault.resolve("aws-sm://ringkeeper/acme/n8n").reveal() == CANARY


def test_aws_missing_secret_shows_code_only(aws: AwsSecretsManagerBackend) -> None:
    with pytest.raises(VaultError, match="ResourceNotFoundException"):
        aws.get("aws-sm://ringkeeper/missing")


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
    logger = logging.getLogger("ringkeeper.test")
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
    from ringkeeper.db import session_scope  # noqa: PLC0415

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


def test_secret_init_creates_the_key_once(data_dir: Path) -> None:
    first = runner.invoke(app, ["secret", "init"])
    assert first.exit_code == 0, first.output
    assert "created master key" in first.stdout
    assert "Back it up now" in first.stdout
    assert (data_dir / "master.key").exists()
    again = runner.invoke(app, ["secret", "init"])
    assert "master key already exists" in again.stdout


def test_log_scrubbing_keeps_formatters_that_read_args_working() -> None:
    from uvicorn.logging import AccessFormatter  # noqa: PLC0415

    scrub.install_log_scrubbing()
    Secret("file://x", CANARY)
    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", f"/x?k={CANARY}", "1.1", 200),
        None,
    )  # fmt: skip
    line = AccessFormatter("%(client_addr)s %(request_line)s %(status_code)s").format(record)
    assert line == "127.0.0.1:1 GET /x?k=[redacted] HTTP/1.1 200 OK"

    class Leaky:
        def __str__(self) -> str:
            return f"conn {CANARY}"

    leaky = logging.getLogger("x").makeRecord(
        "x", logging.INFO, __file__, 1, "%s", (Leaky(),), None
    )
    assert leaky.getMessage() == "conn [redacted]"


def test_delete_and_concurrent_writes_keep_every_entry(data_dir: Path) -> None:
    import threading  # noqa: PLC0415

    vault = get_vault()
    threads = [
        threading.Thread(target=vault.store, args=(f"file://t/{i}", f"value-{i}"))
        for i in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [vault.resolve(f"file://t/{i}").reveal() for i in range(20)] == [
        f"value-{i}" for i in range(20)
    ]
    vault.delete("file://t/3")
    vault.delete("file://t/missing")  # fine
    with pytest.raises(VaultError):
        vault.resolve("file://t/3")
    assert vault.resolve("file://t/4").reveal() == "value-4"


def test_the_master_key_appears_whole_even_when_created_at_once(data_dir: Path) -> None:
    import threading  # noqa: PLC0415

    from ringkeeper.config import get_settings  # noqa: PLC0415
    from ringkeeper.keys import load_master_key  # noqa: PLC0415

    settings = get_settings()
    found: list[bytes] = []
    errors: list[Exception] = []

    def load() -> None:
        try:
            found.append(load_master_key(settings))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=load) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(set(found)) == 1  # one key, the same for everyone
    assert [p.name for p in data_dir.iterdir() if p.name.endswith(".tmp")] == []
