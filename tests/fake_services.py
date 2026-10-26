"""Small stand-ins for Airtable, Vercel and Twilio (httpx2.MockTransport) and Gmail
(IMAP and SMTP objects): enough to check what the adapters send and read back."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import Message
from typing import Any

import httpx2

AIRTABLE_TOKEN = "patFAKEairtable0123456789.abcdef"
VERCEL_TOKEN = "vercel-token-fake-5b9e21aa"
TWILIO_SECRET = json.dumps(
    {
        "account_sid": "AC" + "1" * 32,
        "key_sid": "SK" + "2" * 32,
        "key_secret": "twilio-secret-fake-77",
    }
)
GMAIL_SECRET = json.dumps({"address": "owner@example.com", "app_password": "abcd efgh ijkl mnop"})


@dataclass
class FakeHttp:
    """Answers by (method, path) with canned JSON; records every request."""

    routes: dict[tuple[str, str], Any] = field(default_factory=dict[tuple[str, str], Any])
    requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])

    def transport(self) -> httpx2.MockTransport:
        def handle(request: httpx2.Request) -> httpx2.Response:
            self.requests.append(request)
            answer = self.routes.get((request.method, request.url.path))
            if answer is None:
                return httpx2.Response(404, json={"error": {"message": "no such thing"}})
            if isinstance(answer, httpx2.Response):
                return answer
            return httpx2.Response(200, json=answer)

        return httpx2.MockTransport(handle)


MAILBOXES = [
    b'(\\HasNoChildren) "/" "INBOX"',
    b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Brouillons"',  # a French Gmail
    b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Messages envoy&AOk-s"',
    b'(\\All \\HasNoChildren) "/" "[Gmail]/Tous les messages"',
]

MESSAGE = (
    b"From: Anna <anna@acme.example>\r\nTo: owner@example.com\r\nSubject: Invoice 42\r\n"
    b"Date: Mon, 26 Oct 2026 09:00:00 +0000\r\nMessage-ID: <m42@acme.example>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\nPlease find the invoice attached.\r\n"
)


@dataclass
class FakeImap:
    password: str = "abcdefghijklmnop"
    refusal: str | None = None  # what Gmail says when it refuses the login
    messages: dict[int, bytes] = field(default_factory=lambda: {7: MESSAGE})
    appended: list[tuple[str, str, bytes]] = field(default_factory=list[tuple[str, str, bytes]])
    commands: list[tuple[Any, ...]] = field(default_factory=list[tuple[Any, ...]])
    literal: bytes | None = None
    selected: str | None = None
    logged_out: bool = False

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        import imaplib  # noqa: PLC0415

        if self.refusal or password != self.password:
            raise imaplib.IMAP4.error(self.refusal or "[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"logged in"]

    def list(self) -> tuple[str, list[bytes]]:
        return "OK", MAILBOXES

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        assert readonly, "reads must never change the mailbox"
        self.selected = mailbox
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.commands.append((command, *args, self.literal))
        if command == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in sorted(self.messages))]
        uid = int(args[0])
        if uid not in self.messages:
            return "OK", [None]
        raw = self.messages[uid]
        if "HEADER.FIELDS" in args[1]:
            raw = raw.split(b"\r\n\r\n")[0] + b"\r\n\r\n"
        return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def append(
        self, mailbox: str, flags: str, _when: str, message: bytes
    ) -> tuple[str, list[bytes]]:
        self.appended.append((mailbox, flags, message))
        return "OK", [b"APPEND completed"]

    def logout(self) -> None:
        self.logged_out = True

    def shutdown(self) -> None:
        self.logged_out = True


@dataclass
class FakeSmtp:
    password: str = "abcdefghijklmnop"
    sent: list[Message] = field(default_factory=list[Message])

    def __enter__(self) -> FakeSmtp:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        import smtplib  # noqa: PLC0415

        if password != self.password:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, message: Message) -> None:
        self.sent.append(message_from_bytes(message.as_bytes()))
