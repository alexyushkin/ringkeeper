"""Settings > Notifications: Telegram's prefs, and Slack and Discord channels
(ringkeeper.channels). Tokens go straight to the vault and never come back out.

`opener` is the desktop app's Open link (channels.links): no sign-in, the
signature is the check. It brings the app window up at a page, or, with no
window to take it, signs this browser in there (desktop.opener).
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ringkeeper import channels
from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import RecentAuth, SessionDep, WriteSessionDep, require_owner
from ringkeeper.api.supervisor import Supervisor
from ringkeeper.channels import links
from ringkeeper.channels.outbox import ChannelError
from ringkeeper.channels.slack import MANIFEST
from ringkeeper.config import get_settings
from ringkeeper.desktop import opener as desktop_opener
from ringkeeper.models import Channel
from ringkeeper.schemas import Input
from ringkeeper.services.runtime import SETTINGS_CHANNEL
from ringkeeper.storage import events

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
opener = APIRouter()

Kind = Literal["slack", "discord"]
Event = Literal["approvals", "finished", "routines", "failures"]
Token = Annotated[str, Field(min_length=10, max_length=500)]
Ident = Annotated[str, Field(min_length=1, max_length=64)]


class Quiet(Input):
    from_: Annotated[str, Field(alias="from", pattern=r"^\d{1,2}:\d{2}$")]
    to: Annotated[str, Field(pattern=r"^\d{1,2}:\d{2}$")]
    tz: Annotated[str, Field(min_length=1, max_length=64)] = "UTC"


class PrefsIn(Input):
    events: list[Event] = Field(default_factory=list[Event])
    quiet: Quiet | None = None
    clients: list[Annotated[str, Field(max_length=63)]] = Field(default_factory=list[str])
    projects: list[Annotated[str, Field(max_length=63)]] = Field(default_factory=list[str])

    def doc(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class TokensIn(Input):
    kind: Kind
    bot_token: Token
    app_token: Token | None = None  # Slack's app-level token (xapp-)

    def tokens(self) -> dict[str, str]:
        return {"bot_token": self.bot_token, "app_token": self.app_token or ""}


class ChannelIn(TokensIn):
    name: Annotated[str, Field(max_length=80)] = ""
    target: Ident
    approver: Ident
    prefs: PrefsIn | None = None


class ChannelPatch(Input):
    name: Annotated[str, Field(max_length=80)] | None = None
    enabled: bool | None = None
    target: Ident | None = None
    approver: Ident | None = None
    prefs: PrefsIn | None = None


class ChannelOut(BaseModel):
    id: str
    kind: str
    name: str
    enabled: bool
    target: str
    approver: str
    about: dict[str, str]  # the workspace or bot, as Slack or Discord named it
    prefs: dict[str, Any]
    running: bool
    error: str | None


def _status(request: Request) -> dict[str, dict[str, object]]:
    supervisor: Supervisor | None = getattr(request.app.state, "supervisor", None)
    return supervisor.status() if supervisor is not None else {}


def _wake(request: Request) -> None:
    supervisor: Supervisor | None = getattr(request.app.state, "supervisor", None)
    if supervisor is not None:
        supervisor.wake()


def _out(channel: Channel, status: dict[str, dict[str, object]]) -> ChannelOut:
    state = status.get(f"{channel.kind}:{channel.id}", {})
    config = dict(channel.config)
    about = {k: str(config[k]) for k in ("team", "bot", "app_id", "invite") if config.get(k)}
    error = state.get("error")
    return ChannelOut(
        id=str(channel.id), kind=channel.kind, name=channel.name, enabled=channel.enabled,
        target=str(config.get("target") or ""), approver=str(config.get("approver") or ""),
        about=about, prefs=dict(channel.prefs), running=bool(state.get("running")),
        error=str(error) if error else None,
    )  # fmt: skip


@contextmanager
def _remote() -> Generator[None]:
    """Slack or Discord said no, or couldn't be reached: a 502 that says so."""
    try:
        yield
    except ChannelError as error:
        raise HTTPException(502, detail=str(error)) from None


