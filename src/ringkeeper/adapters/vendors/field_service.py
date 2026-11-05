"""Field service: Jobber, Housecall Pro and ServiceTitan, through their own APIs.

Reads run. Creating and editing clients, jobs and estimates waits for approval. Anything that
tells a customer, any delete, and anything bulk needs the name typed.

Jobber is GraphQL, so a call's values go in `body`. Its schema is only shown to a signed-in
developer: these were checked against the schema in Jobber's own app template (2022) and its
changelog up to 2026-09-25, and the table says so as unverified. Mutations can't be undone
here: `mutation` has no `undo`, and a Snapshot's read can't send GraphQL variables.

Housecall Pro's API has no way to send an invoice or a message. It tells customers in two
other ways, as flags on a schedule call. Their defaults aren't documented, so leaving one out
counts as telling. Left out on purpose: webhooks, app enable/disable, company settings, and
the invoice preview (it answers with HTML).

ServiceTitan signs in with a client ID and secret traded for a short-lived token, which
Vendor.auth can't do, so it isn't in VENDORS. The table is ready for when it can. Left out:
employee and technician edits and account actions (they reset passwords and change roles),
writes to invoices and payments, and customer `active`, credit and payment terms fields.
"""

from typing import cast

from ringkeeper.adapters.vendor_spec import (
    JSON,
    Auth,
    Exchange,
    Secret,
    Snapshot,
    Vendor,
    final,
    held,
    mutation,
    query,
    reads,
)

CATEGORY = "CRM"


# --- Jobber ---

JOBBER_VERSION = "2026-09-25"
PAGE = ("first", "after")
PAGE_INFO = "pageInfo { hasNextPage endCursor }"
ERRORS = "userErrors { message path }"
CLIENT = """
id name firstName lastName companyName isCompany isLead isArchived
emails { address primary } phones { number primary }
billingAddress { street city province postalCode country }
"""
JOB = "id jobNumber title jobStatus startAt endAt total client { id name }"
QUOTE = "id quoteNumber title quoteStatus amounts { total } client { id name }"
INVOICE = """
id invoiceNumber subject invoiceStatus dueDate amounts { total invoiceBalance } client { id name }
"""
REQUEST = "id title requestStatus createdAt client { id name }"
VISIT = "id title startAt endAt visitStatus client { id name }"
LIST_ARGS = "$first: Int = 25, $after: String"
SEARCH_ARGS = f"{LIST_ARGS}, $searchTerm: String"
SEARCH_USE = "first: $first, after: $after, searchTerm: $searchTerm"


def _found(field: str, selection: str) -> str:
    return (
        f"query($first: Int = 25, $after: String, $searchTerm: String) "
        f"{{ {field}({SEARCH_USE}) {{ nodes {{ {selection} }} {PAGE_INFO} totalCount }} }}"
    )


def _one(field: str, selection: str) -> str:
    return f"query($id: EncodedId!) {{ {field}(id: $id) {{ {selection} }} }}"


def _jobber_who(found: JSON) -> str:
    account = found.get("account")
    return str(cast(JSON, account).get("name")) if isinstance(account, dict) else "this account"


