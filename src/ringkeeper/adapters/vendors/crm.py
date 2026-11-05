"""CRMs: HubSpot, Salesforce, Pipedrive and Keap, through their own APIs.

The rule for all four: reads run, contact and deal edits wait for approval, and bulk changes,
deletes, merges, sending email or SMS and sequence enrollment need the name typed.

Left out on purpose. HubSpot: SMTP tokens (they answer with a password), private app usage,
batch reads and list membership. Salesforce: anything that runs Apex or a composite of
arbitrary requests (/composite, /tooling, /apexrest, /process), and every other invocable
action. Keap: user edits, orders, subscriptions and automation goals (achieving one starts an
automation). Pipedrive: mail, users and webhooks.

Fields an edit can't take, so a delete or a change of who may see a record can't ride along:
Pipedrive's is_deleted, visible_to and marketing_status, and an activity's attendees (outside
people, and its docs don't say whether they're emailed).

In Keap, a tag can start an automation, so tagging needs the name.
"""

from typing import cast

from ringkeeper.adapters.vendor_spec import (
    JSON,
    Auth,
    Secret,
    Snapshot,
    Vendor,
    final,
    held,
    reads,
)

CATEGORY = "CRM"
SENDS = "sends email to real people"
ENROLLS = "starts a sequence of emails to real people"
MERGES = "merges two records, and that can't be split again"
BULK = "changes many records at once"
PAGE = ("limit", "after")


# --- HubSpot ---

HS_OBJECT = ("properties", "associations")
HS_SEARCH = ("query", "filterGroups", "sorts", "properties", "limit", "after")
HS_READ = ("properties", "associations", "idProperty")
HS_EDIT = ("properties",)


def _hubspot_who(found: JSON) -> str:
    kind = "sandbox" if found.get("accountType") == "SANDBOX" else "account"
    return f"{kind} {found.get('portalId')}"


def _hubspot_back(read: str, says: str) -> Snapshot:
    """An edit sets some properties: those are kept, and a blank clears one again."""
    return Snapshot(read, nest="properties", query="properties", empty="", says=says)


