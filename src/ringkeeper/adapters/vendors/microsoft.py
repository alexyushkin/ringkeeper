"""A Microsoft account through Microsoft Graph: mail, calendar, OneDrive and SharePoint files.

The same holds as a Google account. Reading runs and every write waits for approval. One email
is held, and more than 10 recipients needs the name typed, as for every email connection
(adapters.mail). Deleting and sharing need the name typed.

Reading an attachment or a OneDrive or SharePoint file, looking at a page of it, and saving a
copy of it in the project run too; the last two are audited. A file's content comes from the
signed link Graph answers with, which is followed once and never shown.

A send whose recipients can't be counted from the call needs the name typed too: send_draft
(they're in the draft) and reply_all (they're on the message).

Left out on purpose:
- Mail rules and mailbox settings: a forwarding rule or an auto-reply goes on running by itself.
- /$batch, delta queries, and subscriptions.
- Uploads: a call's body is JSON, and a file isn't.
- findMeetingTimes and getSchedule: reads sent by POST, which a table can't call a read.
- Every other user's mailbox, calendar or drive: a call only reaches /me, and the drives and
  sites this account can already see.
"""

import base64
import binascii
from typing import Any, cast

from ringkeeper.adapters import extract, files, mail_text
from ringkeeper.adapters.base import ToolError
from ringkeeper.adapters.mail import BULK
from ringkeeper.adapters.paging import PAGE_CHARS
from ringkeeper.adapters.vendor_spec import (
    CONTENT,
    JSON,
    PROJECT,
    Snapshot,
    Vendor,
    drafts,
    final,
    held,
    reads,
)

CATEGORY = "Email"
# What each kind of access asks for at sign-in, by the access keys of services.google_oauth.
# Space-separated where it takes two: finding SharePoint sites needs Sites.Read.All.
SCOPES = {
    "mail_read": "Mail.Read",
    "mail_send": "Mail.Send",
    "mail_drafts": "Mail.ReadWrite",
    "calendar_read": "Calendars.Read",
    "calendar_write": "Calendars.ReadWrite",
    "files_read": "Files.Read.All Sites.Read.All",
    "files_write": "Files.ReadWrite.All",
}
ALWAYS = "User.Read offline_access"  # who signed in, and staying signed in
RECIPIENTS = ("toRecipients", "ccRecipients", "bccRecipients")
# Well-known folders a move can name. Moving to one of these is deleting.
BIN = frozenset({"deleteditems", "recoverableitemsdeletions"})
BODY_PAGING = ("offset", "limit", "include_quoted")
ATTACHMENT_FIELDS = "id,name,contentType,size"
THREAD_FIELDS = "id,conversationId,from,toRecipients,ccRecipients,subject,receivedDateTime,body"
MOST_IN_THREAD = 200
ODATA = ("$select", "$filter", "$orderby", "$top", "$skip")
DRAFT = (
    "subject", "body", "toRecipients", "ccRecipients", "bccRecipients", "replyTo", "importance",
    "categories",
)  # fmt: skip
NEW_DRAFT = (*DRAFT, "attachments")
EVENT = (
    "subject", "body", "start", "end", "location", "locations", "attendees", "isOnlineMeeting",
    "onlineMeetingProvider", "isAllDay", "recurrence", "reminderMinutesBeforeStart", "showAs",
    "sensitivity", "categories",
)  # fmt: skip
# What an edit may change is what's put back, so undoing it leaves nothing behind.
EVENT_EDIT = (
    "subject", "body", "start", "end", "location", "attendees", "isAllDay", "showAs",
    "sensitivity", "categories", "reminderMinutesBeforeStart",
)  # fmt: skip
EVENT_BACK = Snapshot("get_event", EVENT_EDIT, says="the event's details")
DRAFT_BACK = Snapshot("get_message", DRAFT, says="the draft as it was")
FLAGS = ("isRead", "flag", "categories", "importance")
FLAGS_BACK = Snapshot("get_message", FLAGS, says="how the message was marked")
FILE_BACK = Snapshot("get_file", ("name", "description"), says="the file's name and description")
FILE = "/drives/{drive_id}/items/{item_id}"
ATTACHMENT = "/me/messages/{message_id}/attachments/{attachment_id}"
READING = ("pages", "inside", "offset", "limit")
VIEWING = ("page", "picture", "inside")
ACCESS = "changes who can reach a file"


