"""File storage: AWS S3, Cloudflare R2, any S3-compatible service, and Vercel Blob.

The secret is JSON. S3 and anything compatible, signed with AWS Signature Version 4:

    {"provider": "aws", "access_key": "...", "secret_key": "...", "region": "us-east-1"}
    {"provider": "r2", "account_id": "...", "access_key": "...", "secret_key": "..."}
    {"provider": "custom", "endpoint": "https://...", "region": "...", "access_key": ...}

Vercel Blob isn't S3-compatible: one read-write token, one store, no buckets.

    {"provider": "vercel_blob", "token": "vercel_blob_rw_..."}

Listing and reading run. An upload waits for approval; a delete needs the name typed.
Neither can be undone here. A project's link can be pinned to a bucket and a folder.
"""

import base64
import datetime
import hashlib
import hmac
import json
import re
from typing import Annotated, Any, Literal, cast
from urllib.parse import parse_qsl, quote, urlsplit
from xml.etree import ElementTree

import httpx2
from pydantic import Field

from ringkeeper import netpolicy
from ringkeeper.adapters import rest
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, final, held, read
from ringkeeper.models import InstanceType

# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None
PROVIDERS = ("aws", "r2", "custom", "vercel_blob")
BLOB_API = "https://vercel.com/api/blob"
BLOB_VERSION = "12"  # what @vercel/blob 2.6 sends; it has no public REST reference
MAX_READ_BYTES = 2_000_000
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
OK = 300
Bucket = Annotated[
    str | None,
    Field(max_length=63, description="The bucket (not used with Vercel Blob)."),
]
Key = Annotated[str, Field(min_length=1, max_length=1024, description="The file's path.")]
Prefix = Annotated[str | None, Field(max_length=1024, description="Only paths starting so.")]


def settings(value: str) -> JSON:
    """The stored JSON, checked; raises ToolError if it can't be used."""
    try:
        data: Any = json.loads(value)
    except ValueError:
        raise ToolError("this storage connection's secret isn't in the expected form") from None
    fields = cast(JSON, data) if isinstance(data, dict) else {}
    provider = str(fields.get("provider") or "aws")
    if provider not in PROVIDERS:
        raise ToolError(f"unknown storage provider '{provider}'")
    if provider == "vercel_blob":
        if not str(fields.get("token") or "").startswith("vercel_blob_rw_"):
            raise ToolError("a Vercel Blob read-write token starts with vercel_blob_rw_")
        return {**fields, "provider": provider}
    if not fields.get("access_key") or not fields.get("secret_key"):
        raise ToolError("this storage connection needs an access key and its secret")
    region = str(fields.get("region") or "").strip()
    match provider:
        case "aws":
            region = region or "us-east-1"
            endpoint = f"https://s3.{region}.amazonaws.com"
        case "r2":
            account = str(fields.get("account_id") or "").strip()
            if not re.fullmatch(r"[0-9a-fA-F]{32}", account):
                raise ToolError("Cloudflare's account ID is 32 letters and digits")
            region, endpoint = "auto", f"https://{account}.r2.cloudflarestorage.com"
        case _:
            endpoint = str(fields.get("endpoint") or "").strip().rstrip("/")
            if not endpoint.startswith(("https://", "http://")):
                raise ToolError("this storage connection needs its address (https://...)")
            region = region or "us-east-1"
    return {**fields, "provider": provider, "region": region, "endpoint": endpoint}


def secret_fragments(value: str) -> list[str]:
    try:
        fields = settings(value)
    except ToolError:
        return []
    return [str(fields[k]) for k in ("secret_key", "token") if fields.get(k)]


# --- AWS Signature Version 4 ---


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def _encoded(value: str, *, slash: bool = False) -> str:
    return quote(value, safe="-_.~" + ("/" if slash else ""))


