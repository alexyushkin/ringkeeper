"""Discord: your own bot, on the gateway, so nothing needs a public address.

One token in the vault: the bot's. It posts and updates messages over the REST
API and takes button presses from the gateway (INTERACTION_CREATE, which needs no
privileged intents), and DMs to the bot (DIRECT_MESSAGES, not privileged either; a
DM's text comes without the Message Content intent). Invited with PERMISSIONS only:
see the channel, send messages.
"""

import json
import logging
import platform
import threading
from typing import Any, cast

import httpx2
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection, connect

from ringkeeper import scrub
from ringkeeper.channels import plain
from ringkeeper.channels.cards import Button, Card
from ringkeeper.channels.outbox import ChannelError, OnMessage, OnPress

logger = logging.getLogger(__name__)

# Tests swap in httpx2.MockTransport.
transport: httpx2.BaseTransport | None = None

VIEW_CHANNEL, SEND_MESSAGES = 1 << 10, 1 << 11
PERMISSIONS = VIEW_CHANNEL | SEND_MESSAGES
TEXT_LIMIT = 1900  # a message is at most 2000 characters
TEXT_CHANNEL, ANNOUNCEMENT_CHANNEL = 0, 5
COMPONENT, EPHEMERAL = 3, 1 << 6
DIRECT_MESSAGES = 1 << 12  # the gateway intent for DMs to the bot
STYLES = {"primary": 3, "danger": 4}  # success (green), danger (red); others grey
# Gateway opcodes
DISPATCH, HEARTBEAT, IDENTIFY, RECONNECT, INVALID_SESSION, HELLO = 0, 1, 2, 7, 9, 10


def invite_url(application_id: str) -> str:
    return (
        "https://discord.com/oauth2/authorize?client_id="
        f"{application_id}&scope=bot&permissions={PERMISSIONS}"
    )


