"""Service adapters. Adding a service: one module here, one entry below, tests."""

from ringkeeper.adapters import airtable, gmail, mcp, n8n, supabase, twilio, vercel
from ringkeeper.adapters.base import Adapter
from ringkeeper.models import InstanceType

ADAPTERS: dict[InstanceType, Adapter] = {
    module.ADAPTER.service: module.ADAPTER
    for module in (supabase, n8n, airtable, vercel, twilio, gmail, mcp)
}

# What the agent calls a service, where its type's name would mislead: the
# `supabase` type is any Postgres (Neon too). Its gateway path is /mcp/database
# (and /mcp/supabase, kept for anything that already uses it).
AGENT_NAMES = {InstanceType.SUPABASE: "database"}


def agent_name(service: InstanceType) -> str:
    return AGENT_NAMES.get(service, service.value)
