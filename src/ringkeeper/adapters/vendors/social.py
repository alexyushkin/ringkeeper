"""Social accounts: Bluesky, Facebook Pages, Instagram and Threads.

Holds, by the user's rule: reading runs. Anything nobody can see yet waits for approval (a
scheduled or unpublished Page post, a media container). Publishing, editing what's live,
replying and deleting need the name typed.

A scheduled Page post goes out by itself at its time: that's the point of it, and approving it
is what allows that. Instagram and Threads have no drafts of their own, so a draft there is the
held card itself, or a container waiting to be published.

Facebook and Instagram sign in through the owner's Meta app, Threads through the same app's
Threads use case, and each takes its token as a form field or a query value, as Meta documents.
That token is a Page's: each Page has its own, and it never leaves the connection. So every read
names its fields in the operation's path, where a call can't change them: asked for
`access_token`, a Page would hand one back.

Left out on purpose:
- Direct messages, on all four. They need more of the vendor's review, and Bluesky a different
  kind of app password.
- Pictures and video on Bluesky: an upload's body is a file, and a call's is JSON. Instagram and
  Threads fetch media from a public address instead.
- Facebook: Page settings, roles, ads, and anything about a person's own profile.
- Following, likes and blocks: nobody asked for them, and each reaches a real person.
"""

import re
from datetime import UTC, datetime
from typing import Any, cast

from ringkeeper.adapters.base import ToolError
from ringkeeper.adapters.vendor_spec import (
    JSON,
    Auth,
    Exchange,
    Secret,
    Vendor,
    final,
    held,
    reads,
)
from ringkeeper.remote import ScopeField

CATEGORY = "Social"
GRAPH = "https://graph.facebook.com/v27.0"
THREADS = "https://graph.threads.com/v1.0"
TOKEN_FIELD = "access_token"  # noqa: S105 (the field's name)
PUBLISHES = "publishes it for everyone to see"
REPLIES = "posts a reply in public"
EDITS = "changes a post people can already see"
DELETES = "deletes it for good"
NEEDS_REVIEW = (
    "Your own accounts work now. A client's account needs Meta's review of your app first."
)
SIGN_IN_UNTRIED = "Sign-in hasn't been tried against a real Meta app yet."


# --- Bluesky ---

BLUESKY_SERVICE = "https://bsky.social/xrpc"
POST = "app.bsky.feed.post"
MOST_LETTERS = 300  # Bluesky counts graphemes: this is close, and the service has the last word
HANDLE = r"^[a-zA-Z0-9][a-zA-Z0-9.-]{1,252}\.[a-zA-Z]{2,}$"
FOUR_GROUPS = r"^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$"
LINK = re.compile(r"https?://[^\s<>\"]+")


def _links(text: str) -> list[JSON]:
    """A link is only a link on Bluesky if the post says so, in bytes."""
    found: list[JSON] = []
    for match in LINK.finditer(text):
        url = match.group().rstrip(".,;:!?)")
        start = len(text[: match.start()].encode())
        found.append({
            "index": {"byteStart": start, "byteEnd": start + len(url.encode())},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
        })  # fmt: skip
    return found


def _bluesky_post(body: JSON, _account: str | None) -> JSON:
    text = str(body.get("text") or "").strip()
    if not text:
        raise ToolError("a post needs its text")
    if len(text) > MOST_LETTERS:
        raise ToolError(f"Bluesky posts stop at {MOST_LETTERS} characters")
    record: JSON = {
        "$type": POST, "text": text, "createdAt": datetime.now(UTC).isoformat(),
    }  # fmt: skip
    if facets := _links(text):
        record["facets"] = facets
    for key in ("reply", "langs"):
        if body.get(key):
            record[key] = body[key]
    if card := cast(JSON | None, body.get("link_card")):
        record["embed"] = {"$type": "app.bsky.embed.external", "external": card}
    return {"collection": POST, "record": record}


