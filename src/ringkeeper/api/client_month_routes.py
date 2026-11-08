"""A client's month (services.client_month): what was done and what it cost, the report as
Markdown, PDF or CSV, the monthly cap, and the report emailed to the client, held for approval."""

from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy.orm import Session

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import SessionDep, WriteSessionDep, require_owner
from ringkeeper.calls import ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.schemas import Input
from ringkeeper.services import admin_audit, calls, client_month, clients, pdf_text
from ringkeeper.services.calls import RefusalError
from ringkeeper.services.runtime import effective
from ringkeeper.vault import get_vault

router = APIRouter(prefix="/clients", dependencies=[AdminDep, Depends(require_owner)])
OWNER = "owner"
MonthQuery = Annotated[str | None, Query(max_length=7, pattern=r"^\d{4}-\d{2}$")]


def _month(session: Session, slug: str, month: str | None) -> client_month.Month:
    year, number = client_month.parse(month)
    mode = effective(session, get_settings()).claude_auth_mode
    return client_month.month_of(session, slug, year, number, mode)


@router.get("/{slug}/month")
def get_month(slug: str, session: SessionDep, month: MonthQuery = None) -> dict[str, Any]:
    found = _month(session, slug, month)
    return {
        "client": found.client, "name": found.name, "month": f"{found.year}-{found.month:02d}",
        "label": found.label, "projects": len(found.projects),
        "done": found.done, "failed": found.failed, "stopped": found.stopped, "open": found.open,
        "changes": found.changes, "held": found.held, "approved": found.approved,
        "denied": found.denied, "expired": found.expired, "waiting": found.waiting,
        "refused": found.refused, "cost_usd": round(found.cost_usd, 4),
        "estimated": found.estimated, "cap_usd": found.cap_usd,
        "summary": client_month.summary_lines(found, cost=True),
        "tasks": [
            {"when": t.when.isoformat(), "project": t.project, "title": t.title,
             "status": t.status, "result": t.result, "cost_usd": t.cost_usd}
            for t in found.tasks
        ],
        "mail": [
            {"connection": r.connection, "title": r.title, "project": r.project}
            for r in client_month.mail_routes(session, found.client)
        ],
    }  # fmt: skip


def _download(body: bytes, name: str, media: str) -> Response:
    return Response(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/{slug}/month.md")
def month_markdown(
    slug: str, session: SessionDep, month: MonthQuery = None, cost: bool = False
) -> Response:
    found = _month(session, slug, month)
    text = client_month.markdown(found, cost=cost)
    return _download(text.encode("utf-8"), client_month.file_name(found, "md"), "text/markdown")


@router.get("/{slug}/month.pdf")
def month_pdf(
    slug: str, session: SessionDep, month: MonthQuery = None, cost: bool = False
) -> Response:
    found = _month(session, slug, month)
    title = f"{found.name}: {found.label}"
    pdf = pdf_text.render(client_month.markdown(found, cost=cost), title)
    return _download(pdf, client_month.file_name(found, "pdf"), "application/pdf")


@router.get("/{slug}/month.csv")
def month_csv(slug: str, session: SessionDep, month: MonthQuery = None) -> Response:
    found = _month(session, slug, month)
    text = client_month.as_csv(found)
    return _download(text.encode("utf-8-sig"), client_month.file_name(found, "csv"), "text/csv")


class CapIn(Input):
    cap_usd: Annotated[float | None, Field(gt=0, le=1_000_000)] = None


@router.put("/{slug}/cap")
def set_cap(slug: str, data: CapIn, session: WriteSessionDep) -> dict[str, Any]:
    """New tasks for the client stop once the month's AI cost reaches it. None: no cap."""
    client = clients.get(session, slug)
    cap = round(data.cap_usd, 2) if data.cap_usd is not None else None
    client.monthly_cap_usd = cap
    said = (
        f"set {client.name}'s monthly cap to {client_month.money(cap)}"
        if cap is not None
        else f"removed {client.name}'s monthly cap"
    )
    admin_audit.record(session, "client.cap", said, by=OWNER)
    return {"cap_usd": client.monthly_cap_usd}


class EmailIn(Input):
    month: Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]
    to: Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
    connection: Annotated[str, Field(min_length=1, max_length=63)]
    cost: bool = False


@router.post("/{slug}/month/email")
async def email_month(slug: str, data: EmailIn) -> dict[str, Any]:
    """The report as an email to the client, from one of their mail connections. Like every
    send, it waits in Approvals: nothing goes out until you approve it there."""

    def prepare() -> ToolCall:
        with session_scope(commit=False) as session:
            found = _month(session, slug, data.month)
            return client_month.email_call(session, found, data.to, data.connection, cost=data.cost)

    try:
        call = await anyio.to_thread.run_sync(prepare)
        result = await calls.run_call(call, get_vault())
    except RuleViolationError as error:
        raise HTTPException(422, detail=str(error)) from None
    except RefusalError as refusal:
        raise HTTPException(422, detail=f"Ringkeeper refused it: {refusal}") from None
    if result.get("status") != "held_for_approval":
        raise HTTPException(500, detail="It wasn't held for approval, so it wasn't sent.")
    return {"decision_id": result["decision_id"]}
