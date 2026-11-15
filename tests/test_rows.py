"""A big list is gone through with Ringkeeper's own tools, never a shell: a list tool gives only
the fields asked for and cuts long text, and a saved list is read with read_more's fields,
where and page."""

import json
from typing import Any

import pytest

from ringkeeper.adapters.base import ToolError
from ringkeeper.calls import Caller
from ringkeeper.services import continuations, rows

ME = Caller("p", "session-1", "t1")
NOTE = "Called about the boiler. " * 60  # longer than rows.TEXT_CHARS


def records(count: int, notes: str = NOTE) -> dict[str, Any]:
    """An Airtable page: values under `fields`, and a few names that aren't ASCII."""
    names = ["Zoë Ångström", "Łucja Wójcik", "José Núñez", "Ana Lima"]
    return {
        "records": [
            {
                "id": f"rec{i:014d}",
                "createdTime": "2026-11-01T09:00:00.000Z",
                "fields": {
                    "Name": names[i % 4],
                    "Notes": notes + ("Priority: urgent." if i % 3 == 0 else "Priority: routine."),
                    "Status": "Open",
                },
            }
            for i in range(count)
        ],
        "total": count,
    }


@pytest.fixture(autouse=True)
def nothing_kept() -> Any:
    continuations.clear()
    yield
    continuations.clear()


def test_a_list_tool_gives_only_the_fields_asked_for() -> None:
    shown = continuations.fit(ME, records(3, notes="short"), "records", ["name"])
    assert [r["fields"] for r in shown["records"]] == [
        {"Name": "Zoë Ångström"}, {"Name": "Łucja Wójcik"}, {"Name": "José Núñez"},
    ]  # fmt: skip
    assert shown["records"][0]["id"] == "rec00000000000000"
    assert "continuation" not in shown  # nothing was cut or saved


def test_long_text_is_cut_and_the_whole_value_is_one_call_away() -> None:
    shown = continuations.fit(ME, records(3), "records")
    notes = shown["records"][0]["fields"]["Notes"]
    assert len(notes) == rows.TEXT_CHARS + len(rows.CUT_MARK)
    assert notes.endswith(rows.CUT_MARK)
    assert "where" in shown["note"]
    one = continuations.read_more(
        ME, shown["continuation"], where={"field": "id", "equals": "rec00000000000001"}
    )
    assert one["records"] == [
        {"id": "rec00000000000001", **records(3)["records"][1]["fields"]}
    ]  # whole, and flat


def test_a_big_list_is_saved_and_says_exactly_how_to_go_on() -> None:
    shown = continuations.fit(ME, records(149), "records")
    assert shown["saved_rows"] == 149
    assert shown["note"].startswith('149 records saved. Use read_more with continuation and '
                                    'fields=["Name", "Notes", "Status"]')  # fmt: skip
    assert len(shown["records"]) == rows.PAGE_ROWS
    assert (shown["page"], shown["pages"], shown["next_page"]) == (1, 3, 2)
    assert shown["total"] == 149  # what came with the list stays
    assert len(json.dumps(shown, ensure_ascii=False)) < continuations.ROWS_FIT_CHARS


def test_a_saved_list_is_read_by_fields_where_and_page() -> None:
    ident = continuations.fit(ME, records(149), "records")["continuation"]
    names: list[str] = []
    page: int | None = 1
    while page is not None:
        part = continuations.read_more(ME, ident, fields=["Name"], page=page)
        names += [r["Name"] for r in part["records"]]
        assert all(set(r) == {"id", "Name"} for r in part["records"])
        page = part["next_page"]
    assert len(names) == 149
    assert names[:2] == ["Zoë Ångström", "Łucja Wójcik"]

    urgent = continuations.read_more(
        ME, ident, fields=["Name"], where={"field": "notes", "contains": "URGENT"}
    )
    assert urgent["total_rows"] == 50  # every third of 149
    routine = continuations.read_more(ME, ident, where={"field": "Notes", "contains": "routine"})
    assert routine["total_rows"] == 99
    assert routine["records"][0]["Notes"].endswith(rows.CUT_MARK)  # many: still cut
    ana = continuations.read_more(ME, ident, where={"field": "Name", "equals": "ana lima"})
    assert ana["total_rows"] == 37


def test_a_filter_or_field_it_cant_read_says_what_it_takes() -> None:
    ident = continuations.fit(ME, records(149), "records")["continuation"]
    with pytest.raises(ToolError, match="The fields are: id, Name, Notes, Status"):
        continuations.read_more(ME, ident, fields=["Phone"])
    with pytest.raises(ToolError, match="one of equals or contains"):
        continuations.read_more(ME, ident, where={"field": "Name"})
    with pytest.raises(ToolError, match="only 3 pages"):
        continuations.read_more(ME, ident, page=4)


def test_sql_rows_take_fields_by_column() -> None:
    result = {
        "columns": ["id", "name", "notes"],
        "rows": [[i, f"Zoë {i}", NOTE] for i in range(3)],
        "row_count": 3,
    }
    shown = continuations.fit(ME, result, "rows", ["name"])
    assert (shown["columns"], shown["rows"][0]) == (["id", "name"], [0, "Zoë 0"])


def test_any_tools_long_list_is_saved_as_rows() -> None:
    """A tool that doesn't say it lists still has its long list read in pages."""
    result = {"items": [{"n": i, "text": "word " * 200} for i in range(400)]}
    shown = continuations.fit(ME, result)
    assert shown["saved_rows"] == 400
    assert continuations.read_more(ME, shown["continuation"], page=8)["next_page"] is None


@pytest.mark.anyio
async def test_the_gateway_offers_fields_on_list_tools_and_filters_on_read_more() -> None:
    from typing import cast  # noqa: PLC0415

    from ringkeeper.adapters import ADAPTERS  # noqa: PLC0415
    from ringkeeper.gateway.app import build_server  # noqa: PLC0415
    from ringkeeper.models import InstanceType  # noqa: PLC0415
    from ringkeeper.vault import Vault  # noqa: PLC0415

    listed = {
        InstanceType.AIRTABLE: "list_records", InstanceType.SUPABASE: "query",
        InstanceType.N8N: "list_workflows", InstanceType.GMAIL: "search_messages",
    }  # fmt: skip
    for service, name in listed.items():
        server = build_server(ADAPTERS[service], cast(Vault, None))
        tools = {t.name: t for t in await server.list_tools()}
        assert "fields" in tools[name].parameters["properties"], name
        assert {"fields", "where", "page"} <= set(tools["read_more"].parameters["properties"])
    plain = build_server(ADAPTERS[InstanceType.AIRTABLE], cast(Vault, None))
    tools = {t.name: t for t in await plain.list_tools()}
    assert "fields" not in tools["get_record"].parameters["properties"]  # not a list