def _bluesky_delete(body: JSON, _account: str | None) -> JSON:
    rkey = str(body.get("rkey") or "")
    if not re.fullmatch(r"[A-Za-z0-9._~:-]{1,512}", rkey):
        raise ToolError("name the post by its rkey: the last part of its at:// address")
    return {"collection": POST, "rkey": rkey}


def _bluesky_who(found: JSON) -> str:
    return str(found.get("handle") or "the account")


BLUESKY = Vendor(
    "bluesky", "Bluesky", CATEGORY, "Posts, replies, notifications", BLUESKY_SERVICE,
    auth=Auth(field="session"),
    secrets=(
        Secret("repo", "Handle", "Like yourname.bsky.social.", hidden=False, pattern=HANDLE),
        Secret("app_password", "App password", "Four groups of four letters and numbers.",
               pattern=FOUR_GROUPS),
    ),
    exchange=Exchange(
        f"{BLUESKY_SERVICE}/com.atproto.server.createSession",
        {"identifier": "repo", "password": "app_password"},
        into="session", as_json=True, answer="accessJwt",
    ),
    probe="get_session", who=_bluesky_who, never_trusted=True,
    words=("bluesky", "bsky", "atproto", "social", "posts"),
    key_url="https://bsky.app/settings/app-passwords",
    steps="In Bluesky, open Settings, Privacy and security, App passwords, and add one. Don't "
    "use your account password.",
    note="Reading runs. Posting, replying and deleting need the name typed: Bluesky has no "
    "drafts, so a post waits on its approval card.",
    ops=(
        reads("get_session", "/com.atproto.server.getSession", "Which account this is"),
        reads("get_profile", "/app.bsky.actor.getProfile", "A profile, with its counts", "actor"),
        reads("list_posts", "/app.bsky.feed.getAuthorFeed", "An account's posts, with their "
              "likes, reposts and replies", "actor", "limit", "cursor", "filter"),
        reads("get_thread", "/app.bsky.feed.getPostThread", "A post and its replies", "uri",
              "depth"),
        reads("list_notifications", "/app.bsky.notification.listNotifications",
              "Replies, mentions and follows", "limit", "cursor"),
        reads("search_posts", "/app.bsky.feed.searchPosts", "Find posts", "q", "author", "limit",
              "cursor"),

        final("create_post", "POST", "/com.atproto.repo.createRecord", "Post, or reply",
              PUBLISHES, "text", "reply", "langs", "link_card",
              only=("text", "reply", "langs", "link_card"), shape=_bluesky_post,
              inject=("repo",)),
        final("delete_post", "POST", "/com.atproto.repo.deleteRecord", "Delete a post", DELETES,
              "rkey", only=("rkey",), shape=_bluesky_delete, inject=("repo",)),
    ),
)  # fmt: skip


# --- Facebook Pages and Instagram: one token per Page ---


def _pages(fields: JSON) -> list[JSON]:
    return cast(list[JSON], fields.get("pages") or [])


def _page_token(fields: JSON, account: str | None) -> str:
    pages = _pages(fields)
    if not pages:
        raise ToolError("this sign-in reaches no Pages: sign in again and pick them")
    page = next((p for p in pages if p.get("id") == account), None) if account else pages[0]
    if page is None:
        raise ToolError(f"'{account}' isn't a Page this sign-in reaches")
    return str(page["token"])


def _instagram_token(fields: JSON, account: str | None) -> str:
    linked = [p for p in _pages(fields) if p.get("instagram")]
    if not linked:
        raise ToolError("no Page here has an Instagram professional account linked to it")
    if account is None:
        return str(linked[0]["token"])
    page = next((p for p in linked if cast(JSON, p["instagram"]).get("id") == account), None)
    if page is None:
        raise ToolError(f"'{account}' isn't an Instagram account this sign-in reaches")
    return str(page["token"])


