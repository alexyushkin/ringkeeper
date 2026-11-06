"""Every business system with a table of its own (see adapters.vendor_spec)."""

from ringkeeper.adapters.vendor_spec import Vendor
from ringkeeper.adapters.vendors import (
    accounting,
    ads,
    cms,
    crm,
    field_service,
    identity,
    microsoft,
    notion,
    payments,
    social,
)

ALL: tuple[Vendor, ...] = (
    *identity.VENDORS,
    *microsoft.VENDORS,
    *crm.VENDORS,
    *field_service.VENDORS,
    *accounting.VENDORS,
    *cms.VENDORS,
    *payments.VENDORS,
    *social.VENDORS,
    *ads.VENDORS,
    *notion.VENDORS,
)