HUBSPOT = Vendor(
    "hubspot", "HubSpot", CATEGORY, "Contacts, companies, deals, sequences",
    "https://api.hubapi.com",
    provider="hubspot", secrets=(),
    probe="get_account", who=_hubspot_who,
    words=("hubspot", "crm", "contacts", "companies", "deals", "sequences", "marketing email"),
    key_url="https://developers.hubspot.com/docs/apps/developer-platform/build-apps/create-an-app",
    steps="Add your HubSpot app in Developer apps, then sign in from this tile.",
    note="Reading runs. Creating and editing contacts, companies, deals, notes and tasks waits "
    "for approval. Deletes, merges, bulk edits, sending email and enrolling someone in a "
    "sequence need the name typed. Edits here can't be undone.",
    ops=(
        reads("get_account", "/account-info/v3/details", "Which HubSpot account this is"),
        reads("search_contacts", "/crm/v3/objects/contacts/search", "Find contacts", *HS_SEARCH,
              method="POST"),
        reads("get_contact", "/crm/v3/objects/contacts/{contact_id}", "Read one contact",
              *HS_READ),
        reads("search_companies", "/crm/v3/objects/companies/search", "Find companies",
              *HS_SEARCH, method="POST"),
        reads("get_company", "/crm/v3/objects/companies/{company_id}", "Read one company",
              *HS_READ),
        reads("search_deals", "/crm/v3/objects/deals/search", "Find deals", *HS_SEARCH,
              method="POST"),
        reads("get_deal", "/crm/v3/objects/deals/{deal_id}", "Read one deal", *HS_READ),
        reads("search_tasks", "/crm/v3/objects/tasks/search", "Find tasks", *HS_SEARCH,
              method="POST"),
        reads("list_owners", "/crm/v3/owners", "Who can own a record", "email", *PAGE),
        reads("list_pipelines", "/crm/v3/pipelines/{object_type}",
              "An object's pipelines and stages"),
        reads("list_properties", "/crm/v3/properties/{object_type}",
              "An object's properties, to know what can be set"),
        reads("list_sequences", "/automation/v4/sequences", "A user's sequences", "userId",
              "name", *PAGE),

        # Held.
        held("create_contact", "POST", "/crm/v3/objects/contacts", "Add a contact", *HS_OBJECT,
             only=HS_OBJECT),
        held("update_contact", "PATCH", "/crm/v3/objects/contacts/{contact_id}",
             "Edit a contact", *HS_EDIT, only=HS_EDIT,
             undo=_hubspot_back("get_contact", "the contact's fields")),
        held("create_company", "POST", "/crm/v3/objects/companies", "Add a company", *HS_OBJECT,
             only=HS_OBJECT),
        held("update_company", "PATCH", "/crm/v3/objects/companies/{company_id}",
             "Edit a company", *HS_EDIT, only=HS_EDIT,
             undo=_hubspot_back("get_company", "the company's fields")),
        held("create_deal", "POST", "/crm/v3/objects/deals", "Add a deal", *HS_OBJECT,
             only=HS_OBJECT),
        held("update_deal", "PATCH", "/crm/v3/objects/deals/{deal_id}", "Edit a deal", *HS_EDIT,
             only=HS_EDIT,
             undo=_hubspot_back("get_deal", "the deal's fields")),
        held("create_note", "POST", "/crm/v3/objects/notes", "Add a note", *HS_OBJECT,
             only=HS_OBJECT),
        held("create_task", "POST", "/crm/v3/objects/tasks", "Add a task", *HS_OBJECT,
             only=HS_OBJECT),
        held("update_task", "PATCH", "/crm/v3/objects/tasks/{task_id}", "Edit a task", *HS_EDIT,
             only=HS_EDIT),

        # The name typed.
        final("delete_contact", "DELETE", "/crm/v3/objects/contacts/{contact_id}",
              "Delete a contact", "deletes a contact"),
        final("delete_company", "DELETE", "/crm/v3/objects/companies/{company_id}",
              "Delete a company", "deletes a company"),
        final("delete_deal", "DELETE", "/crm/v3/objects/deals/{deal_id}", "Delete a deal",
              "deletes a deal"),
        final("merge_contacts", "POST", "/crm/v3/objects/contacts/merge", "Merge two contacts",
              MERGES, "primaryObjectId", "objectIdToMerge",
              only=("primaryObjectId", "objectIdToMerge")),
        final("merge_companies", "POST", "/crm/v3/objects/companies/merge", "Merge two companies",
              MERGES, "primaryObjectId", "objectIdToMerge",
              only=("primaryObjectId", "objectIdToMerge")),
        final("update_contacts", "POST", "/crm/v3/objects/contacts/batch/update",
              "Edit many contacts at once", BULK, "inputs", only=("inputs",)),
        final("enroll_in_sequence", "POST", "/automation/v4/sequences/enrollments",
              "Enroll a contact in a sequence", ENROLLS, "userId", "contactId", "sequenceId",
              "senderEmail", "senderAliasAddress",
              only=("contactId", "sequenceId", "senderEmail", "senderAliasAddress")),
        final("send_email", "POST", "/marketing/v4/email/single-send", "Send a marketing email",
              SENDS, "emailId", "message", "contactProperties", "customProperties",
              only=("emailId", "message", "contactProperties", "customProperties")),
    ),
)  # fmt: skip


# --- Salesforce ---

SF_VERSION = "v68.0"
# Any My Domain address: acme.my.salesforce.com, acme--uat.sandbox.my.salesforce.com,
# acme-dev-ed.develop.my.salesforce.com. The token goes here, so nothing else is accepted.
SF_HOST = r"^[a-z0-9][a-z0-9-]{0,80}(\.[a-z0-9-]{1,40})?\.my\.salesforce\.com$"
SF_FIELDS = ("fields",)
SF_CONTACT = (
    "FirstName", "LastName", "Email", "Phone", "MobilePhone", "Title", "Department", "AccountId",
    "Description", "MailingStreet", "MailingCity", "MailingState", "MailingPostalCode",
    "MailingCountry", "LeadSource", "OwnerId",
)  # fmt: skip
SF_ACCOUNT = (
    "Name", "Phone", "Website", "Industry", "Type", "Description", "BillingStreet", "BillingCity",
    "BillingState", "BillingPostalCode", "BillingCountry", "NumberOfEmployees", "AnnualRevenue",
    "OwnerId",
)  # fmt: skip
SF_OPPORTUNITY = (
    "Name", "StageName", "CloseDate", "Amount", "Description", "NextStep", "Probability",
    "AccountId", "Type", "LeadSource", "OwnerId",
)  # fmt: skip
SF_LEAD = (
    "FirstName", "LastName", "Company", "Email", "Phone", "Title", "Status", "Description",
    "LeadSource", "OwnerId",
)  # fmt: skip
SF_TASK = ("Subject", "Status", "Priority", "ActivityDate", "Description", "WhoId", "WhatId",
           "OwnerId")  # fmt: skip