def _count(value: Any) -> int:
    return len(cast(list[Any], value)) if isinstance(value, list) else 0


def _recipients_why(args: JSON) -> str | None:
    """Why this send needs the name typed: it goes to many people at once. Recipients sit in
    the body (forward) or in its `message` (sendMail, reply), and a call may use both."""
    body = cast(JSON, args.get("body") or {})
    inner = body.get("message")
    places = [body, cast(JSON, inner)] if isinstance(inner, dict) else [body]
    total = sum(_count(place.get(key)) for place in places for key in RECIPIENTS)
    return f"sends to {total} people at once" if total > BULK else None


def _attendees_why(args: JSON) -> str | None:
    """An event with attendees sends them invitations: too many is a mailing."""
    total = _count(cast(JSON, args.get("body") or {}).get("attendees"))
    return f"invites {total} people at once" if total > BULK else None


def _move_why(args: JSON) -> str | None:
    """By name only: the folder's own ID isn't recognised. Deleted Items can be emptied back."""
    folder = str(cast(JSON, args.get("body") or {}).get("destinationId") or "").lower()
    return "moves a message to Deleted Items" if folder in BIN else None


def _number(value: Any, default: int, most: int | None = None) -> int:
    """A paging value from a call's query, which isn't checked by a schema."""
    try:
        number = max(int(value), 0) if value is not None else default
    except (TypeError, ValueError):
        raise ToolError(f"'{value}' isn't a number") from None
    return min(number, most) if most is not None else number


def _with_attachments(query: JSON, _local: JSON) -> JSON:
    return {"$expand": f"attachments($select={ATTACHMENT_FIELDS})"} | query


def _attachment_names(query: JSON, _local: JSON) -> JSON:
    """Graph sends every attachment's whole content unless told not to."""
    return query | {"$select": ATTACHMENT_FIELDS}


def _attachment_row(found: JSON) -> JSON:
    return {
        "id": found.get("id"), "name": found.get("name"), "type": found.get("contentType"),
        "size": found.get("size"),
    }  # fmt: skip


def _listed(answer: JSON, _local: JSON) -> JSON:
    found = cast(list[JSON], answer.get("value") or [])
    return {"attachments": [_attachment_row(a) for a in found]}


def _text_of(message: JSON) -> str:
    body = cast(JSON, message.get("body") or {})
    content = str(body.get("content") or "")
    html = str(body.get("contentType") or "").lower() == "html"
    return mail_text.html_to_text(content) if html else content


def _shown(message: JSON, offset: int, limit: int, include_quoted: bool = False) -> JSON:
    """A message as an agent reads it: text rather than HTML, and what part of it this is."""
    if not isinstance(message.get("body"), dict):  # a $select that left it out
        return message
    kept = {k: v for k, v in message.items() if k not in ("body", "bodyPreview", "attachments")}
    attachments = cast(list[JSON], message.get("attachments") or [])
    return kept | mail_text.body_fields(_text_of(message), offset, limit, include_quoted) | {
        "attachments": [_attachment_row(a) for a in attachments]
    }  # fmt: skip


def _saved(answer: JSON, _local: JSON) -> JSON:
    """A saved draft, in short: its id (its undo deletes it), subject and where it opens."""
    return {
        "saved": "draft", "id": answer.get("id"), "subject": answer.get("subject"),
        "link": answer.get("webLink"),
    }  # fmt: skip


def _readable(answer: JSON, local: JSON) -> JSON:
    return _shown(
        answer,
        _number(local.get("offset"), 0),
        _number(local.get("limit"), PAGE_CHARS, PAGE_CHARS),
        str(local.get("include_quoted")).lower() == "true",
    )


def _thread_query(_query: JSON, local: JSON) -> JSON:
    """Not sorted by Graph: it refuses to sort by one field and filter by another."""
    conversation = str(local.get("conversation_id") or "").replace("'", "''")
    if not conversation:
        raise ToolError("name the conversation: conversation_id, from a message")
    return {
        "$filter": f"conversationId eq '{conversation}'",
        "$select": THREAD_FIELDS,
        "$expand": f"attachments($select={ATTACHMENT_FIELDS})",
        "$top": MOST_IN_THREAD,
    }