def _list_pages(fields: JSON, account: str | None) -> JSON:
    """What the sign-in reaches, and never a token. A project held to one Page sees that one."""
    found = [{"id": p.get("id"), "name": p.get("name")} for p in _pages(fields)]
    return {"pages": [p for p in found if account is None or p["id"] == account]}


def _list_instagram(fields: JSON, account: str | None) -> JSON:
    found = [cast(JSON, p["instagram"]) for p in _pages(fields) if p.get("instagram")]
    kept = [{"id": a.get("id"), "username": a.get("username")} for a in found]
    return {"accounts": [a for a in kept if account is None or a["id"] == account]}


def _not_published(value: Any) -> bool:
    return value is False or str(value).lower() == "false"


def _page_post_why(args: JSON) -> str | None:
    """Only a post that plainly says it isn't published is held. Anything else publishes."""
    body = cast(JSON, args.get("body") or {})
    return None if _not_published(body.get("published")) else PUBLISHES


def _published(_body: JSON, _account: str | None) -> JSON:
    return {"is_published": True}


def _hidden(body: JSON, _account: str | None) -> JSON:
    return {"is_hidden": body.get("is_hidden") is not False}


def _ig_hidden(body: JSON, _account: str | None) -> JSON:
    return {"hide": body.get("hide") is not False}


def _named(found: JSON) -> str:
    return str(found.get("name") or found.get("username") or "the account")


PAGE_FIELDS = "id,name,about,category,fan_count,followers_count,link,verification_status"
POST_FIELDS = "id,message,created_time,permalink_url,status_type,shares,is_published"
SCHEDULED_FIELDS = "id,message,created_time,scheduled_publish_time,is_published"
COMMENT_FIELDS = "id,from,message,created_time,like_count,comment_count,is_hidden,permalink_url"
PAGE_ITEM = ("message", "link", "published", "scheduled_publish_time")
PAGE_SCOPE = ScopeField("page", "Facebook Page", ("account",),
                        "The Page's ID. A sign-in can reach several.")  # fmt: skip
INSTAGRAM_SCOPE = ScopeField("instagram", "Instagram account", ("account",),
                             "The account's ID. A sign-in can reach several.")  # fmt: skip

FACEBOOK = Vendor(
    "facebook", "Facebook Pages", CATEGORY, "Posts, comments, insights", GRAPH,
    provider="meta", secrets=(), token_param=TOKEN_FIELD, token_of=_page_token,
    scope=(PAGE_SCOPE,), probe="get_me", who=_named, never_trusted=True,
    words=("facebook", "meta", "fb", "pages", "social", "posts", "comments"),
    key_url="https://developers.facebook.com/apps",
    steps="Add your Meta app in Settings, Developer apps, then sign in from this tile and pick "
    "the Pages it may reach.",
    note="Reading runs. A scheduled or unpublished post waits for approval. Publishing, editing "
    "a post, replying and deleting need the name typed. " + NEEDS_REVIEW,
    unverified=SIGN_IN_UNTRIED,
    ops=(
        reads("get_me", "/me?fields=id,name", "Which Page answers"),
        reads("list_pages", "/pages", "The Pages this sign-in reaches", answer=_list_pages),
        reads("get_page", f"/{{account}}?fields={PAGE_FIELDS}", "A Page's details"),
        reads("list_posts", f"/{{account}}/feed?fields={POST_FIELDS}", "A Page's posts", "limit",
              "after"),
        reads("list_scheduled", f"/{{account}}/scheduled_posts?fields={SCHEDULED_FIELDS}",
              "Posts waiting for their time", "limit", "after"),
        reads("list_comments", f"/{{post_id}}/comments?fields={COMMENT_FIELDS}",
              "Comments on a post, or replies to a comment", "limit", "after"),
        reads("page_insights", "/{account}/insights", "A Page's numbers", "metric", "period",
              "since", "until"),
        reads("post_insights", "/{post_id}/insights", "A post's numbers", "metric"),

        # Held: nobody sees these yet.
        final("create_post", "POST", "/{account}/feed", "Post to the Page", _page_post_why,
              *PAGE_ITEM, only=PAGE_ITEM,
              hint="With published false it's unpublished, and only then does it wait for a "
              "plain approval. scheduled_publish_time, 10 minutes to 30 days ahead, sets when it "
              "goes out by itself."),
        held("hide_comment", "POST", "/{comment_id}", "Hide a comment, or show it again",
             "is_hidden", only=("is_hidden",), shape=_hidden),

        # The name typed.
        final("publish_post", "POST", "/{post_id}", "Publish an unpublished post now", PUBLISHES,
              shape=_published),
        final("edit_post", "POST", "/{post_id}", "Change a post's text", EDITS, "message",
              only=("message",)),
        final("delete_post", "DELETE", "/{post_id}", "Delete a post", DELETES),
        final("reply_to_comment", "POST", "/{comment_id}/comments",
              "Comment on a post, or reply to a comment", REPLIES, "message",
              only=("message",)),
        final("delete_comment", "DELETE", "/{comment_id}", "Delete a comment", DELETES),
    ),
)  # fmt: skip

