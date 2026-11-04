"""Claude Code's login: a file on Windows and Linux, the Keychain on a Mac."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from ringkeeper.errors import RuleViolationError
from ringkeeper.services import subscription

TOKEN = "sk-ant-oat01-" + "a" * 40  # a made-up one


def login(token: str = TOKEN, expires_in: float = 3600) -> str:
    expires = (time.time() + expires_in) * 1000
    return json.dumps({"claudeAiOauth": {"accessToken": token, "expiresAt": expires}})


@pytest.fixture
def no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / ".credentials.json"
    monkeypatch.setattr(subscription, "credentials_file", lambda: path)
    return path


def on(monkeypatch: pytest.MonkeyPatch, system: str) -> None:
    monkeypatch.setattr(subscription.platform, "system", lambda: system)


def keychain(monkeypatch: pytest.MonkeyPatch, code: int, said: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, code, stdout=said, stderr="")

    monkeypatch.setattr(subscription.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_windows_and_linux_read_the_file_and_never_the_keychain(
    monkeypatch: pytest.MonkeyPatch, no_file: Path, system: str
) -> None:
    on(monkeypatch, system)
    calls = keychain(monkeypatch, 0, login("sk-ant-oat01-wrong"))
    no_file.write_text(login(), encoding="utf-8")
    assert subscription.login_token() == TOKEN
    assert calls == []


def test_a_mac_reads_the_keychain(monkeypatch: pytest.MonkeyPatch, no_file: Path) -> None:
    on(monkeypatch, "Darwin")
    calls = keychain(monkeypatch, 0, login() + "\n")
    assert subscription.login_token() == TOKEN
    assert no_file.exists() is False
    [command] = calls
    assert command == [
        "/usr/bin/security", "find-generic-password", "-s", "Claude Code-credentials", "-w",
    ]  # fmt: skip


def test_a_mac_without_it_in_the_keychain_tries_the_file(
    monkeypatch: pytest.MonkeyPatch, no_file: Path
) -> None:
    on(monkeypatch, "Darwin")
    keychain(monkeypatch, 44, "")
    no_file.write_text(login(), encoding="utf-8")
    assert subscription.login_token() == TOKEN


def test_refusing_macos_s_prompt_says_what_to_do(
    monkeypatch: pytest.MonkeyPatch, no_file: Path
) -> None:
    on(monkeypatch, "Darwin")
    keychain(monkeypatch, 51, "")
    assert no_file.exists() is False
    with pytest.raises(RuleViolationError, match="setup-token"):
        subscription.login_token()


def test_a_prompt_nobody_answers_doesn_t_hang(
    monkeypatch: pytest.MonkeyPatch, no_file: Path
) -> None:
    on(monkeypatch, "Darwin")
    assert no_file.exists() is False

    def waits(command: list[str], **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subscription.subprocess, "run", waits)
    with pytest.raises(RuleViolationError, match="setup-token"):
        subscription.login_token()


@pytest.mark.parametrize("system", ["Darwin", "Windows"])
def test_an_expired_login_says_so(
    monkeypatch: pytest.MonkeyPatch, no_file: Path, system: str
) -> None:
    on(monkeypatch, system)
    keychain(monkeypatch, 0, login(expires_in=-60))
    no_file.write_text(login(expires_in=-60), encoding="utf-8")
    with pytest.raises(RuleViolationError, match="expired"):
        subscription.login_token()


def test_the_token_isn_t_in_what_goes_wrong(monkeypatch: pytest.MonkeyPatch, no_file: Path) -> None:
    on(monkeypatch, "Darwin")
    keychain(monkeypatch, 0, login(expires_in=-60))
    assert no_file.exists() is False
    with pytest.raises(RuleViolationError) as caught:
        subscription.login_token()
    assert TOKEN not in str(caught.value)