def _thread(answer: JSON, local: JSON) -> JSON:
    found = sorted(
        cast(list[JSON], answer.get("value") or []),
        key=lambda m: str(m.get("receivedDateTime") or ""),
    )
    chars = _number(local.get("body_chars"), mail_text.THREAD_BODY_CHARS, PAGE_CHARS)
    shown = [_shown(m, 0, chars) for m in found]
    part = mail_text.thread_page(
        shown, _number(local.get("offset"), 0), _number(local.get("limit"), 20, 100) or 20
    )
    if "@odata.nextLink" not in answer:
        return part
    return part | {
        "truncated": True,
        "read_note": f"This conversation has more than {MOST_IN_THREAD} messages: only "
        f"{MOST_IN_THREAD} were read.",
    }


def _attached(answer: JSON) -> tuple[JSON, bytes]:
    """An attachment as Graph sends it: its name and type, and its bytes."""
    name = str(answer.get("name") or "attachment")
    kind = str(answer.get("contentType") or "")
    if not answer.get("contentBytes"):
        raise ToolError(f"'{name}' is an attached item or a link, not a file")
    try:
        data = base64.b64decode(str(answer["contentBytes"]))
    except binascii.Error:
        raise ToolError(f"'{name}' came back damaged") from None
    return {"name": name, "type": kind, "size": len(data)}, data


def _downloaded(answer: JSON) -> tuple[JSON, bytes]:
    """A OneDrive or SharePoint file: its details, and the content fetched with them."""
    data = cast(bytes, answer.get(CONTENT) or b"")
    kind = str(cast(JSON, answer.get("file") or {}).get("mimeType") or "")
    meta = {"id": answer.get("id"), "name": str(answer.get("name") or "file"), "type": kind}
    return meta | {"size": len(data)}, data


def _inside(local: JSON) -> str | None:
    return str(local["inside"]) if local.get("inside") else None


def _text(meta: JSON, data: bytes, local: JSON, see: str) -> JSON:
    found = extract.extract(str(meta["name"]), str(meta["type"]), data, _inside(local))
    pages = str(local["pages"]) if local.get("pages") else None
    offset = _number(local.get("offset"), 0)
    limit = _number(local.get("limit"), PAGE_CHARS, PAGE_CHARS) or PAGE_CHARS
    return files.text_answer(meta, found, offset, limit, pages, see)


def _view(meta: JSON, data: bytes, local: JSON) -> JSON:
    page = _number(local.get("page"), 1) or 1
    picture = _number(local.get("picture"), 0) or None
    shown = extract.view(str(meta["name"]), str(meta["type"]), data, page, picture, _inside(local))
    return files.picture_answer(meta, shown)


async def _save(meta: JSON, data: bytes, local: JSON) -> JSON:
    name, _kind, data = extract.opened(str(meta["name"]), str(meta["type"]), data, _inside(local))
    return await files.save(cast(str | None, local.get(PROJECT)), name, data)


def _attachment(answer: JSON, local: JSON) -> JSON:
    meta, data = _attached(answer)
    return _text(meta, data, local, "microsoft.view_attachment")


def _attachment_view(answer: JSON, local: JSON) -> JSON:
    return _view(*_attached(answer), local)


async def _attachment_save(answer: JSON, local: JSON) -> JSON:
    return await _save(*_attached(answer), local)


def _file(answer: JSON, local: JSON) -> JSON:
    meta, data = _downloaded(answer)
    return _text(meta, data, local, "microsoft.view_file")


def _file_view(answer: JSON, local: JSON) -> JSON:
    return _view(*_downloaded(answer), local)


async def _file_save(answer: JSON, local: JSON) -> JSON:
    return await _save(*_downloaded(answer), local)


def _who(found: JSON) -> str:
    return str(found.get("mail") or found.get("userPrincipalName") or found.get("displayName"))