JOBBER = Vendor(
    "jobber", "Jobber", CATEGORY, "Clients, jobs, quotes, invoices",
    "https://api.getjobber.com/api",
    provider="jobber", secrets=(), graphql_path="/graphql",
    headers={"X-JOBBER-GRAPHQL-VERSION": JOBBER_VERSION},
    probe="get_account", who=_jobber_who,
    words=("jobber", "field service", "crm", "clients", "jobs", "quotes", "invoices", "visits"),
    key_url="https://developer.getjobber.com/apps",
    steps="Add your Jobber app in Developer apps, then sign in from this tile.",
    note="Reading runs. Creating and editing clients, jobs and quotes waits for approval. "
    "Deleting or archiving, and emailing a quote or invoice, need the name typed. Edits here "
    "can't be undone.",
    unverified="Jobber shows its current schema only to signed-in developers. Operations were "
    "checked against the schema in its app template (2022) and its changelog to 2026-09-25.",
    ops=(
        query("get_account", "Which Jobber account this is", "query { account { id name } }"),
        query("list_clients", "Find clients", _found("clients", CLIENT), "searchTerm", *PAGE),
        query("get_client", "Read one client", _one("client", CLIENT), "id"),
        query("list_jobs", "Find jobs", _found("jobs", JOB), "searchTerm", *PAGE),
        query("get_job", "Read one job", _one("job", JOB), "id"),
        query("list_quotes", "Find quotes", _found("quotes", QUOTE), "searchTerm", *PAGE),
        query("get_quote", "Read one quote", _one("quote", QUOTE), "id"),
        query("list_invoices", "Find invoices", _found("invoices", INVOICE), "searchTerm", *PAGE),
        query("get_invoice", "Read one invoice", _one("invoice", INVOICE), "id"),
        query("list_requests", "Find work requests", _found("requests", REQUEST), "searchTerm",
              *PAGE),
        query("list_visits", "List visits",
              f"query({LIST_ARGS}) {{ visits(first: $first, after: $after) "
              f"{{ nodes {{ {VISIT} }} {PAGE_INFO} }} }}", *PAGE),
        query("list_users", "List the people on this account",
              f"query({LIST_ARGS}) {{ users(first: $first, after: $after) "
              f"{{ nodes {{ id name {{ full }} email {{ raw }} }} {PAGE_INFO} }} }}", *PAGE),

        # Held.
        mutation("create_client", "Add a client",
                 "mutation($input: ClientCreateInput!) { clientCreate(input: $input) "
                 f"{{ client {{ id name }} {ERRORS} }} }}", "input"),
        mutation("edit_client", "Edit a client",
                 "mutation($clientId: EncodedId!, $input: ClientEditInput!) "
                 "{ clientEdit(clientId: $clientId, input: $input) "
                 f"{{ client {{ id name }} {ERRORS} }} }}", "clientId", "input"),
        mutation("add_client_note", "Add a note to a client",
                 "mutation($clientId: EncodedId!, $input: ClientCreateNoteInput!) "
                 "{ clientCreateNote(clientId: $clientId, input: $input) "
                 f"{{ clientNote {{ id }} {ERRORS} }} }}", "clientId", "input"),
        mutation("create_job", "Add a job",
                 "mutation($input: JobCreateAttributes!) { jobCreate(input: $input) "
                 f"{{ job {{ id jobNumber title }} {ERRORS} }} }}", "input"),
        mutation("edit_job", "Edit a job",
                 "mutation($jobId: EncodedId!, $input: JobEditInput!) "
                 "{ jobEdit(jobId: $jobId, input: $input) "
                 f"{{ job {{ id jobNumber title }} {ERRORS} }} }}", "jobId", "input"),
        mutation("add_job_note", "Add a note to a job",
                 "mutation($jobId: EncodedId!, $input: JobCreateNoteInput!) "
                 "{ jobCreateNote(jobId: $jobId, input: $input) "
                 f"{{ jobNote {{ id }} {ERRORS} }} }}", "jobId", "input"),
        mutation("create_quote", "Add a quote, as a draft",
                 "mutation($attributes: QuoteCreateAttributes!) "
                 "{ quoteCreate(attributes: $attributes) "
                 f"{{ quote {{ id quoteNumber title }} {ERRORS} }} }}", "attributes"),
        mutation("edit_quote", "Edit a quote",
                 "mutation($quoteId: EncodedId!, $attributes: QuoteEditAttributes!) "
                 "{ quoteEdit(quoteId: $quoteId, attributes: $attributes) "
                 f"{{ quote {{ id quoteNumber title }} {ERRORS} }} }}", "quoteId", "attributes"),

        # The name typed.
        mutation("archive_client", "Archive a client",
                 "mutation($clientId: EncodedId!) { clientArchive(clientId: $clientId) "
                 f"{{ client {{ id }} {ERRORS} }} }}", "clientId", why="archives a client"),
        mutation("delete_jobs", "Delete jobs",
                 "mutation($jobIds: [EncodedId!]!) { jobDelete(jobIds: $jobIds) "
                 f"{{ {ERRORS} }} }}", "jobIds", why="deletes jobs"),
        mutation("delete_quote", "Delete a quote",
                 "mutation($quoteId: EncodedId!) { quoteDelete(quoteId: $quoteId) "
                 f"{{ {ERRORS} }} }}", "quoteId", why="deletes a quote"),
        mutation("email_quote_or_invoice", "Email a quote or invoice to a client",
                 "mutation($input: WorkObjectSendEmailInput!) "
                 f"{{ workObjectSendEmail(input: $input) {{ message {ERRORS} }} }}", "input",
                 why="emails a quote or invoice to a client"),
    ),
)  # fmt: skip