def signed_headers(
    fields: JSON, method: str, url: str, query: dict[str, str], digest: str,
    now: datetime.datetime,
) -> dict[str, str]:  # fmt: skip
    """The headers that sign this request (AWS Signature Version 4, service s3)."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    parts = urlsplit(url)
    canonical_query = "&".join(f"{_encoded(k)}={_encoded(v)}" for k, v in sorted(query.items()))
    headers = {"host": parts.netloc, "x-amz-content-sha256": digest, "x-amz-date": stamp}
    names = ";".join(sorted(headers))
    lines = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    request = "\n".join((method, parts.path or "/", canonical_query, lines, names, digest))
    region = str(fields["region"])
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest())
    )
    key = _hmac(f"AWS4{fields['secret_key']}".encode(), day)
    for word in (region, "s3", "aws4_request"):
        key = _hmac(key, word)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "x-amz-content-sha256": digest,
        "x-amz-date": stamp,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={fields['access_key']}/{scope}, "
            f"SignedHeaders={names}, Signature={signature}"
        ),
    }


def sign_v4(
    access_key: str, secret_key: str, region: str, service: str, method: str, url: str,
    body: bytes, now: datetime.datetime, session_token: str | None = None,
) -> dict[str, str]:  # fmt: skip
    """The headers that sign a request to any other AWS service: its path is encoded once more
    for the signature, as every service but S3 requires."""
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    parts = urlsplit(url)
    digest = hashlib.sha256(body).hexdigest()
    headers = {"host": parts.netloc, "x-amz-date": stamp}
    if session_token:
        headers["x-amz-security-token"] = session_token
    names = ";".join(sorted(headers))
    lines = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    path = quote(parts.path or "/", safe="/-_.~")
    canonical_query = "&".join(
        sorted(f"{_encoded(k)}={_encoded(v)}" for k, v in parse_qsl(parts.query, True))
    )
    request = "\n".join((method, path, canonical_query, lines, names, digest))
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode()).hexdigest())
    )
    key = _hmac(f"AWS4{secret_key}".encode(), day)
    for word in (region, service, "aws4_request"):
        key = _hmac(key, word)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {k: v for k, v in headers.items() if k != "host"} | {
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={names}, Signature={signature}"
        ),
    }


def _bucket(fields: JSON, bucket: str | None) -> str:
    name = (bucket or str(fields.get("bucket") or "")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", name):
        raise ToolError("name a bucket (bucket=...); list_buckets shows them")
    return name


async def _s3(
    fields: JSON, method: str, path: str, query: dict[str, str] | None = None,
    body: bytes = b"", extra: dict[str, str] | None = None,
) -> httpx2.Response:  # fmt: skip
    url = str(fields["endpoint"]) + _encoded(path, slash=True)
    netpolicy.check_url(str(fields["endpoint"]))
    now = datetime.datetime.now(datetime.UTC)
    digest = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA256
    headers = signed_headers(fields, method, url, query or {}, digest, now) | (extra or {})
    client = httpx2.AsyncClient(timeout=rest.TIMEOUT, transport=transport)
    try:
        async with client:
            response = await client.request(
                method, url, params=query or None, headers=headers, content=body or None
            )
    except httpx2.TimeoutException:
        raise ToolError("the storage service timed out") from None
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach the storage service: {type(error).__name__}") from None
    if response.status_code >= OK:
        code = re.search(r"<Message>([^<]+)</Message>", response.text)
        detail = code[1] if code else response.reason_phrase
        raise ToolError(f"storage returned {response.status_code}: {detail}")
    return response


def _xml(text: str) -> ElementTree.Element:
    # Tag names without their namespace, which differs between services.
    root = ElementTree.fromstring(text)  # noqa: S314 (see the import)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


# --- Vercel Blob ---


async def _blob(
    fields: JSON, method: str, path: str, *, params: dict[str, str] | None = None,
    content: bytes | None = None, json_body: JSON | None = None,
    headers: dict[str, str] | None = None,
) -> JSON:  # fmt: skip
    sent = {"authorization": f"Bearer {fields['token']}", "x-api-version": BLOB_VERSION}
    client = httpx2.AsyncClient(timeout=rest.TIMEOUT, transport=transport)
    try:
        async with client:
            response = await client.request(
                method, BLOB_API + path, params=params, content=content, json=json_body,
                headers=sent | (headers or {}),
            )  # fmt: skip
    except httpx2.HTTPError as error:
        raise ToolError(f"cannot reach Vercel Blob: {type(error).__name__}") from None
    if response.status_code >= OK:
        raise ToolError(
            f"Vercel Blob returned {response.status_code}: {rest.error_message(response)}"
        )
    return cast(JSON, response.json()) if response.content else {}


def _blob_file(blob: JSON) -> JSON:
    return {"key": blob.get("pathname"), "size": blob.get("size"),
            "modified": blob.get("uploadedAt"), "url": blob.get("url")}  # fmt: skip


async def _blob_read(fields: JSON, key: str) -> httpx2.Response:
    found = await _blob(fields, "GET", "", params={"prefix": key, "limit": "1"})
    blobs = cast(list[JSON], found.get("blobs") or [])
    if not blobs or blobs[0].get("pathname") != key:
        raise ToolError(f"no file at {key}")
    client = httpx2.AsyncClient(timeout=rest.TIMEOUT, transport=transport)
    async with client:
        return await client.get(str(blobs[0].get("url")))


# --- Tools ---


def _is_blob(fields: JSON) -> bool:
    return fields["provider"] == "vercel_blob"


async def probe(target: Target) -> str:
    fields = settings(target.secret.reveal())
    if _is_blob(fields):
        await _blob(fields, "GET", "", params={"limit": "1"})
        return "Connected to Vercel Blob."
    if fields.get("bucket"):
        await _s3(fields, "GET", f"/{fields['bucket']}", {"list-type": "2", "max-keys": "1"})
        return f"Connected: read the bucket {fields['bucket']}."
    found = await list_buckets.handler(target)
    count = len(found["buckets"])
    return f"Connected: {count} bucket{'' if count == 1 else 's'}."


@read
async def list_buckets(target: Target) -> JSON:
    """The account's buckets. A key limited to one bucket may not be allowed to ask."""
    fields = settings(target.secret.reveal())
    if _is_blob(fields):
        return {"buckets": [], "note": "Vercel Blob has one store and no buckets."}
    root = _xml((await _s3(fields, "GET", "/")).text)
    return {"buckets": [e.text for e in root.iter("Name") if e.text]}