IG_FIELDS = "id,username,name,biography,followers_count,follows_count,media_count"
MEDIA_FIELDS = "id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count"
IG_COMMENT_FIELDS = "id,text,username,timestamp,hidden,like_count"
IG_ITEM = ("caption", "image_url", "video_url", "media_type", "children", "is_carousel_item")

INSTAGRAM = Vendor(
    "instagram", "Instagram", CATEGORY, "Business accounts: posts, comments, insights", GRAPH,
    provider="meta", secrets=(), token_param=TOKEN_FIELD, token_of=_instagram_token,
    scope=(INSTAGRAM_SCOPE,), probe="get_me", who=_named, never_trusted=True,
    words=("instagram", "ig", "insta", "meta", "social", "posts", "reels", "comments"),
    key_url="https://developers.facebook.com/apps",
    steps="The account must be a professional one, linked to a Facebook Page. Add your Meta app "
    "in Settings, Developer apps, then sign in from this tile.",
    note="Reading runs. Preparing a post waits for approval, and it stays unseen. Publishing it, "
    "replying and deleting need the name typed. Pictures and video come from a public "
    "address, and pictures must be JPEG. Instagram can't schedule through this API. "
    + NEEDS_REVIEW,
    unverified=SIGN_IN_UNTRIED,
    ops=(
        reads("get_me", "/me?fields=id,name", "Which linked Page answers"),
        reads("list_accounts", "/accounts", "The Instagram accounts this sign-in reaches",
              answer=_list_instagram),
        reads("get_account", f"/{{account}}?fields={IG_FIELDS}", "An account's details"),
        reads("list_media", f"/{{account}}/media?fields={MEDIA_FIELDS}", "An account's posts",
              "limit", "after"),
        reads("get_media", f"/{{media_id}}?fields={MEDIA_FIELDS}", "One post"),
        reads("list_comments", f"/{{media_id}}/comments?fields={IG_COMMENT_FIELDS}",
              "Comments on a post", "limit", "after"),
        reads("list_replies", f"/{{comment_id}}/replies?fields={IG_COMMENT_FIELDS}",
              "Replies to a comment"),
        reads("account_insights", "/{account}/insights", "An account's numbers", "metric",
              "period", "metric_type", "since", "until"),
        reads("media_insights", "/{media_id}/insights", "A post's numbers", "metric"),
        reads("publishing_limit", "/{account}/content_publishing_limit?fields=quota_usage,config",
              "How many posts are left in this 24 hours"),
        reads("container_status", "/{container_id}?fields=id,status_code",
              "Whether a prepared post is ready to publish"),

        # Held: nobody sees a container, and it ends by itself after a day.
        held("prepare_post", "POST", "/{account}/media", "Prepare a post", *IG_ITEM,
             only=IG_ITEM, hint="Then publish_post, with the id this gives as creation_id."),
        held("hide_comment", "POST", "/{comment_id}", "Hide a comment, or show it again", "hide",
             only=("hide",), shape=_ig_hidden),

        # The name typed.
        final("publish_post", "POST", "/{account}/media_publish", "Publish a prepared post",
              PUBLISHES, "creation_id", only=("creation_id",)),
        final("reply_to_comment", "POST", "/{comment_id}/replies", "Reply to a comment", REPLIES,
              "message", only=("message",)),
        final("delete_post", "DELETE", "/{media_id}", "Delete a post", DELETES),
        final("delete_comment", "DELETE", "/{comment_id}", "Delete a comment", DELETES),
    ),
)  # fmt: skip