# --- Housecall Pro ---

HCP_LIST = ("page", "page_size", "sort_by", "sort_direction", "location_ids", "expand")
HCP_CUSTOMER = (
    "first_name", "last_name", "email", "company", "notifications_enabled", "mobile_number",
    "home_number", "work_number", "tags", "lead_source", "notes",
)  # fmt: skip
HCP_LINE_ITEM = (
    "name", "description", "unit_price", "unit_cost", "quantity", "kind", "taxable",
    "service_item_id", "service_item_type",
)  # fmt: skip
HCP_SCHEDULE = (
    "start_time", "end_time", "arrival_window_in_minutes", "notify", "notify_pro",
    "dispatched_employees",
)  # fmt: skip


def _schedule_why(args: JSON) -> str | None:
    """Telling people is a choice in the call: only `false` is a clear no."""
    body = cast(JSON, args.get("body") or {})
    told = [who for flag, who in (("notify", "customer"), ("notify_pro", "employee"))
            if body.get(flag) is not False]  # fmt: skip
    return f"may tell the {' and the '.join(told)} about the new time" if told else None


def _estimate_why(args: JSON) -> str | None:
    schedule = cast(JSON, args.get("body") or {}).get("schedule")
    if not isinstance(schedule, dict):
        return None
    tells = cast(JSON, schedule).get("notify_customer") is not False
    return "may tell the customer it's scheduled" if tells else None


def _company_who(found: JSON) -> str:
    return str(found.get("name") or "this company")