@router.get("/channels")
def list_channels(request: Request, session: SessionDep) -> dict[str, Any]:
    status = _status(request)
    prefs, _since = channels.telegram_state(session)
    return {
        "telegram": {"prefs": prefs.dump()},
        "channels": [_out(c, status) for c in channels.list_channels(session)],
    }


@router.get("/channels/slack/manifest")
def slack_manifest() -> dict[str, Any]:
    """The Slack app to create (From an app manifest): Socket Mode, buttons, and
    only the scopes Ringkeeper uses."""
    return MANIFEST


@router.put("/channels/telegram/prefs")
def put_telegram_prefs(data: PrefsIn, session: WriteSessionDep) -> dict[str, Any]:
    prefs = channels.set_telegram_prefs(session, data.doc())
    return {"prefs": prefs.dump()}


def _with(data: TokensIn, use: Callable[[channels.SlackApi | channels.DiscordApi], Any]) -> Any:
    api = channels.connect(data.kind, get_settings(), data.tokens())
    try:
        with _remote():
            return use(api)
    finally:
        api.close()


@router.post("/channels/check", dependencies=[RecentAuth])
def check_tokens(data: TokensIn) -> dict[str, Any]:
    """The tokens work, and where this app or bot can post. Nothing is stored."""
    return _with(data, lambda api: {"about": api.check(), "targets": api.channels()})


@router.post("/channels", dependencies=[RecentAuth])
def add_channel(data: ChannelIn, request: Request, session: WriteSessionDep) -> ChannelOut:
    with _remote():
        channel = channels.create_channel(
            session, get_settings(), data.kind, name=data.name, tokens=data.tokens(),
            target=data.target, approver=data.approver,
            prefs=data.prefs.doc() if data.prefs else None,
        )  # fmt: skip
    events.notify(session, SETTINGS_CHANNEL)
    _wake(request)
    return _out(channel, {})


@router.put("/channels/{ident}")
def change_channel(
    ident: str, data: ChannelPatch, request: Request, session: WriteSessionDep
) -> ChannelOut:
    channel = channels.get_channel(session, ident)
    channels.update_channel(
        session, channel, name=data.name, enabled=data.enabled, target=data.target,
        approver=data.approver, prefs=data.prefs.doc() if data.prefs else None,
    )  # fmt: skip
    session.flush()
    events.notify(session, SETTINGS_CHANNEL)
    _wake(request)
    return _out(channel, _status(request))


@router.delete("/channels/{ident}", status_code=204)
def remove_channel(ident: str, request: Request, session: WriteSessionDep) -> None:
    channels.delete_channel(session, get_settings(), channels.get_channel(session, ident))
    events.notify(session, SETTINGS_CHANNEL)
    _wake(request)


@router.post("/channels/{ident}/test")
def test_channel(ident: str, session: SessionDep) -> dict[str, str]:
    """Posts a test message; its button checks that your answers are accepted there."""
    channel = channels.get_channel(session, ident)
    with _remote():
        channels.send_test(get_settings(), channel)
    return {"sent": channel.name}


@router.get("/channels/{ident}/targets")
def channel_targets(ident: str, session: SessionDep) -> dict[str, Any]:
    channel = channels.get_channel(session, ident)
    with _remote():
        return {"targets": channels.targets(get_settings(), channel)}


# --- The desktop app's Open link ---

OPENING = """<!doctype html><meta charset="utf-8"><title>Ringkeeper</title>
<body style="font:16px system-ui;margin:3rem;color:#333">
<p>{text}</p></body>"""


@opener.get("/open", response_class=HTMLResponse, include_in_schema=False)
def open_page(
    session: SessionDep,
    to: Annotated[str, Query(max_length=300)],
    sig: Annotated[str, Query(max_length=64)],
) -> Response:
    settings = get_settings()
    if settings.mode != "desktop" or not to.startswith("/") or to.startswith("//"):
        raise HTTPException(404)
    if not links.signed(session, to, sig):
        return HTMLResponse(OPENING.format(text="This link isn't from this Ringkeeper."), 403)
    if not desktop_opener.window_can_open() and (there := desktop_opener.sign_in(to)):
        return RedirectResponse(there, status_code=303)  # this browser, signed in at the page
    desktop_opener.ask(to)
    text = "Opening Ringkeeper on this computer. You can close this tab."
    return HTMLResponse(OPENING.format(text=text))
