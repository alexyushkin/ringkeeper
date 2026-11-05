"""Every business system with a table of its own (see adapters.vendor_spec)."""

from ringkeeper.adapters.vendor_spec import Vendor
from ringkeeper.adapters.vendors import identity

ALL: tuple[Vendor, ...] = (*identity.VENDORS,)