def _salesforce_who(found: JSON) -> str:
    calls = cast(JSON, found.get("DailyApiRequests") or {})
    return f"{calls.get('Remaining')} of {calls.get('Max')} API calls left today"


SALESFORCE = Vendor(
    "salesforce", "Salesforce", CATEGORY, "Accounts, contacts, leads, opportunities",
    f"https://{{instance_host}}/services/data/{SF_VERSION}",
    provider="salesforce",
    secrets=(
        Secret("instance_host", "Org address", "Comes from the sign-in, like "
               "acme.my.salesforce.com", hidden=False, pattern=SF_HOST),
    ),
    probe="get_limits", who=_salesforce_who,
    words=("salesforce", "sfdc", "crm", "accounts", "contacts", "leads", "opportunities", "soql"),
    key_url="https://help.salesforce.com/s/articleView"
    "?id=xcloud.create_a_local_external_client_app.htm",
    steps="Add your Salesforce External Client App in Developer apps, then sign in from this "
    "tile.",
    note="Reading runs, SOQL included. Creating and editing records waits for approval, and an "
    "edit can be undone. Deletes and sending email need the name typed. Nothing here can run "
    "Apex or a batch of arbitrary requests.",
    ops=(
        reads("get_limits", "/limits", "This org's limits and what's left of them"),
        reads("query", "/query", "Run a SOQL query", "q"),
        reads("query_more", "/query/{locator}", "The next page of a SOQL query"),
        reads("search", "/search", "Run a SOSL search", "q"),
        reads("describe_object", "/sobjects/{object_name}/describe",
              "An object's fields, to know what can be set"),
        reads("get_contact", "/sobjects/Contact/{record_id}", "Read one contact", *SF_FIELDS),
        reads("get_account", "/sobjects/Account/{record_id}", "Read one account", *SF_FIELDS),
        reads("get_opportunity", "/sobjects/Opportunity/{record_id}", "Read one opportunity",
              *SF_FIELDS),
        reads("get_lead", "/sobjects/Lead/{record_id}", "Read one lead", *SF_FIELDS),
        reads("get_task", "/sobjects/Task/{record_id}", "Read one task", *SF_FIELDS),

        # Held. An undo puts back the standard fields named above.
        held("create_contact", "POST", "/sobjects/Contact", "Add a contact", "LastName",
             "FirstName", "Email", "AccountId"),
        held("update_contact", "PATCH", "/sobjects/Contact/{record_id}", "Edit a contact",
             undo=Snapshot("get_contact", SF_CONTACT, says="the contact's standard fields")),
        held("create_account", "POST", "/sobjects/Account", "Add an account", "Name"),
        held("update_account", "PATCH", "/sobjects/Account/{record_id}", "Edit an account",
             undo=Snapshot("get_account", SF_ACCOUNT, says="the account's standard fields")),
        held("create_opportunity", "POST", "/sobjects/Opportunity", "Add an opportunity", "Name",
             "StageName", "CloseDate", "AccountId", "Amount"),
        held("update_opportunity", "PATCH", "/sobjects/Opportunity/{record_id}",
             "Edit an opportunity",
             undo=Snapshot("get_opportunity", SF_OPPORTUNITY,
                           says="the opportunity's standard fields")),
        held("create_lead", "POST", "/sobjects/Lead", "Add a lead", "LastName", "Company"),
        held("update_lead", "PATCH", "/sobjects/Lead/{record_id}", "Edit a lead",
             undo=Snapshot("get_lead", SF_LEAD, says="the lead's standard fields")),
        held("create_task", "POST", "/sobjects/Task", "Add a task", "Subject", "WhoId", "WhatId",
             "ActivityDate"),
        held("update_task", "PATCH", "/sobjects/Task/{record_id}", "Edit a task",
             undo=Snapshot("get_task", SF_TASK, says="the task's standard fields")),
        held("create_note", "POST", "/sobjects/Note", "Add a note to a record", "ParentId",
             "Title", "Body", only=("ParentId", "Title", "Body")),

        # The name typed.
        final("delete_contact", "DELETE", "/sobjects/Contact/{record_id}", "Delete a contact",
              "deletes a contact"),
        final("delete_account", "DELETE", "/sobjects/Account/{record_id}", "Delete an account",
              "deletes an account"),
        final("delete_opportunity", "DELETE", "/sobjects/Opportunity/{record_id}",
              "Delete an opportunity", "deletes an opportunity"),
        final("delete_lead", "DELETE", "/sobjects/Lead/{record_id}", "Delete a lead",
              "deletes a lead"),
        final("delete_task", "DELETE", "/sobjects/Task/{record_id}", "Delete a task",
              "deletes a task"),
        final("send_email", "POST", "/actions/standard/emailSimple", "Send an email", SENDS,
              "inputs", only=("inputs",)),
    ),
)  # fmt: skip


