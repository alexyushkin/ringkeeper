"""Where connections may point: checked when one is saved and again at connect time.

A connection's address (an n8n base URL, a Postgres connection string) comes
from whoever manages Ringkeeper, but it decides where the api and gateway
send a client's credentials. So it may not point at:

- Ringkeeper's own services (its database, api, gateway, web app, stt), by
  name or by the addresses those names resolve to;
- loopback, link-local (including cloud metadata at 169.254.169.254),
  unspecified or multicast addresses.

LAN addresses stay allowed: a self-hosted n8n often lives on one. URLs may
not carry a username, password, query or fragment, and must be http(s).
`RINGKEEPER_ALLOW_LOCAL_CONNECTIONS=1` lifts the loopback rule (tests, and
services published on the same host for development).

The service names are resolved together (a slow DNS costs one wait, not five),
and not at all in desktop mode, which has no such services: its database is
a file and its API and gateway are on loopback, which is refused anyway.
"""

import ipaddress
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

from ringkeeper.errors import RuleViolationError

OWN_SERVICES = ("postgres", "api", "gateway", "web", "stt")
_CACHE_SECONDS = 60.0
_own_cache: dict[str, object] = {"at": 0.0, "addresses": frozenset[str]()}


class AddressNotAllowedError(RuleViolationError):
    pass


def _allow_local() -> bool:
    return os.environ.get("RINGKEEPER_ALLOW_LOCAL_CONNECTIONS") == "1"


def local_allowed() -> bool:
    """Loopback addresses are allowed (tests, development)."""
    return _allow_local()


def _addresses(host: str) -> set[str]:
    try:
        return {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return set()


def _own_addresses() -> frozenset[str]:
    """What Ringkeeper's own service names resolve to here (cached for a minute)."""
    if os.environ.get("RINGKEEPER_MODE") == "desktop":
        return frozenset()
    if time.monotonic() - cast(float, _own_cache["at"]) > _CACHE_SECONDS:
        with ThreadPoolExecutor(len(OWN_SERVICES)) as pool:
            found = set[str]().union(*pool.map(_addresses, OWN_SERVICES))
        _own_cache.update(at=time.monotonic(), addresses=frozenset(found))
    return cast(frozenset[str], _own_cache["addresses"])


def check_host(host: str) -> None:
    """Refuse a host that is Ringkeeper itself, or a local or link-local address."""
    name = host.strip().strip("[]").rstrip(".").lower()
    if not name:
        raise AddressNotAllowedError("the address has no host")
    if name in OWN_SERVICES:
        raise AddressNotAllowedError(f"'{host}' is one of Ringkeeper's own services")
    local_name = name == "localhost" or name.endswith((".localhost", ".internal"))
    if local_name and not _allow_local():
        # host.docker.internal is the machine Ringkeeper runs on.
        raise AddressNotAllowedError(f"connections can't point at '{host}' (this machine)")
    own = _own_addresses()
    for address in _addresses(name) or {name}:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            continue  # an unresolvable name: the connection will just fail
        if address in own:
            raise AddressNotAllowedError(f"'{host}' is one of Ringkeeper's own services")
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise AddressNotAllowedError(f"'{host}' is a link-local or special address")
        if ip.is_loopback and not _allow_local():
            raise AddressNotAllowedError(f"'{host}' is a loopback address")


def check_url(url: str) -> str:
    """An http(s) base URL with a host and nothing hidden in it. Returns it trimmed."""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise AddressNotAllowedError("that isn't a valid address") from None
    if parts.scheme not in ("http", "https"):
        raise AddressNotAllowedError("the address must start with https:// (or http://)")
    if parts.username or parts.password or "@" in parts.netloc:
        raise AddressNotAllowedError(
            "put no username or password in the address; the API key goes in its own field"
        )
    if parts.query or parts.fragment:
        raise AddressNotAllowedError("the address can't have a ?query or #fragment")
    check_host(parts.hostname or "")
    return url


def check_dsn(dsn: str) -> None:
    """Every host in a Postgres connection string."""
    try:
        params = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        raise AddressNotAllowedError("that isn't a valid connection string") from None
    hosts = [h for h in str(params.get("host") or "").split(",") if h]
    hostaddrs = [h for h in str(params.get("hostaddr") or "").split(",") if h]
    if not hosts and not hostaddrs:
        raise AddressNotAllowedError("the connection string has no host")
    for host in [*hosts, *hostaddrs]:
        if host.startswith("/"):
            raise AddressNotAllowedError("connections over a local socket aren't allowed")
        check_host(host)
