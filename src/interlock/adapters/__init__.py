"""Service adapters. Adding a service: one module here, one entry below, tests."""

from interlock.adapters import n8n, supabase
from interlock.adapters.base import Adapter
from interlock.models import InstanceType

ADAPTERS: dict[InstanceType, Adapter] = {
    supabase.ADAPTER.service: supabase.ADAPTER,
    n8n.ADAPTER.service: n8n.ADAPTER,
}