MICROSOFT = Vendor(
    "microsoft", "Microsoft account", CATEGORY, "Outlook mail, calendar, OneDrive, SharePoint",
    "https://graph.microsoft.com/v1.0",
    provider="microsoft", probe="get_me", who=_who,
    words=("microsoft", "outlook", "office 365", "microsoft 365", "m365", "exchange", "email",
           "mail", "calendar", "onedrive", "sharepoint", "files", "graph"),
    key_url="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
    steps="Add your Microsoft app in Settings, Developer apps, then sign in from this tile with "
    "the account, and choose what it may do. Each account is its own connection.",
    note="One connection is one Microsoft account. Reading runs. Every write waits for approval: "
    "an email, a draft, an event, a folder. More than 10 recipients or attendees, deleting, and "
    "sharing a file need the name typed.",
    ops=(
        reads("get_me", "/me", "Who this account is"),

        # Mail. mail_read to read, mail_drafts to change, mail_send to send.
        reads("list_messages", "/me/messages", "Find messages", "$search", *ODATA),
        reads("get_message", "/me/messages/{message_id}",
              "Read one message: its whole text, in parts if it's long, and its attachments. "
              "Earlier messages a reply quotes are in `quoted`", "$select",
              local=BODY_PAGING, prepare=_with_attachments, after=_readable),
        reads("get_thread", "/me/messages",
              "A conversation, oldest message first: conversation_id from a message. Each "
              "message shows the start of its text; get_message reads the rest",
              local=("conversation_id", "offset", "limit", "body_chars"),
              prepare=_thread_query, after=_thread),
        reads("list_mail_folders", "/me/mailFolders", "Mail folders", *ODATA),
        reads("list_attachments", "/me/messages/{message_id}/attachments",
              "A message's attachments", "$top", prepare=_attachment_names, after=_listed),
        reads("read_attachment", ATTACHMENT, "The text of one attachment: PDF, Word, Excel, "
              "PowerPoint, CSV, text, an attached email or a .zip (inside= a file in it), and "
              "pictures where OCR is installed. A long one starts with its outline: read on "
              "with pages=", local=READING, after=_attachment),
        reads("view_attachment", ATTACHMENT, "One page of an attached PDF, or a picture, as an "
              "image: better than the text for a table, a chart or a scan", local=VIEWING,
              after=_attachment_view, audited=True),
        reads("save_attachment", ATTACHMENT, "Save a copy of an attachment in the project's "
              "folder (attachments/) to work on it there. Answers its path", local=("inside",),
              after=_attachment_save, audited=True),
        final("send_mail", "POST", "/me/sendMail", "Send an email", _recipients_why, "message",
              "saveToSentItems", only=("message", "saveToSentItems")),
        final("reply", "POST", "/me/messages/{message_id}/reply", "Reply to the sender",
              _recipients_why, "comment", "message", only=("comment", "message")),
        final("forward", "POST", "/me/messages/{message_id}/forward", "Forward a message",
              _recipients_why, "comment", "toRecipients", "message",
              only=("comment", "toRecipients", "message")),
        drafts("create_draft", "POST", "/me/messages", "Save an email as a draft", *NEW_DRAFT,
               only=NEW_DRAFT, remove="delete_message", remove_key="message_id",
               hint="Put the draft's link in your final message.", after=_saved),
        held("update_draft", "PATCH", "/me/messages/{message_id}", "Change a draft", *DRAFT,
             only=DRAFT, undo=DRAFT_BACK),
        held("mark_message", "PATCH", "/me/messages/{message_id}",
             "Mark a message read, flagged, or in a category", *FLAGS, only=FLAGS,
             undo=FLAGS_BACK),
        final("move_message", "POST", "/me/messages/{message_id}/move",
              "Move a message to a folder", _move_why, "destinationId", only=("destinationId",)),
        final("send_draft", "POST", "/me/messages/{message_id}/send", "Send a saved draft",
              "sends a draft, and how many people it goes to can't be seen here"),
        final("reply_all", "POST", "/me/messages/{message_id}/replyAll",
              "Reply to everyone on a message",
              "replies to everyone on a message, and how many that is can't be seen here",
              "comment", only=("comment", "message")),
        final("delete_message", "DELETE", "/me/messages/{message_id}", "Delete a message",
              "deletes an email"),

        # Calendar. calendar_read to read, calendar_write to change.
        reads("list_calendars", "/me/calendars", "This account's calendars", *ODATA),
        reads("list_events_between", "/me/calendarView", "Events between two dates, repeats "
              "included", "startDateTime", "endDateTime", *ODATA),
        reads("get_event", "/me/events/{event_id}", "Read one event", "$select"),
        final("create_event", "POST", "/me/events", "Add an event. Attendees get an invitation",
              _attendees_why, *EVENT, only=EVENT),
        final("update_event", "PATCH", "/me/events/{event_id}", "Change an event. Attendees are "
              "told", _attendees_why, *EVENT_EDIT, only=EVENT_EDIT, undo=EVENT_BACK),
        held("accept_event", "POST", "/me/events/{event_id}/accept", "Accept an invitation",
             "comment", "sendResponse", only=("comment", "sendResponse")),
        held("decline_event", "POST", "/me/events/{event_id}/decline", "Decline an invitation",
             "comment", "sendResponse", only=("comment", "sendResponse")),
        final("cancel_event", "POST", "/me/events/{event_id}/cancel", "Cancel a meeting you "
              "organize, and tell its attendees", "cancels a meeting for everyone on it",
              "comment", only=("comment",)),
        final("delete_event", "DELETE", "/me/events/{event_id}", "Delete an event",
              "deletes an event"),

        # Files. files_read to read, files_write to change.
        reads("get_my_drive", "/me/drive", "This account's OneDrive, with its ID"),
        reads("list_my_files", "/me/drive/root/children", "The top of this account's OneDrive",
              *ODATA),
        reads("list_folder", f"{FILE}/children", "What's in a folder", *ODATA),
        reads("get_file", FILE, "One file or folder's details", "$select"),
        reads("read_file", FILE, "A file's text: PDF, Word, Excel, PowerPoint, text, a .zip "
              "(inside= a file in it). A long one starts with its outline: read on with pages=",
              local=READING, download="/content", after=_file),
        reads("view_file", FILE, "One page of a PDF, or a picture, as an image: better than "
              "the text for a table, a chart or a scan", local=VIEWING, download="/content",
              after=_file_view, audited=True),
        reads("save_file", FILE, "Save a copy of a file in the project's folder "
              "(attachments/) to work on it there. Answers its path", local=("inside",),
              download="/content", after=_file_save, audited=True),
        reads("list_file_access", f"{FILE}/permissions", "Who can reach a file", "$select"),
        reads("search_files", "/search/query", "Search files by what's in them: body requests, "
              "with entityTypes driveItem and a query.queryString", "requests", method="POST"),
        reads("search_sites", "/sites", "Find SharePoint sites", "search"),
        reads("list_site_drives", "/sites/{site_id}/drives", "A SharePoint site's libraries"),
        held("create_folder", "POST", f"{FILE}/children", "Add a folder", "name", "folder",
             "@microsoft.graph.conflictBehavior",
             only=("name", "folder", "@microsoft.graph.conflictBehavior")),
        held("rename_file", "PATCH", FILE, "Rename a file or folder", "name", "description",
             only=("name", "description"), undo=FILE_BACK),
        held("move_file", "PATCH", FILE, "Move a file or folder", "parentReference", "name",
             only=("parentReference", "name")),
        held("copy_file", "POST", f"{FILE}/copy", "Copy a file or folder", "parentReference",
             "name", only=("parentReference", "name")),
        final("delete_file", "DELETE", FILE, "Delete a file or folder", "deletes a file"),
        final("share_file", "POST", f"{FILE}/invite", "Share a file with people", ACCESS,
              "recipients", "roles", "requireSignIn", "sendInvitation", "message",
              only=("recipients", "roles", "requireSignIn", "sendInvitation", "message",
                    "expirationDateTime")),
        final("create_share_link", "POST", f"{FILE}/createLink", "Make a link to a file", ACCESS,
              "type", "scope", "expirationDateTime",
              only=("type", "scope", "expirationDateTime", "retainInheritedPermissions")),
        final("remove_file_access", "DELETE", f"{FILE}/permissions/{{permission_id}}",
              "Take away someone's access to a file", ACCESS),
    ),
)  # fmt: skip

VENDORS = (MICROSOFT,)