# --- Threads ---

THREAD_FIELDS = "id,text,media_type,permalink,timestamp,username,is_reply"
REPLY_FIELDS = "id,text,username,timestamp,permalink,hide_status,is_reply"
THREAD_ITEM = ("media_type", "text", "image_url", "video_url", "reply_to_id", "link_attachment")


def _threads_hidden(body: JSON, _account: str | None) -> JSON:
    return {"hide": body.get("hide") is not False}


THREADS_VENDOR = Vendor(
    "threads", "Threads", CATEGORY, "Posts, replies, insights", THREADS,
    provider="threads", secrets=(), token_param=TOKEN_FIELD,
    probe="get_me", who=_named, never_trusted=True,
    words=("threads", "meta", "social", "posts", "replies"),
    key_url="https://developers.facebook.com/apps",
    steps="Add the Threads use case to your Meta app, add it in Settings, Developer apps, then "
    "sign in from this tile.",
    note="Reading runs. Preparing a post or a reply waits for approval, and it stays unseen. "
    "Publishing it and deleting need the name typed. Threads can't schedule through this API, "
    "and a sign-in lasts 60 days unless it's used. " + NEEDS_REVIEW,
    unverified=SIGN_IN_UNTRIED,
    ops=(
        reads("get_me", "/me?fields=id,username,name", "Which account this is"),
        reads("list_posts", f"/{{account}}/threads?fields={THREAD_FIELDS}", "The account's posts",
              "limit", "after", "since", "until"),
        reads("get_post", f"/{{post_id}}?fields={THREAD_FIELDS}", "One post"),
        reads("list_replies", f"/{{post_id}}/replies?fields={REPLY_FIELDS}", "Replies to a post",
              "limit", "after"),
        reads("get_conversation", f"/{{post_id}}/conversation?fields={REPLY_FIELDS}",
              "A post's whole conversation", "limit", "after"),
        reads("post_insights", "/{post_id}/insights", "A post's numbers", "metric"),
        reads("account_insights", "/{account}/threads_insights", "The account's numbers",
              "metric", "since", "until"),
        reads("publishing_limit", "/{account}/threads_publishing_limit"
              "?fields=quota_usage,config,reply_quota_usage,reply_config",
              "How many posts and replies are left in this 24 hours"),

        # Held: nobody sees a container.
        held("prepare_post", "POST", "/{account}/threads", "Prepare a post or a reply",
             *THREAD_ITEM, only=THREAD_ITEM,
             hint="reply_to_id makes it a reply. Then publish_post, with the id this gives as "
             "creation_id, about 30 seconds later."),
        held("hide_reply", "POST", "/{reply_id}/manage_reply", "Hide a reply, or show it again",
             "hide", only=("hide",), shape=_threads_hidden),

        # The name typed.
        final("publish_post", "POST", "/{account}/threads_publish", "Publish a prepared post",
              PUBLISHES, "creation_id", only=("creation_id",)),
        final("delete_post", "DELETE", "/{post_id}", "Delete a post", DELETES),
    ),
)  # fmt: skip

VENDORS = (BLUESKY, FACEBOOK, INSTAGRAM, THREADS_VENDOR)
