"""A Microsoft account through Microsoft Graph: mail, calendar, OneDrive and SharePoint files.

The same holds as a Google account. Reading runs and every write waits for approval. One email
is held, and more than 10 recipients needs the name typed, as for every email connection
(adapters.mail). Deleting and sharing need the name typed.

A send whose recipients can't be counted from the call needs the name typed too: send_draft
(they're in the draft) and reply_all (they're on the message).

Left out on purpose:
- Mail rules and mailbox settings: a forwarding rule or an auto-reply goes on running by itself.
- /$batch, delta queries, and subscriptions.
- A file's content: Graph answers with a redirect to a signed link, and a call answers JSON.
- Uploads: a call's body is JSON, and a file isn't.
- findMeetingTimes and getSchedule: reads sent by POST, which a table can't call a read.
- Every other user's mailbox, calendar or drive: a call only reaches /me, and the drives and
  sites this account can already see.
"""

from typing import Any, cast

from ringkeeper.adapters.mail import BULK
from ringkeeper.adapters.vendor_spec import JSON, Snapshot, Vendor, final, held, reads

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
        reads("get_message", "/me/messages/{message_id}", "Read one message", "$select"),
        reads("list_mail_folders", "/me/mailFolders", "Mail folders", *ODATA),
        reads("list_attachments", "/me/messages/{message_id}/attachments",
              "A message's attachments", "$select", "$top"),
        final("send_mail", "POST", "/me/sendMail", "Send an email", _recipients_why, "message",
              "saveToSentItems", only=("message", "saveToSentItems")),
        final("reply", "POST", "/me/messages/{message_id}/reply", "Reply to the sender",
              _recipients_why, "comment", "message", only=("comment", "message")),
        final("forward", "POST", "/me/messages/{message_id}/forward", "Forward a message",
              _recipients_why, "comment", "toRecipients", "message",
              only=("comment", "toRecipients", "message")),
        held("create_draft", "POST", "/me/messages", "Save an email as a draft", *NEW_DRAFT,
             only=NEW_DRAFT),
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