# --- Pipedrive ---

PD_PERSON = (
    "name", "owner_id", "org_id", "emails", "phones", "label_ids", "postal_address", "notes",
    "im", "birthday", "job_title", "custom_fields",
)  # fmt: skip
PD_ORGANIZATION = (
    "name", "owner_id", "label_ids", "address", "website", "linkedin", "industry",
    "annual_revenue", "employee_count", "custom_fields",
)  # fmt: skip
PD_DEAL = (
    "title", "owner_id", "person_id", "org_id", "pipeline_id", "stage_id", "value", "currency",
    "status", "probability", "lost_reason", "close_time", "won_time", "lost_time",
    "expected_close_date", "label_ids", "custom_fields",
)  # fmt: skip
PD_ACTIVITY = (
    "subject", "type", "owner_id", "deal_id", "lead_id", "org_id", "project_id", "due_date",
    "due_time", "duration", "busy", "done", "location", "participants", "public_description",
    "priority", "note",
)  # fmt: skip
PD_NOTE = ("content", "lead_id", "deal_id", "person_id", "org_id", "project_id")  # not its author
PD_SEARCH = ("term", "fields", "exact_match", "limit", "cursor")
PD_LIST = ("limit", "cursor", "sort_by", "sort_direction")
MERGE_INTO = ("merge_with_id",)


def _pipedrive_who(found: JSON) -> str:
    user = cast(JSON, found.get("data") or {})
    return f"{user.get('name')} at {user.get('company_name')}"


