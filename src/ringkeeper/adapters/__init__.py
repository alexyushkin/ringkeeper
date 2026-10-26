"""Service adapters. Adding a service: one module here, one entry below, tests."""

from ringkeeper.adapters import airtable, gmail, mcp, n8n, supabase, twilio, vercel
from ringkeeper.adapters.base import Adapter
from ringkeeper.models import InstanceType

ADAPTERS: dict[InstanceType, Adapter] = {
    module.ADAPTER.service: module.ADAPTER
    for module in (supabase, n8n, airtable, vercel, twilio, gmail, mcp)
}