HOUSECALL_PRO = Vendor(
    "housecallpro", "Housecall Pro", CATEGORY, "Customers, jobs, estimates, invoices",
    "https://api.housecallpro.com",
    auth=Auth("bearer", scheme="Token"),
    secrets=(
        Secret("api_key", "API key", "From My Apps, then API Key Management, in Housecall Pro"),
    ),
    probe="get_company", who=_company_who,
    words=("housecall pro", "housecall", "hcp", "field service", "customers", "jobs",
           "estimates", "invoices"),
    key_url="https://help.housecallpro.com/en/articles/8505035-api-overview",
    steps="Sign in to Housecall Pro as an admin, open My Apps, then All Apps, and find API Key "
    "Management. Generate a key, name it and choose full or read-only access. The API needs "
    "the MAX plan or above.",
    note="Reading runs. Creating and editing customers, jobs and leads waits for approval. "
    "Moving a job's time needs the name typed unless the call says not to tell anyone. "
    "Deletes, dispatching, approving estimates and bulk edits need the name typed.",
    ops=(
        reads("get_company", "/company", "This company's details"),
        reads("list_customers", "/customers", "Find customers", "q", *HCP_LIST),
        reads("get_customer", "/customers/{customer_id}", "Read one customer", "expand"),
        reads("list_customer_addresses", "/customers/{customer_id}/addresses",
              "A customer's addresses"),
        reads("list_employees", "/employees", "List employees", "page", "page_size", "sort_by",
              "sort_direction", "location_ids"),
        reads("list_jobs", "/jobs", "Find jobs", "customer_id", "employee_ids", "work_status",
              "scheduled_start_min", "scheduled_start_max", *HCP_LIST),
        reads("get_job", "/jobs/{job_id}", "Read one job", "expand"),
        reads("list_job_line_items", "/jobs/{job_id}/line_items", "A job's line items"),
        reads("list_job_appointments", "/jobs/{job_id}/appointments", "A job's appointments"),
        reads("list_estimates", "/estimates", "Find estimates", *HCP_LIST),
        reads("get_estimate", "/estimates/{estimate_id}", "Read one estimate"),
        reads("list_leads", "/leads", "Find leads", "page", "page_size", "sort_by",
              "sort_direction", "location_ids"),
        reads("get_lead", "/leads/{lead_id}", "Read one lead"),
        reads("list_invoices", "/invoices", "Find invoices", "customer_uuid", "status",
              "due_at_min", "due_at_max", "page", "page_size"),
        reads("get_invoice", "/api/invoices/{invoice_uuid}", "Read one invoice"),

        # Held.
        held("create_customer", "POST", "/customers", "Add a customer", *HCP_CUSTOMER,
             "addresses", only=(*HCP_CUSTOMER, "addresses")),
        held("update_customer", "PUT", "/customers/{customer_id}", "Edit a customer",
             *HCP_CUSTOMER, only=HCP_CUSTOMER,
             undo=Snapshot("get_customer", HCP_CUSTOMER, says="the customer's details")),
        held("create_job", "POST", "/jobs", "Add a job", "customer_id", "address_id", "schedule",
             "line_items", "assigned_employee_ids", "notes"),
        held("add_job_note", "POST", "/jobs/{job_id}/notes", "Add a note to a job", "content",
             only=("content",)),
        held("add_job_line_item", "POST", "/jobs/{job_id}/line_items", "Add a line item to a job",
             *HCP_LINE_ITEM, only=HCP_LINE_ITEM),
        held("update_appointment", "PUT", "/jobs/{job_id}/appointments/{appointment_id}",
             "Move a job appointment", "start_time", "end_time", "arrival_window_minutes",
             "dispatched_employees_ids",
             only=("start_time", "end_time", "arrival_window_minutes",
                   "dispatched_employees_ids")),
        held("create_lead", "POST", "/leads", "Add a lead", "customer_id", "customer",
             "address_id", "note"),
        held("convert_lead", "POST", "/leads/{lead_id}/convert",
             "Turn a lead into an estimate or job", "type", only=("type",)),

        # The name typed, for what a call says.
        final("create_estimate", "POST", "/estimates", "Add an estimate", _estimate_why,
              "customer_id", "address_id", "options", "schedule", "note"),
        final("reschedule_job", "PUT", "/jobs/{job_id}/schedule", "Change a job's time",
              _schedule_why, *HCP_SCHEDULE, only=HCP_SCHEDULE),

        # The name typed.
        final("dispatch_job", "PUT", "/jobs/{job_id}/dispatch", "Send a job to employees",
              "sends a job to employees", "dispatched_employees",
              only=("dispatched_employees",)),
        final("approve_estimate_options", "POST", "/estimates/options/approve",
              "Mark estimate options approved",
              "approves an estimate, which can copy it to a new job", "option_ids",
              only=("option_ids",)),
        final("update_job_line_items", "PUT", "/jobs/{job_id}/line_items/bulk_update",
              "Replace a job's line items", "changes a job's line items in bulk", "line_items",
              "append_line_items", only=("line_items", "append_line_items")),
        final("delete_appointment", "DELETE", "/jobs/{job_id}/appointments/{appointment_id}",
              "Delete a job appointment", "deletes an appointment"),
    ),
)  # fmt: skip


# --- ServiceTitan ---

ST_PAGE = ("page", "pageSize", "includeTotal", "sort")
ST_CUSTOMER = ("name", "type", "address", "customFields", "doNotMail", "doNotService",
               "tagTypeIds")  # fmt: skip
