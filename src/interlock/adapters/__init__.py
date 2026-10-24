"""Service adapters. Adding a service: one module here, one entry below, tests."""

from interlock.adapters import supabase
from interlock.adapters.base import Adapter
from interlock.models import InstanceType

ADAPTERS: dict[InstanceType, Adapter] = {
    supabase.ADAPTER.service: supabase.ADAPTER,
}