class DiscordApi:
    def __init__(self, token: str, base_url: str) -> None:
        scrub.register(token)
        self._token = token
        self._http = httpx2.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=30.0,
            transport=transport,
            headers={"Authorization": f"Bot {token}", "User-Agent": "Ringkeeper (ringkeeper, 1)"},
        )
        self._target: str | None = None
        self.target = ""  # a channel id, or "user:<id>" for a DM
        self._sequence: int | None = None

    def close(self) -> None:
        self._http.close()

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.request(method, path, json=body)
        except httpx2.HTTPError:
            raise ChannelError(
                "Couldn't reach Discord. Check this computer is online, then try again."
            ) from None
        if response.status_code == 204:
            return None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 400:
            said = cast(dict[str, Any], data).get("message") if isinstance(data, dict) else None
            raise ChannelError(
                "Discord didn't accept the bot token. Copy it again from the Bot page."
                if response.status_code == 401
                else "The bot can't post there. Invite it to the server with the link in Settings."
                if response.status_code == 403
                else f"Discord said: {said or response.status_code}. Check the bot, then try again."
            )
        return data

    # --- Setup ---

    def check(self) -> dict[str, str]:
        """The token works: the bot's name, and its application (for the invite link)."""
        me = cast(dict[str, Any], self.call("GET", "users/@me"))
        app = cast(dict[str, Any], self.call("GET", "oauth2/applications/@me"))
        app_id = str(app.get("id") or me.get("id"))
        bot = str(me.get("username") or "")
        return {"bot": bot, "app_id": app_id, "invite": invite_url(app_id)}

    def channels(self) -> list[dict[str, str]]:
        """Text channels in the servers the bot was invited to."""
        found: list[dict[str, str]] = []
        for guild in cast(list[dict[str, Any]], self.call("GET", "users/@me/guilds") or []):
            channels = cast(
                list[dict[str, Any]], self.call("GET", f"guilds/{guild['id']}/channels") or []
            )
            found += [
                {"id": str(c["id"]), "name": f"{guild['name']} / #{c['name']}"}
                for c in channels
                if c.get("type") in (TEXT_CHANNEL, ANNOUNCEMENT_CHANNEL)
            ]
        return sorted(found, key=lambda c: c["name"])

    # --- Messages ---

    def _channel(self) -> str:
        if self._target is None:
            if self.target.startswith("user:"):
                recipient = self.target.removeprefix("user:")
                opened = self.call("POST", "users/@me/channels", {"recipient_id": recipient})
                self._target = str(cast(dict[str, Any], opened)["id"])
            else:
                self._target = self.target
        return self._target

    def post(self, card: Card) -> str:
        channel = self._channel()
        sent = self.call("POST", f"channels/{channel}/messages", message(card))
        return f"{channel}/{cast(dict[str, Any], sent)['id']}"

    def update(self, ref: str, card: Card) -> None:
        channel, ident = ref.split("/", 1)
        self.call("PATCH", f"channels/{channel}/messages/{ident}", message(card))

    def direct(self, user: str, card: Card) -> str:
        """A message in the DM with this person."""
        opened = self.call("POST", "users/@me/channels", {"recipient_id": user})
        channel = str(cast(dict[str, Any], opened)["id"])
        sent = self.call("POST", f"channels/{channel}/messages", message(card))
        return f"{channel}/{cast(dict[str, Any], sent)['id']}"

    def attach(self, user: str, files: list[tuple[str, bytes]]) -> None:
        """Files in the DM with this person, in one message (at most 10, Discord's limit)."""
        opened = self.call("POST", "users/@me/channels", {"recipient_id": user})
        channel = str(cast(dict[str, Any], opened)["id"])
        sent = files[:10]
        payload = {"attachments": [{"id": i, "filename": name} for i, (name, _) in enumerate(sent)]}
        try:
            response = self._http.post(
                f"channels/{channel}/messages",
                data={"payload_json": json.dumps(payload)},
                files=[(f"files[{i}]", (name, data)) for i, (name, data) in enumerate(sent)],
            )
        except httpx2.HTTPError:
            raise ChannelError("Couldn't send a file to Discord. Try again later.") from None
        if response.status_code >= 400:
            raise ChannelError("Discord didn't take a file. Check the bot, then try again.")

    # --- Button presses and DMs, over the gateway ---

    def listen(
        self, stop: threading.Event, on_press: OnPress, on_message: OnMessage | None = None
    ) -> None:
        gateway = cast(dict[str, Any], self.call("GET", "gateway/bot"))
        url = f"{gateway['url']}?v=10&encoding=json"
        with connect(url, open_timeout=15, proxy=None, max_size=8 * 1024 * 1024) as socket:
            hello = json.loads(socket.recv(timeout=30))
            if hello.get("op") != HELLO:
                return
            every = float(hello["d"]["heartbeat_interval"]) / 1000
            beating = threading.Event()
            threading.Thread(
                target=self._heartbeat, args=(socket, every, stop, beating), daemon=True,
                name="rk-discord-beat",
            ).start()  # fmt: skip
            try:
                socket.send(json.dumps({"op": IDENTIFY, "d": self._identify()}))
                self._receive(socket, stop, on_press, on_message)
            finally:
                beating.set()

    def _identify(self) -> dict[str, Any]:
        system = platform.system().lower() or "unknown"
        return {
            "token": self._token,
            "intents": DIRECT_MESSAGES,  # interactions arrive without any intents
            "properties": {"os": system, "browser": "ringkeeper", "device": "ringkeeper"},
        }

    def _heartbeat(
        self, socket: ClientConnection, every: float, stop: threading.Event, done: threading.Event
    ) -> None:
        while not (stop.is_set() or done.wait(every)):
            try:
                socket.send(json.dumps({"op": HEARTBEAT, "d": self._sequence}))
            except ConnectionClosed:
                return

    def _receive(
        self,
        socket: ClientConnection,
        stop: threading.Event,
        on_press: OnPress,
        on_message: OnMessage | None = None,
    ) -> None:
        while not stop.is_set():
            try:
                raw = socket.recv(timeout=1.0)
            except TimeoutError:
                continue
            except ConnectionClosed:
                return
            event = cast(dict[str, Any], json.loads(raw))
            if event.get("s") is not None:
                self._sequence = int(event["s"])
            op = event.get("op")
            if op == HEARTBEAT:
                socket.send(json.dumps({"op": HEARTBEAT, "d": self._sequence}))
            elif op in (RECONNECT, INVALID_SESSION):
                return  # a fresh connection
            elif op == DISPATCH and event.get("t") == "INTERACTION_CREATE":
                self._interaction(cast(dict[str, Any], event.get("d") or {}), on_press)
            elif op == DISPATCH and event.get("t") == "MESSAGE_CREATE" and on_message is not None:
                self._dm(cast(dict[str, Any], event.get("d") or {}), on_message)

    def _dm(self, sent: dict[str, Any], on_message: OnMessage) -> None:
        """A person's DM to the bot (never a server channel's message, nor a bot's)."""
        author = cast(dict[str, Any], sent.get("author") or {})
        if sent.get("guild_id") or author.get("bot") or not author.get("id"):
            return
        user = str(author["id"])
        who = f"discord:{user}" + (f" ({author['username']})" if author.get("username") else "")
        mentions = cast(list[dict[str, Any]], sent.get("mentions") or [])
        said = plain.from_discord(str(sent.get("content") or ""), mentions)
        reply = on_message(user, said, who)
        if reply and sent.get("channel_id"):
            try:
                self.call(
                    "POST", f"channels/{sent['channel_id']}/messages",
                    {"content": scrub.scrub(reply)[:TEXT_LIMIT], "allowed_mentions": {"parse": []}},
                )  # fmt: skip
            except ChannelError as error:
                logger.warning("could not answer a Discord DM: %s", error)

    def _interaction(self, interaction: dict[str, Any], on_press: OnPress) -> None:
        if interaction.get("type") != COMPONENT:
            return
        member = cast(dict[str, Any], interaction.get("member") or {})
        user = cast(dict[str, Any], member.get("user") or interaction.get("user") or {})
        user_id = str(user.get("id") or "")
        who = f"discord:{user_id}" + (f" ({user['username']})" if user.get("username") else "")
        action = str(cast(dict[str, Any], interaction.get("data") or {}).get("custom_id") or "")
        reply = on_press(action, user_id, who)
        try:
            self.call(
                "POST",
                f"interactions/{interaction['id']}/{interaction['token']}/callback",
                {"type": 4, "data": {"content": scrub.scrub(reply), "flags": EPHEMERAL}},
            )
        except ChannelError as error:
            logger.warning("could not answer a Discord button: %s", error)


def _button(button: Button) -> dict[str, Any]:
    if button.url:
        return {"type": 2, "style": 5, "label": button.label[:80], "url": button.url}
    return {
        "type": 2,
        "style": STYLES.get(button.style or "", 2),
        "label": button.label[:80],
        "custom_id": button.action,
    }


def message(card: Card) -> dict[str, Any]:
    """Plain text, no mentions, then the buttons (five to a row)."""
    text = card.text if len(card.text) <= TEXT_LIMIT else card.text[: TEXT_LIMIT - 1] + "…"
    buttons = [_button(b) for b in card.buttons[:25]]
    rows = [{"type": 1, "components": buttons[i : i + 5]} for i in range(0, len(buttons), 5)]
    return {"content": text, "components": rows, "allowed_mentions": {"parse": []}}