PIPEDRIVE = Vendor(
    "pipedrive", "Pipedrive", CATEGORY, "People, organizations, deals, activities",
    "https://api.pipedrive.com",
    auth=Auth("header", "x-api-token"),
    secrets=(
        Secret("api_key", "API token", "From Personal preferences, then API, in Pipedrive"),
    ),
    probe="get_me", who=_pipedrive_who,
    words=("pipedrive", "crm", "people", "persons", "organizations", "deals", "activities"),
    key_url="https://app.pipedrive.com/settings/api",
    steps="In Pipedrive, click your account name, then Company settings, Personal preferences "
    "and API, and copy your token. It acts as you, with your permissions.",
    note="Reading runs. Creating and editing people, organizations, deals, activities and notes "
    "waits for approval, and an edit can be undone. Deletes and merges need the name typed.",
    ops=(
        reads("get_me", "/v1/users/me", "Whose token this is"),
        reads("search_persons", "/api/v2/persons/search", "Find people", *PD_SEARCH),
        reads("get_person", "/api/v2/persons/{person_id}", "Read one person"),
        reads("search_organizations", "/api/v2/organizations/search", "Find organizations",
              *PD_SEARCH),
        reads("get_organization", "/api/v2/organizations/{organization_id}",
              "Read one organization"),
        reads("search_deals", "/api/v2/deals/search", "Find deals", *PD_SEARCH),
        reads("list_deals", "/api/v2/deals", "List deals", "owner_id", "person_id", "org_id",
              "pipeline_id", "stage_id", "status", *PD_LIST),
        reads("get_deal", "/api/v2/deals/{deal_id}", "Read one deal"),
        reads("list_activities", "/api/v2/activities", "List activities", "owner_id", "deal_id",
              "person_id", "org_id", "done", *PD_LIST),
        reads("get_activity", "/api/v2/activities/{activity_id}", "Read one activity"),
        reads("list_notes", "/v1/notes", "List notes", "deal_id", "person_id", "org_id",
              "lead_id", "start", "limit"),
        reads("list_pipelines", "/api/v2/pipelines", "List pipelines"),
        reads("list_stages", "/api/v2/stages", "List stages", "pipeline_id"),

        # Held.
        held("create_person", "POST", "/api/v2/persons", "Add a person", *PD_PERSON,
             only=PD_PERSON),
        held("update_person", "PATCH", "/api/v2/persons/{person_id}", "Edit a person", *PD_PERSON,
             only=PD_PERSON,
             undo=Snapshot("get_person", ("name", "owner_id", "org_id", "emails", "phones",
                                          "label_ids"), where="data",
                           says="the person's name, owner, emails, phones and labels")),
        held("create_organization", "POST", "/api/v2/organizations", "Add an organization",
             *PD_ORGANIZATION, only=PD_ORGANIZATION),
        held("update_organization", "PATCH", "/api/v2/organizations/{organization_id}",
             "Edit an organization", *PD_ORGANIZATION, only=PD_ORGANIZATION,
             undo=Snapshot("get_organization", ("name", "owner_id", "label_ids", "website",
                                                "linkedin", "industry", "annual_revenue",
                                                "employee_count"), where="data",
                           says="the organization's main fields")),
        held("create_deal", "POST", "/api/v2/deals", "Add a deal", *PD_DEAL, only=PD_DEAL),
        held("update_deal", "PATCH", "/api/v2/deals/{deal_id}", "Edit a deal", *PD_DEAL,
             only=PD_DEAL,
             undo=Snapshot("get_deal", ("title", "owner_id", "person_id", "org_id", "pipeline_id",
                                        "stage_id", "value", "currency", "status", "probability",
                                        "expected_close_date", "label_ids"), where="data",
                           says="the deal's main fields")),
        held("create_activity", "POST", "/api/v2/activities", "Add an activity", *PD_ACTIVITY,
             only=PD_ACTIVITY),
        held("update_activity", "PATCH", "/api/v2/activities/{activity_id}", "Edit an activity",
             *PD_ACTIVITY, only=PD_ACTIVITY,
             undo=Snapshot("get_activity", ("subject", "type", "owner_id", "deal_id", "org_id",
                                            "due_date", "due_time", "duration", "busy", "done",
                                            "priority", "note"), where="data",
                           says="the activity's main fields")),
        held("create_note", "POST", "/v1/notes", "Add a note", *PD_NOTE, only=PD_NOTE),

        # The name typed.
        final("delete_person", "DELETE", "/api/v2/persons/{person_id}", "Delete a person",
              "deletes a person"),
        final("delete_organization", "DELETE", "/api/v2/organizations/{organization_id}",
              "Delete an organization", "deletes an organization"),
        final("delete_deal", "DELETE", "/api/v2/deals/{deal_id}", "Delete a deal",
              "deletes a deal"),
        final("delete_activity", "DELETE", "/api/v2/activities/{activity_id}",
              "Delete an activity", "deletes an activity"),
        final("merge_persons", "PUT", "/v1/persons/{person_id}/merge", "Merge two people", MERGES,
              *MERGE_INTO, only=MERGE_INTO),
        final("merge_organizations", "PUT", "/v1/organizations/{organization_id}/merge",
              "Merge two organizations", MERGES, *MERGE_INTO, only=MERGE_INTO),
        final("merge_deals", "PUT", "/v1/deals/{deal_id}/merge", "Merge two deals", MERGES,
              *MERGE_INTO, only=MERGE_INTO),
    ),
)  # fmt: skip


# --- Keap ---

KP_LIST = ("filter", "order_by", "page_size", "page_token")
KP_NOTE = ("title", "text", "type", "user_id", "is_pinned")
CONTACT_IDS = ("contact_ids",)


def _keap_who(found: JSON) -> str:
    return str(found.get("name") or "this account")


