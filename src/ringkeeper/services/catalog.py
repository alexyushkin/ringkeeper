"""The open catalog: MCP servers from the official MCP Registry, cached so
search still works offline.

Searches go to the registry (registry.modelcontextprotocol.io) and every
server seen is kept in the data folder (catalog/registry.json). Without the
network, searches run over that cache instead and say so.

What the app shows of each server:

- its name, title and description, and its publisher: the registry only
  lists a server under a namespace its publisher proved they own, either a
  domain (com.example/... : DNS or HTTP proof) or a GitHub account
  (io.github.someone/... : signed in to GitHub);
- remote (a URL to connect to) or local (an npm, PyPI or container package
  that runs on this computer), or both;
- whether one-click sign-in works: MCP OAuth with dynamic client
  registration, found by asking the server (cached for a week);
- whether it's one of the services with built-in rules (ringkeeper.remote),
  matched by URL: those connect as that service, with its scope and SQL checks.
"""

import contextlib
import json
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit

import httpx2

from ringkeeper import netpolicy, remote
from ringkeeper.config import Settings

REGISTRY = "https://registry.modelcontextprotocol.io"
API = "/v0.1/servers"
PAGE = 30
TIMEOUT = 10.0
PROBE_SECONDS = 7 * 24 * 3600
PROBE_WAIT = 4.0  # a search waits this long, at most, to learn about sign-in
LOCAL_TYPES = ("npm", "pypi", "oci")
OFFICIAL = "io.modelcontextprotocol.registry/official"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.BaseTransport | None = None

_lock = threading.Lock()


def _http() -> httpx2.Client:
    return httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=True)


# --- The cache ---


def _cache_file(settings: Settings) -> Path:
    return settings.data_dir / "catalog" / "registry.json"


