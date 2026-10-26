"""Airtable Web API: the secret is a personal access token (airtable.com/create/tokens).

The token's own scopes and base list decide what it can reach; these tools
only use what they need. Creating records changes nothing that was there;
updating and deleting do, so on production data those need the connection's
name typed to approve (see destructive.py).
"""

import re
from typing import Annotated, Any, cast
from urllib.parse import quote

import httpx2
from pydantic import Field

from ringkeeper.adapters import rest
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, read, write
from ringkeeper.models import InstanceType

API = "https://api.airtable.com/v0"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None

BASE_PATTERN = r"^app[A-Za-z0-9]{14}$"
RECORD_PATTERN = r"^rec[A-Za-z0-9]{14}$"
BaseId = Annotated[str, Field(description="The base id (starts with app).", pattern=BASE_PATTERN)]
Table = Annotated[
    str, Field(description="The table's name or id (tbl...).", min_length=1, max_length=255)
]
RecordId = Annotated[str, Field(description="The record id (rec...).", pattern=RECORD_PATTERN)]
Fields = dict[str, Any]
# Airtable takes at most 10 records per create, update or delete request.
NewRecords = Annotated[list[Fields], Field(min_length=1, max_length=10)]


def _client(target: Target) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=API,
        headers={"Authorization": f"Bearer {target.secret.reveal()}"},
        timeout=rest.TIMEOUT,
        transport=transport,
    )


def _check(value: str, pattern: str, what: str) -> str:
    if not re.fullmatch(pattern, value):
        raise ToolError(f"that isn't an Airtable {what} id")
    return value


def _table_path(base_id: str, table: str) -> str:
    # A table name may hold spaces or a slash: quote all of it, so it stays one path segment.
    return f"/{_check(base_id, BASE_PATTERN, 'base')}/{quote(table, safe='')}"


async def _call(target: Target, method: str, path: str, **kwargs: Any) -> JSON:
    return await rest.call(_client(target), "Airtable", method, path, **kwargs)


async def whoami(target: Target) -> JSON:
    """The token's user (the Test button)."""
    return await _call(target, "GET", "/meta/whoami")


@read
async def list_bases(
    target: Target,
    offset: Annotated[str | None, Field(description="offset from a previous page.")] = None,
) -> JSON:
    """List the bases this token can reach (id, name, permission level)."""
    return await _call(target, "GET", "/meta/bases", params={"offset": offset})


@read
async def list_tables(target: Target, base_id: BaseId) -> JSON:
    """List a base's tables with their fields (name and type) and views."""
    data = await _call(target, "GET", f"/meta/bases/{_check(base_id, BASE_PATTERN, 'base')}/tables")
    tables = cast(list[JSON], data.get("tables", []))
    return {
        "tables": [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "fields": [
                    {"name": f.get("name"), "type": f.get("type")}
                    for f in cast(list[JSON], t.get("fields", []))
                ],
                "views": [v.get("name") for v in cast(list[JSON], t.get("views", []))],
            }
            for t in tables
        ]
    }


@read
async def list_records(
    target: Target,
    base_id: BaseId,
    table: Table,
    filter_formula: Annotated[
        str | None, Field(description="An Airtable formula, e.g. {Status} = 'Open'.")
    ] = None,
    view: Annotated[str | None, Field(description="Only records in this view.")] = None,
    page_size: Annotated[int, Field(ge=1, le=100)] = 50,
    offset: Annotated[str | None, Field(description="offset from a previous page.")] = None,
) -> JSON:
    """List records in a table, one page at a time."""
    params = {
        "filterByFormula": filter_formula,
        "view": view,
        "pageSize": page_size,
        "offset": offset,
    }
    return await _call(target, "GET", _table_path(base_id, table), params=params)


@read
async def get_record(target: Target, base_id: BaseId, table: Table, record_id: RecordId) -> JSON:
    """Get one record with all its fields."""
    path = f"{_table_path(base_id, table)}/{_check(record_id, RECORD_PATTERN, 'record')}"
    return await _call(target, "GET", path)


@write
async def create_records(
    target: Target, base_id: BaseId, table: Table, records: NewRecords
) -> JSON:
    """Create up to 10 records; each item is the new record's fields by name."""
    body: JSON = {"records": [{"fields": fields} for fields in records], "typecast": False}
    return await _call(target, "POST", _table_path(base_id, table), json=body)


@write
async def update_records(
    target: Target,
    base_id: BaseId,
    table: Table,
    records: Annotated[
        list[dict[str, Any]],
        Field(
            min_length=1, max_length=10, description='Items like {"id": "rec...", "fields": {...}}.'
        ),
    ],
) -> JSON:
    """Change fields on up to 10 records. Fields not named are left as they are."""
    for record in records:
        _check(str(record.get("id", "")), RECORD_PATTERN, "record")
        if not isinstance(record.get("fields"), dict):
            raise ToolError("each record needs its fields as an object")
    body: JSON = {"records": [{"id": r["id"], "fields": r["fields"]} for r in records]}
    return await _call(target, "PATCH", _table_path(base_id, table), json=body)


@write
async def delete_records(
    target: Target,
    base_id: BaseId,
    table: Table,
    record_ids: Annotated[list[str], Field(min_length=1, max_length=10)],
) -> JSON:
    """Delete up to 10 records by id."""
    ids = [_check(r, RECORD_PATTERN, "record") for r in record_ids]
    params = {"records[]": ids}
    return await _call(target, "DELETE", _table_path(base_id, table), params=params)


ADAPTER = Adapter(
    service=InstanceType.AIRTABLE,
    tools=(
        list_bases,
        list_tables,
        list_records,
        get_record,
        create_records,
        update_records,
        delete_records,
    ),
)
