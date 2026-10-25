"""Where connections may point: never Ringkeeper itself, loopback or link-local."""

import pytest

from ringkeeper import netpolicy
from ringkeeper.netpolicy import AddressNotAllowedError


@pytest.fixture(autouse=True)
def _strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RINGKEEPER_ALLOW_LOCAL_CONNECTIONS", raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://[fe80::1]/",
        "http://127.0.0.1:5678",
        "http://localhost:5678",
        "http://host.docker.internal:5678",  # the machine Ringkeeper runs on
        "http://postgres:5432",
        "http://gateway:8100",
        "https://user:pass@n8n.example.com",
        "https://n8n.example.com/?x=1",
        "ftp://n8n.example.com",
        "n8n.example.com",
    ],
)
def test_urls_that_are_refused(url: str) -> None:
    with pytest.raises(AddressNotAllowedError):
        netpolicy.check_url(url)


@pytest.mark.parametrize(
    "url", ["https://n8n.example.com", "http://192.168.1.20:5678", "https://n8n.example.com/base"]
)
def test_urls_that_are_fine(url: str) -> None:
    assert netpolicy.check_url(url) == url


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://ringkeeper:ringkeeper@postgres:5432/ringkeeper",  # Ringkeeper's own DB
        "postgresql://u:p@127.0.0.1:5432/db",
        "postgresql://u:p@169.254.169.254/db",
        "postgresql://u:p@/db?host=/var/run/postgresql",  # a local socket
        "postgresql:///db",
    ],
)
def test_connection_strings_that_are_refused(dsn: str) -> None:
    with pytest.raises(AddressNotAllowedError):
        netpolicy.check_dsn(dsn)


def test_a_supabase_pooler_string_is_fine() -> None:
    netpolicy.check_dsn(
        "postgresql://postgres.abc:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres"
    )


def test_local_addresses_can_be_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_ALLOW_LOCAL_CONNECTIONS", "1")
    netpolicy.check_url("http://127.0.0.1:5678")
    with pytest.raises(AddressNotAllowedError):
        netpolicy.check_url("http://169.254.169.254/")  # link-local never
