"""The rule for every kind of connection: many of each, and each one either a client's
(only that client's projects can use it) or yours (a shared tool: any project).

One case per built-in service: two clients' connections of that kind, each in its
client's project, never cross (not linked, and the gateway refuses the call before
a secret is read); yours works in both, with its own secret."""

import pytest

from ringkeeper.adapters import ADAPTERS
from ringkeeper.adapters.base import ToolKind
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType
from ringkeeper.services import bindings, instances
from ringkeeper.services.calls import RefusalError, _target, authorize  # pyright: ignore[reportPrivateUsage]
from ringkeeper.vault import get_vault
from tests.helpers import bind, make_instance, make_project

KINDS = sorted(ADAPTERS, key=lambda kind: kind.value)


def read_tool(kind: InstanceType) -> str:
    return next(t.name for t in ADAPTERS[kind].tools if t.kind is ToolKind.READ)


def secret_used(project: str, kind: InstanceType, instance: str) -> str:
    """What the gateway would call with, for this project naming this instance."""
    spec = ADAPTERS[kind].tool(read_tool(kind))
    call = ToolCall(Caller(project, "session-1", "token-1"), kind, spec.name, instance, {})
    with session_scope() as session:
        auth = authorize(session, call, spec)
    return auth.secret_ref


@pytest.mark.parametrize("kind", KINDS, ids=[k.value for k in KINDS])
def test_two_clients_never_cross_and_yours_works_in_both(
    database_url: str, kind: InstanceType
) -> None:
    vault = get_vault()
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_project(session, "globex-app", owner="globex")
        for name, owner, shared in (
            ("acme-conn", "acme", False),
            ("globex-conn", "globex", False),
            ("mine-conn", instances.SHARED_OWNER, True),
        ):
            ref = f"file://ringkeeper/connections/{name}"
            vault.store(ref, f"secret-of-{name}")
            make_instance(session, name, kind, secret_ref=ref, owner=owner, shared=shared)
    bind("acme-web", "acme-conn")
    bind("globex-app", "globex-conn")
    bind("acme-web", "mine-conn")
    bind("globex-app", "mine-conn")

    # A client's connection can't be linked to another client's project...
    with session_scope() as session, pytest.raises(RuleViolationError, match="one client's"):
        bindings.check_same_client(session, "globex-app", "acme-conn")
    with session_scope() as session, pytest.raises(RuleViolationError, match="one client's"):
        bindings.check_same_client(session, "acme-web", "globex-conn")
    # ...nor called from it: refused before any secret is read.
    with pytest.raises(RefusalError, match="not available to project 'globex-app'"):
        secret_used("globex-app", kind, "acme-conn")
    with pytest.raises(RefusalError, match="not available to project 'acme-web'"):
        secret_used("acme-web", kind, "globex-conn")

    # Each project's own calls use its own connection's secret.
    assert secret_used("acme-web", kind, "acme-conn").endswith("/acme-conn")
    assert secret_used("globex-app", kind, "globex-conn").endswith("/globex-conn")
    # Yours works in both, with its own secret.
    for project in ("acme-web", "globex-app"):
        ref = secret_used(project, kind, "mine-conn")
        assert ref.endswith("/mine-conn")
        assert vault.resolve(ref).reveal() == "secret-of-mine-conn"


def test_the_gateway_resolves_the_named_connections_own_secret(database_url: str) -> None:
    """The secret the adapter gets is the authorized connection's, not another's."""
    kind = InstanceType.AIRTABLE
    vault = get_vault()
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        for name in ("acme-one", "acme-two"):
            ref = f"file://ringkeeper/connections/{name}"
            vault.store(ref, f"pat-{name}")
            make_instance(session, name, kind, secret_ref=ref, owner="acme")
    bind("acme-web", "acme-one")
    bind("acme-web", "acme-two")
    adapter = ADAPTERS[kind]
    spec = adapter.tool(read_tool(kind))
    for name in ("acme-one", "acme-two"):
        call = ToolCall(Caller("acme-web", "s", "t"), kind, spec.name, name, {})
        with session_scope() as session:
            auth = authorize(session, call, spec)
        assert _target(vault, adapter, auth, "acme-web").secret.reveal() == f"pat-{name}"
