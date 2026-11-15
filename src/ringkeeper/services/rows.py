"""Rows in a tool's answer: only the fields asked for, long text cut, and filters and pages over
a saved result, so an agent goes through a big table with Ringkeeper's own tools instead of a
shell (no jq, sed or awk on a Windows computer, and Python's own output there is cp1252).

A row is a dict (an n8n workflow, a message), an Airtable record (its values under `fields`)
or a list beside `columns` (a SQL query). Read here, each is one flat dict: its id and values.
"""

import json
from typing import Any, cast

from ringkeeper.adapters.base import JSON, ToolError

TEXT_CHARS = 500  # a longer text value in a list is cut here
PAGE_ROWS = 50
FULL_ROWS = 5  # as few as this, matched by `where`, come with their whole text
CUT_MARK = " […]"
ID_KEYS = ("id", "ID", "Id")
AUTO_ROWS = 20  # a list this long is read as rows even from a tool that doesn't say so


def key_of(result: JSON) -> str | None:
    """The answer's list of rows, when it has one: the longest list of dicts, or of lists
    with `columns` beside them."""
    best: tuple[int, str] | None = None
    for key, value in result.items():
        if not isinstance(value, list) or len(cast(list[Any], value)) < AUTO_ROWS:
            continue
        items = cast(list[Any], value)
        dicts = all(isinstance(i, dict) for i in items)
        lists = isinstance(result.get("columns"), list) and all(isinstance(i, list) for i in items)
        if (dicts or lists) and (best is None or len(items) > best[0]):
            best = (len(items), key)
    return best[1] if best else None


def _columns(result: JSON) -> list[str] | None:
    columns = result.get("columns")
    if not isinstance(columns, list):
        return None
    return [
        str(cast(JSON, c).get("name") if isinstance(c, dict) else c)
        for c in cast(list[Any], columns)
    ]


def _airtable(row: JSON) -> bool:
    return isinstance(row.get("fields"), dict) and set(row) <= {
        "id", "createdTime", "fields", "commentCount",
    }  # fmt: skip


def flat(result: JSON, key: str) -> list[JSON]:
    """Every row as one flat dict."""
    items = cast(list[Any], result.get(key) or [])
    columns = _columns(result)
    rows: list[JSON] = []
    for item in items:
        if isinstance(item, list) and columns is not None:
            rows.append(dict(zip(columns, cast(list[Any], item), strict=False)))
        elif isinstance(item, dict):
            row = cast(JSON, item)
            if _airtable(row):
                rows.append({"id": row.get("id"), **cast(JSON, row["fields"])})
            else:
                rows.append(row)
    return rows


def _named(names: list[str], fields: list[str]) -> list[str]:
    """The fields asked for, as the rows spell them (any case), ids first."""
    by_case = {n.casefold(): n for n in names}
    ids = [n for n in names if n in ID_KEYS]
    asked = [by_case[f.casefold()] for f in fields if f.casefold() in by_case]
    return ids + [n for n in dict.fromkeys(asked) if n not in ids]


def names_in(rows: list[JSON]) -> list[str]:
    return list(dict.fromkeys(name for row in rows for name in row))


def unknown(rows: list[JSON], fields: list[str]) -> list[str]:
    known = {n.casefold() for n in names_in(rows)}
    return [f for f in fields if f.casefold() not in known]


def project(result: JSON, key: str, fields: list[str]) -> JSON:
    """The answer with only these fields in each row (and its id), in the shape it came in."""
    items = cast(list[Any], result.get(key) or [])
    columns = _columns(result)
    if columns is not None and items and all(isinstance(i, list) for i in items):
        keep = _named(columns, fields)
        at = [columns.index(c) for c in keep]
        raw = cast(list[Any], result["columns"])
        return result | {
            "columns": [raw[i] for i in at],
            key: [[cast(list[Any], row)[i] for i in at] for row in items],
        }
    shaped: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            shaped.append(item)
            continue
        row = cast(JSON, item)
        if _airtable(row):
            values = cast(JSON, row["fields"])
            keep = _named(list(values), fields)
            shaped.append(row | {"fields": {n: values[n] for n in keep}})
        else:
            keep = _named(list(row), fields)
            shaped.append({n: row[n] for n in keep})
    return result | {key: shaped}


def _cut_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str) and len(value) > TEXT_CHARS:
        return value[:TEXT_CHARS] + CUT_MARK, True
    return value, False


def _cut_dict(row: JSON) -> tuple[JSON, bool]:
    cut = False
    out: JSON = {}
    for name, value in row.items():
        out[name], was = _cut_value(value)
        cut = cut or was
    return out, cut


def cut(result: JSON, key: str) -> tuple[JSON, bool]:
    """The answer with every long text value in its rows cut to TEXT_CHARS, and whether any was."""
    any_cut = False
    shaped: list[Any] = []
    for item in cast(list[Any], result.get(key) or []):
        if isinstance(item, list):
            values = [_cut_value(v) for v in cast(list[Any], item)]
            shaped.append([v for v, _ in values])
            any_cut = any_cut or any(was for _, was in values)
        elif isinstance(item, dict):
            row = cast(JSON, item)
            if _airtable(row):
                values, was = _cut_dict(cast(JSON, row["fields"]))
                shaped.append(row | {"fields": values})
            else:
                shaped_row, was = _cut_dict(row)
                shaped.append(shaped_row)
            any_cut = any_cut or was
        else:
            shaped.append(item)
    return result | {key: shaped}, any_cut


def cut_rows(rows: list[JSON]) -> tuple[list[JSON], bool]:
    done = [_cut_dict(row) for row in rows]
    return [row for row, _ in done], any(was for _, was in done)


def _text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [t for v in cast(list[Any], value) for t in _text(v)]
    if isinstance(value, dict):
        return [json.dumps(value, ensure_ascii=False, default=str).casefold()]
    return [] if value is None else [str(value).strip().casefold()]


def matching(rows: list[JSON], where: dict[str, Any]) -> list[JSON]:
    """The rows whose field equals or contains a value (any case). Refuses a filter it can't
    read, saying what it takes."""
    field = where.get("field")
    equals, contains = where.get("equals"), where.get("contains")
    if not isinstance(field, str) or (equals is None) == (contains is None):
        raise ToolError(
            'where takes a field and one of equals or contains: {"field": "Status", "equals": '
            '"Open"} or {"field": "Notes", "contains": "urgent"}.'
        )
    check_fields(rows, [field])
    name = _named(names_in(rows), [field])[-1]
    wanted = str(equals if equals is not None else contains).strip().casefold()
    if equals is not None:
        return [r for r in rows if wanted in _text(r.get(name))]
    return [r for r in rows if any(wanted in t for t in _text(r.get(name)))]


def check_fields(rows: list[JSON], fields: list[str]) -> None:
    """Refuses a field no row has, naming the ones there are."""
    if missing := unknown(rows, fields):
        named = ", ".join(names_in(rows))
        raise ToolError(f"no field named {missing[0]!r}. The fields are: {named}.")


def fields_note(rows: list[JSON]) -> str:
    return json.dumps([n for n in names_in(rows) if n not in ID_KEYS], ensure_ascii=False)


CUT_NOTE = (
    f"Text longer than {TEXT_CHARS} characters is cut, ending in{CUT_MARK}. read_more with this "
    'continuation searches the whole values: where={"field": ..., "contains": ...} gives the '
    "rows that match, and total_rows counts them. One whole row: where="
    '{"field": "id", "equals": "<its id>"}.'
)
