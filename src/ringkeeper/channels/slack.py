"""Slack: your own Slack app, in Socket Mode, so nothing needs a public address.

Two tokens, both in the vault: the bot token (xoxb-, to post and update
messages) and the app-level token (xapp-, with connections:write, to open the
Socket Mode connection that brings button presses, and DMs to the app when the
channel takes requests). Settings > Notifications > Slack walks through making the
app from MANIFEST. An app made before DMs (no im:history, no message.im event) gets
button presses only, until its manifest is pasted again and it's reinstalled.
"""

import json
import logging
import threading
from typing import Any, cast

import httpx2
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from ringkeeper import scrub
from ringkeeper.channels import plain
from ringkeeper.channels.cards import Button, Card
from ringkeeper.channels.outbox import ChannelError, OnMessage, OnPress

logger = logging.getLogger(__name__)

# Tests swap in httpx2.MockTransport.
transport: httpx2.BaseTransport | None = None

SCOPES = [
    "chat:write", "chat:write.public", "im:write", "im:history", "channels:read", "groups:read",
    "files:write",
]  # fmt: skip
MANIFEST: dict[str, Any] = {
    "display_information": {
        "name": "Ringkeeper",
        "description": "Approvals and results from your Ringkeeper.",
    },
    "features": {
        "bot_user": {"display_name": "Ringkeeper", "always_online": False},
        # DMs to the app: new requests, when the channel takes them
        "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False},
    },
    "oauth_config": {"scopes": {"bot": SCOPES}},
    "settings": {
        "event_subscriptions": {"bot_events": ["message.im"]},
        "interactivity": {"is_enabled": True},
        "socket_mode_enabled": True,
        "org_deploy_enabled": False,
        "token_rotation_enabled": False,
    },
}
TEXT_LIMIT = 2900  # a section's text is at most 3000 characters


SAID = {
    "invalid_auth": "Slack didn't accept the bot token. Copy it again from OAuth & Permissions.",
    "not_authed": "Slack didn't get a token. Paste the bot token again.",
    "token_revoked": "That Slack token was revoked. Make a new one and paste it again.",
    "channel_not_found": "Slack can't find that channel. Invite the bot to it, then try again.",
    "not_in_channel": "The bot isn't in that channel. Invite it there, then try again.",
    "missing_scope": (
        "The Slack app is missing a permission. Paste the manifest from Settings again and "
        "reinstall the app."
    ),
}


def said(code: str) -> str:
    """Slack's error code, as what to do."""
    return SAID.get(code, f"Slack said: {code}. Check the app's tokens, then try again.")


