"""Service adapters. Adding a service: one module here, one entry below, tests."""

from ringkeeper.adapters import n8n, supabase
from ringkeeper.adapters.base import Adapter
from ringkeeper.models import InstanceType

ADAPTERS: dict[InstanceType, Adapter] = {
    supabase.ADAPTER.service: supabase.ADAPTER,
    n8n.ADAPTER.service: n8n.ADAPTER,
}
