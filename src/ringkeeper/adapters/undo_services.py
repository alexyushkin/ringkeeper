"""Undo for Airtable records and n8n workflows (see adapters.base.Undoer).

Airtable: an update keeps the old values of the fields it sets; a delete keeps
the whole records (they come back as new records, with new IDs: Airtable has no
undelete); a create keeps the new records' IDs, and reverting deletes them.

n8n: replacing a workflow keeps the workflow as it was (name, nodes,
connections, settings); turning one on or off keeps whether it was on.
"""

from typing import Any, cast

from ringkeeper.adapters.base import JSON, Target, Undoer

# Field types Airtable fills in itself: never written back.
COMPUTED = {
    "formula", "rollup", "count", "lookup", "multipleLookupValues", "createdTime",
    "lastModifiedTime", "autoNumber", "button", "createdBy", "lastModifiedBy",
    "externalSyncSource", "aiText",
}  # fmt: skip


def _records(args: JSON) -> list[JSON]:
    return cast(list[JSON], args.get("records") or [])


class AirtableUpdateUndo(Undoer):
    async def before(self, target: Target, args: JSON) -> JSON:
        from ringkeeper.adapters import airtable  # noqa: PLC0415 (airtable imports this module)

        base, table = str(args["base_id"]), str(args["table"])
        kept: list[JSON] = []
        for record in _records(args):
            now = await airtable.get_record.handler(target, base, table, str(record["id"]))
            fields = cast(JSON, now.get("fields") or {})
            names = list(cast(JSON, record.get("fields") or {}))
            kept.append({"id": record["id"], "fields": {n: fields.get(n) for n in names}})
        return {"base_id": base, "table": table, "records": kept}

    async def current(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import airtable  # noqa: PLC0415

        now: list[JSON] = []
        for record in _records(kept):
            found = await airtable.get_record.handler(
                target, str(kept["base_id"]), str(kept["table"]), str(record["id"])
            )
            fields = cast(JSON, found.get("fields") or {})
            names = list(cast(JSON, record["fields"]))
            now.append({"id": record["id"], "fields": {n: fields.get(n) for n in names}})
        return {"records": now}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import airtable  # noqa: PLC0415

        return await airtable.update_records.handler(
            target, str(kept["base_id"]), str(kept["table"]), _records(kept)
        )

    def describe(self, kept: JSON) -> str:
        records = _records(kept)
        names = sorted({n for r in records for n in cast(JSON, r["fields"])})
        n = f"{len(records)} record" + ("" if len(records) == 1 else "s")
        return f"Puts back the old {', '.join(names)} on {n} in {kept['table']}."


class AirtableDeleteUndo(Undoer):
    async def before(self, target: Target, args: JSON) -> JSON:
        from ringkeeper.adapters import airtable  # noqa: PLC0415

        base, table = str(args["base_id"]), str(args["table"])
        computed = await _computed_fields(target, base, table)
        kept: list[JSON] = []
        for ident in cast(list[str], args.get("record_ids") or []):
            found = await airtable.get_record.handler(target, base, table, ident)
            fields = cast(JSON, found.get("fields") or {})
            kept.append(
                {"id": ident, "fields": {k: v for k, v in fields.items() if k not in computed}}
            )
        return {"base_id": base, "table": table, "records": kept}

    async def after(self, target: Target, args: JSON, before: JSON, result: JSON) -> JSON:
        return {"records": []}  # gone

    async def current(self, target: Target, kept: JSON) -> JSON:
        return {"records": []}  # nothing to overwrite: they come back as new records

    async def restore(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import airtable  # noqa: PLC0415

        fields = [cast(JSON, r["fields"]) for r in _records(kept)]
        made: list[Any] = []
        for start in range(0, len(fields), 10):
            result = await airtable.create_records.handler(
                target, str(kept["base_id"]), str(kept["table"]), fields[start : start + 10]
            )
            made += cast(list[Any], result.get("records") or [])
        return {"records": made}

    def describe(self, kept: JSON) -> str:
        n = len(_records(kept))
        return (
            f"Adds back the {n} deleted record{'' if n == 1 else 's'} in {kept['table']}, "
            "as new records (Airtable gives them new IDs, and links to them from elsewhere "
            "aren't restored)."
        )


class AirtableCreateUndo(Undoer):
    async def before(self, target: Target, args: JSON) -> JSON:
        return {"base_id": str(args["base_id"]), "table": str(args["table"]), "records": []}

    async def after(self, target: Target, args: JSON, before: JSON, result: JSON) -> JSON:
        made = cast(list[JSON], result.get("records") or [])
        before["records"] = [{"id": r.get("id")} for r in made if r.get("id")]
        return {"records": before["records"]}

    async def current(self, target: Target, kept: JSON) -> JSON:
        return {"records": [{"id": r["id"]} for r in _records(kept)]}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import airtable  # noqa: PLC0415

        ids = [str(r["id"]) for r in _records(kept)]
        for start in range(0, len(ids), 10):
            await airtable.delete_records.handler(
                target, str(kept["base_id"]), str(kept["table"]), ids[start : start + 10]
            )
        return {"deleted": len(ids)}

    def describe(self, kept: JSON) -> str:
        n = len(_records(kept))
        return f"Deletes the {n} record{'' if n == 1 else 's'} it added to {kept['table']}."


async def _computed_fields(target: Target, base: str, table: str) -> set[str]:
    from ringkeeper.adapters import airtable  # noqa: PLC0415

    schema = await airtable.list_tables.handler(target, base)
    for found in cast(list[JSON], schema.get("tables") or []):
        if table in (found.get("id"), found.get("name")):
            return {
                str(f.get("name")) for f in cast(list[JSON], found.get("fields") or [])
                if f.get("type") in COMPUTED
            }  # fmt: skip
    return set()


class N8nWorkflowUndo(Undoer):
    async def before(self, target: Target, args: JSON) -> JSON:
        from ringkeeper.adapters import n8n  # noqa: PLC0415

        workflow = await n8n.get_workflow.handler(target, str(args["workflow_id"]))
        keep = {k: workflow.get(k) for k in ("name", "nodes", "connections", "settings")}
        return {
            "workflow_id": str(args["workflow_id"]), "workflow": keep,
            "republish": bool(args.get("republish")), "updatedAt": workflow.get("updatedAt"),
        }  # fmt: skip

    async def current(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import n8n  # noqa: PLC0415

        workflow = await n8n.get_workflow.handler(target, str(kept["workflow_id"]))
        return {"updatedAt": workflow.get("updatedAt")}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import n8n  # noqa: PLC0415

        old = cast(JSON, kept["workflow"])
        return await n8n.update_workflow.handler(
            target, str(kept["workflow_id"]), str(old["name"]), cast(list[JSON], old["nodes"]),
            cast(JSON, old["connections"]), cast(JSON | None, old.get("settings")),
            republish=bool(kept.get("republish")),
        )  # fmt: skip

    def describe(self, kept: JSON) -> str:
        old = cast(JSON, kept["workflow"])
        live = " and puts it live again" if kept.get("republish") else ""
        return f"Puts back the workflow “{old.get('name')}” as it was before{live}."


class N8nActiveUndo(Undoer):
    """activate_workflow / deactivate_workflow: back to on or off."""

    async def before(self, target: Target, args: JSON) -> JSON:
        from ringkeeper.adapters import n8n  # noqa: PLC0415

        workflow = await n8n.get_workflow.handler(target, str(args["workflow_id"]))
        return {
            "workflow_id": str(args["workflow_id"]), "name": workflow.get("name"),
            "active": bool(workflow.get("active")),
        }  # fmt: skip

    async def current(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import n8n  # noqa: PLC0415

        workflow = await n8n.get_workflow.handler(target, str(kept["workflow_id"]))
        return {"active": bool(workflow.get("active"))}

    async def restore(self, target: Target, kept: JSON) -> JSON:
        from ringkeeper.adapters import n8n  # noqa: PLC0415

        tool = n8n.activate_workflow if kept["active"] else n8n.deactivate_workflow
        return await tool.handler(target, str(kept["workflow_id"]))

    def describe(self, kept: JSON) -> str:
        state = "on" if kept["active"] else "off"
        return f"Turns the workflow “{kept.get('name')}” {state} again, as it was."