ST_LOCATION = ("name", "address", "customFields", "tagTypeIds")
ST_LEAD = (
    "campaignId", "priority", "businessUnitId", "jobTypeId", "summary", "leadCustomerName",
    "leadPhone", "leadEmail", "leadStreet", "leadUnit", "leadCity", "leadState", "leadZip",
    "leadCountry",
)  # fmt: skip
ST_JOB = ("priority", "jobTypeId", "summary", "summaryOfWork", "customFields", "tagTypeIds",
          "customerPo")  # fmt: skip
ST_NOTE = ("text", "pinToTop")


def _titan_who(found: JSON) -> str:
    units = found.get("data")
    count = len(cast(list[JSON], units)) if isinstance(units, list) else 0
    return f"{count} business units"


def _titan_headers(fields: JSON) -> dict[str, str]:
    return {"Authorization": str(fields["access_token"]), "ST-App-Key": str(fields["app_key"])}


SERVICETITAN = Vendor(
    "servicetitan", "ServiceTitan", CATEGORY, "Customers, jobs, estimates, invoices",
    "https://api.servicetitan.io",
    auth=Auth("custom", custom=_titan_headers),
    exchange=Exchange(
        "https://auth.servicetitan.io/connect/token",
        {"grant_type": "=client_credentials", "client_id": "client_id",
         "client_secret": "client_secret"},
        into="access_token",
    ),
    secrets=(
        Secret("client_id", "Client ID", "From your app in the ServiceTitan Developer Portal"),
        Secret("client_secret", "Client secret", "Shown once, when it's generated"),
        Secret("app_key", "Application key", "From your app in the Developer Portal"),
        Secret("default_account", "Tenant ID", "The number of your ServiceTitan account",
               hidden=False, pattern=r"^[0-9]{1,20}$"),
    ),
    probe="list_business_units", who=_titan_who,
    words=("servicetitan", "service titan", "field service", "customers", "jobs", "estimates",
           "invoices"),
    key_url="https://developer.servicetitan.io/",
    steps="In the ServiceTitan Developer Portal, register an app, then connect it to your "
    "account to get a Client ID, secret and application key.",
    note="Reading runs. Creating and editing customers, leads and jobs waits for approval, and "
    "an edit can be undone. Cancelling a job, selling an estimate and deletes need the name "
    "typed.",
    ops=(
        reads("list_business_units", "/settings/v2/tenant/{account}/business-units",
              "List business units", "name", "active", *ST_PAGE),
        reads("list_technicians", "/settings/v2/tenant/{account}/technicians", "List technicians",
              "name", "active", *ST_PAGE),
        reads("list_customers", "/crm/v2/tenant/{account}/customers", "Find customers", "name",
              "phone", "street", "city", "zip", "active", *ST_PAGE),
        reads("get_customer", "/crm/v2/tenant/{account}/customers/{customer_id}",
              "Read one customer"),
        reads("list_locations", "/crm/v2/tenant/{account}/locations", "Find locations", "name",
              "customerId", "street", "city", "zip", *ST_PAGE),
        reads("get_location", "/crm/v2/tenant/{account}/locations/{location_id}",
              "Read one location"),
        reads("list_leads", "/crm/v2/tenant/{account}/leads", "Find leads", "customerId",
              "status", "leadCustomerName", "leadPhone", *ST_PAGE),
        reads("get_lead", "/crm/v2/tenant/{account}/leads/{lead_id}", "Read one lead"),
        reads("list_jobs", "/jpm/v2/tenant/{account}/jobs", "Find jobs", "number", "jobStatus",
              "customerId", "locationId", "technicianId", "appointmentStartsOnOrAfter",
              "appointmentStartsBefore", *ST_PAGE),
        reads("get_job", "/jpm/v2/tenant/{account}/jobs/{job_id}", "Read one job"),
        reads("list_appointments", "/jpm/v2/tenant/{account}/appointments", "Find appointments",
              "jobId", "status", "startsOnOrAfter", "startsBefore", "technicianId", "customerId",
              *ST_PAGE),
        reads("list_estimates", "/sales/v2/tenant/{account}/estimates", "Find estimates", "jobId",
              "status", "soldAfter", "soldBefore", *ST_PAGE),
        reads("get_estimate", "/sales/v2/tenant/{account}/estimates/{estimate_id}",
              "Read one estimate"),
        reads("list_invoices", "/accounting/v2/tenant/{account}/invoices", "Find invoices",
              "jobId", "customerId", "number", "statuses", "invoicedOnOrAfter",
              "invoicedOnBefore", *ST_PAGE),
        reads("list_payments", "/accounting/v2/tenant/{account}/payments", "Find payments",
              "customerId", "appliedToInvoiceIds", "paidOnAfter", "paidOnBefore", *ST_PAGE),

        # Held.
        held("create_customer", "POST", "/crm/v2/tenant/{account}/customers", "Add a customer",
             "name", "type", "address", "locations", "contacts"),
        held("update_customer", "PATCH", "/crm/v2/tenant/{account}/customers/{customer_id}",
             "Edit a customer", *ST_CUSTOMER, only=ST_CUSTOMER,
             undo=Snapshot("get_customer", ("name", "type", "doNotMail", "doNotService",
                                            "tagTypeIds"), says="the customer's name and flags")),
        held("add_customer_note", "POST", "/crm/v2/tenant/{account}/customers/{customer_id}/notes",
             "Add a note to a customer", *ST_NOTE, only=ST_NOTE),
        held("update_location", "PATCH", "/crm/v2/tenant/{account}/locations/{location_id}",
             "Edit a location", *ST_LOCATION, only=ST_LOCATION,
             undo=Snapshot("get_location", ("name", "tagTypeIds"),
                           says="the location's name and tags")),
        held("create_lead", "POST", "/crm/v2/tenant/{account}/leads", "Add a lead", "campaignId",
             "summary", "customerId", "locationId"),
        held("update_lead", "PATCH", "/crm/v2/tenant/{account}/leads/{lead_id}", "Edit a lead",
             *ST_LEAD, only=ST_LEAD,
             undo=Snapshot("get_lead", ST_LEAD, says="the lead's details")),
        held("create_job", "POST", "/jpm/v2/tenant/{account}/jobs", "Add a job", "customerId",
             "locationId", "businessUnitId", "jobTypeId", "priority", "campaignId",
             "appointments", "summary"),
        held("update_job", "PATCH", "/jpm/v2/tenant/{account}/jobs/{job_id}", "Edit a job",
             *ST_JOB, only=ST_JOB,
             undo=Snapshot("get_job", ("priority", "jobTypeId", "summary", "summaryOfWork",
                                       "tagTypeIds", "customerPo"), says="the job's details")),
        held("add_job_note", "POST", "/jpm/v2/tenant/{account}/jobs/{job_id}/notes",
             "Add a note to a job", *ST_NOTE, only=ST_NOTE),
        held("reschedule_appointment", "PATCH",
             "/jpm/v2/tenant/{account}/appointments/{appointment_id}/reschedule",
             "Move an appointment", "start", "end", "arrivalWindowStart", "arrivalWindowEnd",
             only=("start", "end", "arrivalWindowStart", "arrivalWindowEnd")),
        held("create_estimate", "POST", "/sales/v2/tenant/{account}/estimates", "Add an estimate",
             "jobId", "locationId", "name", "summary", "items"),

        # The name typed.
        final("cancel_job", "PUT", "/jpm/v2/tenant/{account}/jobs/{job_id}/cancel", "Cancel a job",
              "cancels a job", "reasonId", "memo", only=("reasonId", "memo")),
        final("delete_appointment", "DELETE",
              "/jpm/v2/tenant/{account}/appointments/{appointment_id}", "Delete an appointment",
              "deletes an appointment"),
        final("sell_estimate", "PUT", "/sales/v2/tenant/{account}/estimates/{estimate_id}/sell",
              "Mark an estimate sold", "marks an estimate sold", "soldBy", only=("soldBy",)),
    ),
)  # fmt: skip

# ServiceTitan joins when a Vendor can trade a client ID and secret for a token.
VENDORS = (JOBBER, HOUSECALL_PRO, SERVICETITAN)