@read
async def list_files(
    target: Target,
    bucket: Bucket = None,
    prefix: Prefix = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
    after: Annotated[str | None, Field(description="next_after from the last list.")] = None,
) -> JSON:
    """Files under a prefix: path, size and when each changed."""
    fields = settings(target.secret.reveal())
    if _is_blob(fields):
        params = {"limit": str(limit)} | ({"prefix": prefix} if prefix else {})
        found = await _blob(fields, "GET", "", params=params | ({"cursor": after} if after else {}))
        blobs = cast(list[JSON], found.get("blobs") or [])
        return {"files": [_blob_file(b) for b in blobs], "next_after": found.get("cursor")}
    query = {"list-type": "2", "max-keys": str(limit)}
    query |= ({"prefix": prefix} if prefix else {}) | (
        {"continuation-token": after} if after else {}
    )
    root = _xml((await _s3(fields, "GET", f"/{_bucket(fields, bucket)}", query)).text)
    files = [
        {"key": item.findtext("Key"), "size": int(item.findtext("Size") or 0),
         "modified": item.findtext("LastModified")}
        for item in root.iter("Contents")
    ]  # fmt: skip
    return {"files": files, "next_after": root.findtext("NextContinuationToken")}


def _content(response: httpx2.Response, key: str) -> JSON:
    data = response.content
    kind = response.headers.get("content-type", "")
    if len(data) > MAX_READ_BYTES:
        return {"key": key, "size": len(data), "content_type": kind, "too_big_to_show": True}
    try:
        return {"key": key, "size": len(data), "content_type": kind, "text": data.decode()}
    except UnicodeDecodeError:
        encoded = base64.b64encode(data).decode()
        return {"key": key, "size": len(data), "content_type": kind, "base64": encoded}


@read
async def read_file(target: Target, key: Key, bucket: Bucket = None) -> JSON:
    """Read a file: its text, or base64 if it isn't text. Up to 2 MB."""
    fields = settings(target.secret.reveal())
    if _is_blob(fields):
        return _content(await _blob_read(fields, key), key)
    return _content(await _s3(fields, "GET", f"/{_bucket(fields, bucket)}/{key}"), key)


@held
async def upload_file(
    target: Target,
    key: Key,
    content: Annotated[str, Field(max_length=5_000_000, description="The file's text or base64.")],
    encoding: Literal["text", "base64"] = "text",
    content_type: Annotated[str, Field(max_length=100)] = "text/plain",
    bucket: Bucket = None,
) -> JSON:
    """Upload a file, replacing what's at that path. Always waits for the user's approval."""
    fields = settings(target.secret.reveal())
    try:
        data = (
            base64.b64decode(content, validate=True) if encoding == "base64" else content.encode()
        )
    except ValueError:
        raise ToolError("that isn't valid base64") from None
    if _is_blob(fields):
        options = {"x-content-type": content_type, "x-add-random-suffix": "0",
                   "x-allow-overwrite": "1"}  # fmt: skip
        found = await _blob(fields, "PUT", "/", params={"pathname": key}, content=data,
                            headers=options)  # fmt: skip
        return {"uploaded": key, "size": len(data), "url": found.get("url")}
    await _s3(fields, "PUT", f"/{_bucket(fields, bucket)}/{key}", body=data,
              extra={"Content-Type": content_type})  # fmt: skip
    return {"uploaded": key, "size": len(data)}


@final("deletes a file")
async def delete_file(target: Target, key: Key, bucket: Bucket = None) -> JSON:
    """Delete a file. Waits for approval, with the connection's name typed."""
    fields = settings(target.secret.reveal())
    if _is_blob(fields):
        found = await _blob(fields, "GET", "", params={"prefix": key, "limit": "1"})
        blobs = cast(list[JSON], found.get("blobs") or [])
        if not blobs or blobs[0].get("pathname") != key:
            raise ToolError(f"no file at {key}")
        await _blob(fields, "POST", "/delete", json_body={"urls": [blobs[0].get("url")]})
        return {"deleted": key}
    await _s3(fields, "DELETE", f"/{_bucket(fields, bucket)}/{key}")
    return {"deleted": key}


ADAPTER = Adapter(
    service=InstanceType.S3,
    tools=(list_buckets, list_files, read_file, upload_file, delete_file),
    secret_fragments=secret_fragments,
)