KEAP = Vendor(
    "keap", "Keap", CATEGORY, "Contacts, companies, opportunities, campaigns",
    "https://api.infusionsoft.com/crm/rest",
    auth=Auth("bearer"),
    secrets=(
        Secret("api_key", "Personal Access Token or Service Account Key",
               "From Settings, then API Settings, in Keap"),
    ),
    probe="get_business_profile", who=_keap_who,
    env_vars={"KEAP_API_KEY": "api_key", "KEAP_REST_API_SERVICE_ACCESS_TOKEN": "api_key"},
    words=("keap", "infusionsoft", "crm", "contacts", "opportunities", "tags", "campaigns"),
    key_url="https://developer.keap.com/docs/getting-started",
    steps="In Keap, open Settings, then API Settings, add a key and name it. The token shows "
    "once, so copy it then. A Personal Access Token acts as you. A Service Account Key has "
    "admin access, and only an admin can make one.",
    note="Reading runs. Creating and editing contacts, companies, opportunities, tasks and "
    "notes waits for approval. Deletes, merges, tags, sending email and adding someone to a "
    "campaign sequence need the name typed.",
    ops=(
        reads("get_business_profile", "/v2/businessProfile", "This business's name and details"),
        reads("list_contacts", "/v2/contacts", "Find contacts", "fields", *KP_LIST),
        reads("get_contact", "/v2/contacts/{contact_id}", "Read one contact", "fields"),
        reads("list_companies", "/v2/companies", "Find companies", "fields", *KP_LIST),
        reads("get_company", "/v2/companies/{company_id}", "Read one company", "fields"),
        reads("list_opportunities", "/v2/opportunities", "Find opportunities", "fields",
              *KP_LIST),
        reads("get_opportunity", "/v2/opportunities/{opportunity_id}", "Read one opportunity",
              "fields"),
        reads("list_stages", "/v2/opportunities/stages", "Opportunity stages", *KP_LIST),
        reads("list_tasks", "/v2/tasks", "Find tasks", "fields", *KP_LIST),
        reads("get_task", "/v2/tasks/{task_id}", "Read one task", "fields"),
        reads("list_tags", "/v2/tags", "List tags", *KP_LIST),
        reads("list_campaigns", "/v2/campaigns", "List campaigns", *KP_LIST),
        reads("list_campaign_sequences", "/v2/campaigns/{campaign_id}/sequences",
              "A campaign's sequences"),
        reads("list_users", "/v2/users", "List users", *KP_LIST),

        # Held. A plain read may leave fields out, so an undo covers the ones it answers.
        held("create_contact", "POST", "/v2/contacts", "Add a contact", "given_name",
             "family_name", "email_addresses", "phone_numbers", "addresses"),
        held("update_contact", "PATCH", "/v2/contacts/{contact_id}", "Edit a contact",
             "update_mask",
             undo=Snapshot("get_contact", ("given_name", "family_name", "middle_name",
                                           "preferred_name"), says="the contact's name")),
        held("create_company", "POST", "/v2/companies", "Add a company", "company_name"),
        held("update_company", "PATCH", "/v2/companies/{company_id}", "Edit a company",
             "update_mask",
             undo=Snapshot("get_company", ("company_name", "website", "notes"),
                           says="the company's name, website and notes")),
        held("create_opportunity", "POST", "/v2/opportunities", "Add an opportunity",
             "opportunity_title", "contact_id", "stage_id"),
        held("update_opportunity", "PATCH", "/v2/opportunities/{opportunity_id}",
             "Edit an opportunity", "update_mask",
             undo=Snapshot("get_opportunity", ("opportunity_title", "opportunity_notes",
                                               "next_action_notes", "next_action_time",
                                               "estimated_close_time", "include_in_forecast",
                                               "projected_revenue_low", "projected_revenue_high"),
                           says="the opportunity's main fields")),
        held("create_task", "POST", "/v2/tasks", "Add a task", "title", "assigned_to_user_id",
             "contact_id", "due_time"),
        held("update_task", "PATCH", "/v2/tasks/{task_id}", "Edit a task", "update_mask",
             undo=Snapshot("get_task", ("title", "description", "priority", "completed",
                                        "due_time"), says="the task's main fields")),
        held("create_note", "POST", "/v2/contacts/{contact_id}/notes", "Add a note to a contact",
             *KP_NOTE, only=KP_NOTE),

        # The name typed.
        final("delete_contact", "DELETE", "/v2/contacts/{contact_id}", "Delete a contact",
              "deletes a contact"),
        final("delete_opportunity", "DELETE", "/v2/opportunities/{opportunity_id}",
              "Delete an opportunity", "deletes an opportunity"),
        final("merge_contacts", "POST", "/v2/contacts:merge", "Merge two contacts", MERGES,
              "contact_id", "duplicate_contact_id", only=("contact_id", "duplicate_contact_id")),
        final("send_email", "POST", "/v2/emails:send", "Send an email to contacts", SENDS,
              "contacts", "subject", "html_content", "plain_content", "from_address",
              only=("contacts", "subject", "html_content", "plain_content", "from_address",
                    "user_id", "address_field")),
        final("add_to_sequence", "POST",
              "/v2/campaigns/{campaign_id}/sequences/{sequence_id}:addContacts",
              "Add contacts to a campaign sequence", ENROLLS, *CONTACT_IDS, only=CONTACT_IDS),
        final("apply_tag", "POST", "/v2/tags/{tag_id}/contacts:applyTags",
              "Tag a list of contacts", "tags contacts, which can start an automation",
              *CONTACT_IDS, only=CONTACT_IDS),
    ),
)  # fmt: skip

VENDORS = (HUBSPOT, SALESFORCE, PIPEDRIVE, KEAP)