def _load(settings: Settings) -> dict[str, Any]:
    try:
        data: Any = json.loads(_cache_file(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    cache = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    cache.setdefault("servers", {})
    cache.setdefault("probes", {})
    return cache


def _save(settings: Settings, cache: dict[str, Any]) -> None:
    path = _cache_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(cache), encoding="utf-8")
    partial.replace(path)


# --- What a registry entry says ---


def publisher(name: str) -> dict[str, str]:
    """Who published it, as the registry checked: a domain, or a GitHub account."""
    namespace = name.split("/", 1)[0]
    parts = namespace.split(".")
    if namespace.startswith("io.github.") and len(parts) >= 3:
        account = ".".join(parts[2:])
        return {"publisher": f"github.com/{account}", "verified": "github"}
    return {"publisher": ".".join(reversed(parts)), "verified": "domain"}


def _remotes(server: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for entry in cast(list[dict[str, Any]], server.get("remotes") or []):
        url = str(entry.get("url") or "")
        if entry.get("type") not in ("streamable-http", "sse") or not url.startswith("http"):
            continue
        headers = [
            {
                "name": str(h.get("name") or ""),
                "description": str(h.get("description") or ""),
                "required": bool(h.get("isRequired")),
                "secret": bool(h.get("isSecret")),
                "value": str(h.get("value") or ""),
            }
            for h in cast(list[dict[str, Any]], entry.get("headers") or [])
            if h.get("name")
        ]
        found.append(
            {
                "type": entry["type"],
                "url": url,
                "headers": headers,
                # A URL with {variables} in it needs settings Ringkeeper doesn't ask for yet.
                "usable": "{" not in url and entry["type"] == "streamable-http",
            }
        )
    return found


def _inputs(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": str(i.get("name") or i.get("valueHint") or ""),
            "description": str(i.get("description") or ""),
            "required": bool(i.get("isRequired")),
            "secret": bool(i.get("isSecret")),
            "default": str(i.get("default") or ""),
            "value": str(i.get("value") or ""),
            "type": str(i.get("type") or "named"),
        }
        for i in items
    ]


def _packages(server: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for entry in cast(list[dict[str, Any]], server.get("packages") or []):
        kind = str(entry.get("registryType") or "")
        transport_type = str(cast(dict[str, Any], entry.get("transport") or {}).get("type") or "")
        found.append(
            {
                "registry": kind,
                "identifier": str(entry.get("identifier") or ""),
                "version": str(entry.get("version") or ""),
                "runtime_hint": str(entry.get("runtimeHint") or ""),
                "env": _inputs(cast(list[dict[str, Any]], entry.get("environmentVariables") or [])),
                "arguments": _inputs(
                    cast(list[dict[str, Any]], entry.get("packageArguments") or [])
                ),
                # Ringkeeper installs these, and runs them over stdio.
                "usable": kind in LOCAL_TYPES and transport_type == "stdio",
            }
        )
    return found


def summarize(entry: dict[str, Any], probes: dict[str, Any] | None = None) -> dict[str, Any]:
    """A registry listing's entry, as the app shows it."""
    server = cast(dict[str, Any], entry.get("server") or entry)
    meta = cast(dict[str, Any], cast(dict[str, Any], entry.get("_meta") or {}).get(OFFICIAL) or {})
    name = str(server.get("name") or "")
    remotes_found, packages = _remotes(server), _packages(server)
    recommended = next(
        (r.key for r in remote.CATALOG for e in remotes_found if same_url(e["url"], r.url)), None
    )
    kinds = [k for k, has in (("remote", remotes_found), ("local", packages)) if has]
    return {
        "name": name,
        "title": str(server.get("title") or name.split("/", 1)[-1]),
        "description": str(server.get("description") or "")[:500],
        "version": str(server.get("version") or ""),
        **publisher(name),
        "status": str(meta.get("status") or "active"),
        "website": str(server.get("websiteUrl") or ""),
        "repository": str(cast(dict[str, Any], server.get("repository") or {}).get("url") or ""),
        "kind": "+".join(kinds) or "none",
        "remotes": remotes_found,
        "packages": packages,
        "signin": _signin(remotes_found, probes or {}),
        "recommended": recommended,
    }


def same_url(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def _signin(remotes_found: list[dict[str, Any]], probes: dict[str, Any]) -> bool | None:
    """One-click sign-in: True or False once known, None until the server's been asked."""
    answers = [
        cast(dict[str, Any], probes.get(r["url"]) or {}).get("signin") for r in remotes_found
    ]
    if True in answers:
        return True
    if remotes_found and all(a is False for a in answers):
        return False
    return None


# --- Sign-in, asked of the server ---


def probe(url: str) -> bool:
    """Whether the server offers MCP OAuth that any app can register for."""
    from ringkeeper.services import mcp_oauth  # noqa: PLC0415 (it imports this module's peers)

    try:
        _, server = mcp_oauth.discover(url)
    except mcp_oauth.SignInError:
        return False
    return bool(server.get("registration_endpoint"))


def _probe_all(settings: Settings, urls: list[str], cache: dict[str, Any]) -> None:
    """Learn about sign-in for servers not asked lately, for up to PROBE_WAIT seconds."""
    now = time.time()
    probes = cast(dict[str, Any], cache["probes"])
    todo = [u for u in urls if now - float(probes.get(u, {}).get("at", 0)) > PROBE_SECONDS]
    if not todo:
        return
    pool = ThreadPoolExecutor(max_workers=8)
    futures = {pool.submit(probe, u): u for u in todo}
    done, _ = wait(futures, timeout=PROBE_WAIT)
    pool.shutdown(wait=False, cancel_futures=True)
    for future in done:
        with contextlib.suppress(Exception):
            probes[futures[future]] = {"signin": future.result(), "at": now}


# --- Search ---


def _remember(cache: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    servers = cast(dict[str, Any], cache["servers"])
    for entry in entries:
        name = str(cast(dict[str, Any], entry.get("server") or {}).get("name") or "")
        if name:
            servers[name] = entry


def _offline_search(cache: dict[str, Any], query: str) -> list[dict[str, Any]]:
    words = query.lower().split()
    found: list[dict[str, Any]] = []
    for entry in cast(dict[str, Any], cache["servers"]).values():
        server = cast(dict[str, Any], entry.get("server") or {})
        text = " ".join(str(server.get(k) or "") for k in ("name", "title", "description")).lower()
        if all(w in text for w in words):
            found.append(cast(dict[str, Any], entry))
    return sorted(found, key=lambda e: str(e["server"].get("name")))


def search(settings: Settings, query: str = "", cursor: str | None = None) -> dict[str, Any]:
    """A page of servers matching `query`, from the registry, or from the cache offline."""
    params = {"limit": str(PAGE), "version": "latest"}
    if query.strip():
        params["search"] = query.strip()
    if cursor:
        params["cursor"] = cursor
    with _lock:
        cache = _load(settings)
    try:
        with _http() as http:
            response = http.get(
                (settings.mcp_registry_url or REGISTRY).rstrip("/") + API, params=params
            )
            response.raise_for_status()
            data = cast(dict[str, Any], response.json())
        entries = cast(list[dict[str, Any]], data.get("servers") or [])
        next_cursor = cast(dict[str, Any], data.get("metadata") or {}).get("nextCursor")
        offline = False
    except (httpx2.HTTPError, ValueError):
        entries = _offline_search(cache, query)[:100]
        next_cursor, offline = None, True
    entries = [
        e
        for e in entries
        if cast(dict[str, Any], cast(dict[str, Any], e.get("_meta") or {}).get(OFFICIAL) or {}).get(
            "status"
        )
        != "deleted"
    ]
    if not offline:
        urls = [r["url"] for e in entries for r in summarize(e)["remotes"] if r["usable"]]
        _probe_all(settings, urls, cache)
        with _lock:
            fresh = _load(settings)
            _remember(fresh, entries)
            fresh["probes"] = {**fresh["probes"], **cache["probes"]}
            fresh["updated_at"] = time.time()
            _save(settings, fresh)
            cache = fresh
    return {
        "servers": [summarize(e, cache["probes"]) for e in entries],
        "next_cursor": next_cursor,
        "offline": offline,
        "cached_at": cache.get("updated_at") if offline else None,
    }


def get(settings: Settings, name: str) -> dict[str, Any]:
    """One server's latest version: from the registry, else the cache. KeyError if neither."""
    base = (settings.mcp_registry_url or REGISTRY).rstrip("/")
    url = f"{base}{API}/{quote(name, safe='')}/versions/latest"
    with _lock:
        cache = _load(settings)
    entry: dict[str, Any] | None = None
    try:
        with _http() as http:
            response = http.get(url)
            response.raise_for_status()
            entry = cast(dict[str, Any], response.json())
    except (httpx2.HTTPError, ValueError):
        entry = cast(dict[str, Any] | None, cache["servers"].get(name))
    if entry is None:
        raise KeyError(name)
    summary = summarize(entry)
    _probe_all(settings, [r["url"] for r in summary["remotes"] if r["usable"]], cache)
    with _lock:
        fresh = _load(settings)
        _remember(fresh, [entry])
        fresh["probes"] = {**fresh["probes"], **cache["probes"]}
        _save(settings, fresh)
    return summarize(entry, cache["probes"])


def latest_versions(settings: Settings) -> dict[str, str]:
    """Each cached server's latest version, as the registry last said."""
    with _lock:
        cache = _load(settings)
    return {
        name: str(cast(dict[str, Any], entry.get("server") or {}).get("version") or "")
        for name, entry in cast(dict[str, dict[str, Any]], cache["servers"]).items()
    }


def check_remote_url(url: str) -> str:
    """An address someone typed or the registry gave: http(s), a real host, nothing hidden."""
    url = netpolicy.check_url(url)
    if urlsplit(url).scheme != "https" and not netpolicy.local_allowed():
        raise netpolicy.AddressNotAllowedError("a remote MCP server's address must be https://")
    return url
