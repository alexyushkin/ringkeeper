"""The "Test connection" button: one read-only call through the connection's adapter.

Supabase lists the tables (in a read-only transaction, like every read);
n8n lists one workflow; Airtable and Vercel ask who the token belongs to;
Twilio lists one phone number; Gmail signs in and opens the inbox. Nothing is
written to the service. The result is
saved on the connection (last_test_*) and audited as a change by the owner.
"""

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.orm import Session

from ringkeeper.adapters import ADAPTERS, airtable, gmail, n8n, supabase, twilio, vercel
from ringkeeper.adapters.base import JSON, Target, ToolError
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import InstanceType
from ringkeeper.scrub import scrub
from ringkeeper.services import admin_audit, instances
from ringkeeper.vault import get_vault

TIMEOUT_SECONDS = 20
# What the REST adapters say when the service turns the credential down.
REFUSED = re.compile(r"returned 40[13]:")


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str


async def _probe(kind: InstanceType, target: Target) -> str:
    match kind:
        case InstanceType.SUPABASE:
            result: JSON = await supabase.list_tables.handler(target, schema=None)
            count = len(result["tables"])
            return f"Connected: read {count} table{'s' if count != 1 else ''}."
        case InstanceType.N8N:
            result = await n8n.list_workflows.handler(target, limit=1)
            return "Connected to n8n: the API key works."
        case InstanceType.AIRTABLE:
            me = await airtable.whoami(target)
            return f"Connected to Airtable as {me.get('email') or me.get('id')}."
        case InstanceType.VERCEL:
            user = cast(JSON, (await vercel.whoami(target)).get("user") or {})
            return f"Connected to Vercel as {user.get('username') or user.get('email')}."
        case InstanceType.TWILIO:
            result = await twilio.list_phone_numbers.handler(target, limit=1)
            return "Connected to Twilio: the key works."
        case InstanceType.GMAIL:
            return await gmail.probe(target)
        case _:
            raise ToolError(f"Testing {kind} connections isn't supported yet.")


def test_connection(session: Session, name: str, *, by: str) -> ProbeResult:
    instance = instances.get_instance(session, name)
    try:
        secret = get_vault().resolve(instance.secret_ref)
        if instance.type in ADAPTERS:
            secret.also_scrub(*ADAPTERS[instance.type].secret_fragments(secret.reveal()))
        target = Target(instance.name, instance.env, instance.base_url, secret)
        message = asyncio.run(
            asyncio.wait_for(_probe(instance.type, target), timeout=TIMEOUT_SECONDS)
        )
        result = ProbeResult(True, message)
    except TimeoutError:
        result = ProbeResult(False, "No answer within 20 seconds. Check the address.")
    except (ToolError, RingkeeperError) as error:
        message = scrub(str(error))[:500]
        if REFUSED.search(message):
            message = (
                "The service didn't accept this key. Check that it was copied whole and "
                f"hasn't expired or been revoked. ({message})"
            )
        result = ProbeResult(False, message)
    instance.last_test_at = datetime.now(UTC)
    instance.last_test_ok = result.ok
    instance.last_test_message = result.message
    admin_audit.record(
        session, "connection.test", f"tested {name}: {'ok' if result.ok else 'failed'}", by=by,
        instance=name, details={"ok": result.ok},
    )  # fmt: skip
    session.flush()
    return result
