"""Every business system with a table of its own (see adapters.vendor_spec)."""

from ringkeeper.adapters.vendor_spec import Vendor
from ringkeeper.adapters.vendors import (
    accounting,
    cms,
    crm,
    field_service,
    identity,
    microsoft,
    payments,
)

ALL: tuple[Vendor, ...] = (
    *identity.VENDORS,
    *microsoft.VENDORS,
    *crm.VENDORS,
    *field_service.VENDORS,
    *accounting.VENDORS,
    *cms.VENDORS,
    *payments.VENDORS,
)