class SlackApi:
    def __init__(self, bot_token: str, app_token: str, base_url: str) -> None:
        scrub.register(bot_token)
        scrub.register(app_token)
        self._bot, self._app = bot_token, app_token
        self._http = httpx2.Client(
            base_url=f"{base_url.rstrip('/')}/", timeout=30.0, transport=transport
        )
        self._target: str | None = None
        self.target = ""  # a channel id (C..., G...) or a user id (U...: their DM)

    def close(self) -> None:
        self._http.close()

    def call(self, method: str, token: str | None = None, **body: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token or self._bot}"}
        try:
            response = self._http.post(method, json=body, headers=headers)
            data = cast(dict[str, Any], response.json())
        except (httpx2.HTTPError, ValueError):
            raise ChannelError(
                "Couldn't reach Slack. Check this computer is online, then try again."
            ) from None
        if not data.get("ok"):
            raise ChannelError(said(str(data.get("error") or response.status_code)))
        return data

    # --- Setup ---

    def check(self) -> dict[str, str]:
        """Both tokens work: the workspace and the bot's name."""
        me = self.call("auth.test")
        self.call("apps.connections.open", self._app)
        return {"team": str(me.get("team") or ""), "bot": str(me.get("user") or "")}

    def channels(self) -> list[dict[str, str]]:
        """Channels to pick from: public ones, and private ones the bot was added to."""
        found: list[dict[str, str]] = []
        cursor = ""
        for _page in range(10):
            data = self.call(
                "conversations.list", types="public_channel,private_channel",
                exclude_archived=True, limit=200, cursor=cursor or None,
            )  # fmt: skip
            found += [
                {"id": str(c["id"]), "name": f"#{c['name']}"}
                for c in cast(list[dict[str, Any]], data.get("channels") or [])
            ]
            meta = cast(dict[str, Any], data.get("response_metadata") or {})
            cursor = str(meta.get("next_cursor") or "")
            if not cursor:
                break
        return sorted(found, key=lambda c: c["name"])

    # --- Messages ---

    def _channel(self) -> str:
        """Where messages go: the channel, or the DM with that user (opened once)."""
        if self._target is None:
            if self.target.startswith(("U", "W")):
                opened = self.call("conversations.open", users=self.target)
                self._target = str(opened["channel"]["id"])
            else:
                self._target = self.target
        return self._target

    def post(self, card: Card) -> str:
        channel = self._channel()
        sent = self.call("chat.postMessage", channel=channel, **blocks(card), unfurl_links=False)
        return f"{sent['channel']}/{sent['ts']}"

    def update(self, ref: str, card: Card) -> None:
        channel, ts = ref.split("/", 1)
        self.call("chat.update", channel=channel, ts=ts, **blocks(card))

    def direct(self, user: str, card: Card) -> str:
        """A message in the DM with this person."""
        opened = self.call("conversations.open", users=user)
        channel = str(opened["channel"]["id"])
        sent = self.call("chat.postMessage", channel=channel, **blocks(card), unfurl_links=False)
        return f"{sent['channel']}/{sent['ts']}"

    def _form(self, method: str, **fields: str) -> dict[str, Any]:
        """A method that takes form fields (file uploads)."""
        try:
            response = self._http.post(
                method, data=fields, headers={"Authorization": f"Bearer {self._bot}"}
            )
            data = cast(dict[str, Any], response.json())
        except (httpx2.HTTPError, ValueError):
            raise ChannelError(
                "Couldn't reach Slack. Check this computer is online, then try again."
            ) from None
        if not data.get("ok"):
            raise ChannelError(said(str(data.get("error") or response.status_code)))
        return data

    def attach(self, user: str, files: list[tuple[str, bytes]]) -> None:
        """Files in the DM with this person: each uploaded, then shared there together. An
        app made before files:write says so (paste the manifest again and reinstall)."""
        opened = self.call("conversations.open", users=user)
        channel = str(opened["channel"]["id"])
        done: list[dict[str, str]] = []
        for name, data in files:
            got = self._form("files.getUploadURLExternal", filename=name, length=str(len(data)))
            try:
                uploaded = self._http.post(str(got["upload_url"]), content=data)
            except httpx2.HTTPError:
                raise ChannelError("Couldn't upload a file to Slack. Try again later.") from None
            if uploaded.status_code >= 300:
                raise ChannelError("Slack didn't take a file. Try again later.")
            done.append({"id": str(got["file_id"]), "title": name})
        self._form("files.completeUploadExternal", files=json.dumps(done), channel_id=channel)

    # --- Button presses and DMs, over Socket Mode ---

    def listen(
        self, stop: threading.Event, on_press: OnPress, on_message: OnMessage | None = None
    ) -> None:
        url = str(self.call("apps.connections.open", self._app)["url"])
        with connect(url, open_timeout=15, proxy=None) as socket:
            while not stop.is_set():
                try:
                    raw = socket.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    return
                envelope = cast(dict[str, Any], json.loads(raw))
                if ident := envelope.get("envelope_id"):
                    socket.send(json.dumps({"envelope_id": ident}))  # acknowledged at once
                kind = envelope.get("type")
                if kind == "disconnect":
                    return  # Slack moves us to a fresh connection
                if kind == "interactive":
                    self._interaction(cast(dict[str, Any], envelope.get("payload") or {}), on_press)
                elif kind == "events_api" and on_message is not None:
                    payload = cast(dict[str, Any], envelope.get("payload") or {})
                    self._dm(cast(dict[str, Any], payload.get("event") or {}), on_message)

    def _dm(self, event: dict[str, Any], on_message: OnMessage) -> None:
        """A person's DM to the app (never the app's own messages, edits or joins)."""
        if event.get("type") != "message" or event.get("channel_type") != "im":
            return
        if event.get("subtype") or event.get("bot_id") or not event.get("user"):
            return
        user = str(event["user"])
        said = plain.from_slack(str(event.get("text") or ""))  # as written, without the markup
        reply = on_message(user, said, f"slack:{user}")
        if reply and event.get("channel"):
            try:
                self.call(
                    "chat.postMessage", channel=str(event["channel"]), text=scrub.scrub(reply)
                )
            except ChannelError as error:
                logger.warning("could not answer a Slack DM: %s", error)

    def _interaction(self, payload: dict[str, Any], on_press: OnPress) -> None:
        if payload.get("type") != "block_actions":
            return
        user = cast(dict[str, Any], payload.get("user") or {})
        user_id = str(user.get("id") or "")
        who = f"slack:{user_id}" + (f" ({user['username']})" if user.get("username") else "")
        for action in cast(list[dict[str, Any]], payload.get("actions") or []):
            value = str(action.get("value") or "")
            if not value:
                continue  # Open: a link, nothing to do here
            reply = on_press(value, user_id, who)
            if url := payload.get("response_url"):
                self._reply(str(url), reply)

    def _reply(self, url: str, text: str) -> None:
        """Only the person who pressed sees it."""
        body = {"response_type": "ephemeral", "replace_original": False, "text": scrub.scrub(text)}
        try:
            self._http.post(url, json=body)
        except httpx2.HTTPError as error:
            logger.warning("could not answer a Slack button: %s", type(error).__name__)


def _button(index: int, button: Button) -> dict[str, Any]:
    made: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": button.label[:75]},
        "action_id": f"rk-{index}",
    }
    if button.url:
        made["url"] = button.url
    if button.action:
        made["value"] = button.action
    if button.style in ("primary", "danger"):
        made["style"] = button.style
    return made


def blocks(card: Card) -> dict[str, Any]:
    """Plain text (no Slack markup from what an agent wrote), then the buttons."""
    text = card.text if len(card.text) <= TEXT_LIMIT else card.text[: TEXT_LIMIT - 1] + "…"
    made: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "plain_text", "text": text, "emoji": False}}
    ]
    if card.buttons:
        made.append(
            {
                "type": "actions",
                "elements": [_button(i, b) for i, b in enumerate(card.buttons[:25])],
            }
        )
    return {"text": text, "blocks": made}
