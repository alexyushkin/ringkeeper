"""Keys in a project's own files, offered as connections.

When a project is added (or on demand) its folder's .env files and config files are
read for known keys: DATABASE_URL, AIRTABLE_*, N8N_*, VERCEL_TOKEN, TWILIO_*,
GITHUB_TOKEN, SMTP_*, IMAP_*, RESEND_*, FOF_*, DATAFORSEO_*, R2_*, AWS_* and the like. Each
is offered as a connection for that client, masked, and connected only when you confirm
that one. Nothing is imported by itself. A key for a service Ringkeeper has no connection
for yet (STRIPE_*, HUBSPOT_*...) is listed, and can't be connected.

Only the named files in the project's folder and its config/ folder are read, never
through a link that leaves the folder. A value never leaves the server: what you see
is masked, and confirming names a finding, whose value is read again from the file.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from ringkeeper.adapters.vendors import ALL

MAX_BYTES = 256_000
ENV_FILES = (".env", ".env.local", ".env.development", ".env.development.local",
             ".env.production", ".env.production.local", ".env.staging")  # fmt: skip
CONFIG_FILES = ("config.json", "config.local.json", "settings.json", "secrets.json",
                "config.yaml", "config.yml", "config.toml")  # fmt: skip
_LINE = re.compile(
    r"""^\s*(?:export\s+)?["']?([A-Za-z][A-Za-z0-9_]*)["']?\s*[=:]\s*(.*?)\s*,?\s*$"""
)
FORMS_ON_FIRE = "https://secure.formsonfire.com/api/v2"
DATAFORSEO = "https://api.dataforseo.com/v3"
NOT_YET = (
    ("Stripe", ("STRIPE_",)), ("PayPal", ("PAYPAL_",)),
    ("HubSpot", ("HUBSPOT_",)), ("QuickBooks", ("QUICKBOOKS_", "QBO_", "INTUIT_")),
    ("Xero", ("XERO_",)), ("WordPress", ("WP_", "WORDPRESS_")),
    ("Leadfeeder", ("LEADFEEDER_",)),
)  # fmt: skip
PLACEHOLDER = re.compile(r"^(<.*>|\$\{.*\}|your[-_ ].*|xxx+|changeme|todo|example.*)$", re.I)


@dataclass(frozen=True)
class Finding:
    id: str
    file: str  # relative to the project's folder
    service: str  # a connection type
    label: str
    keys: list[str]
    masked: str
    title: str
    usable: bool = True
    why: str = ""  # why it can't be connected as it is
    warning: str = ""  # connectable, with something to know first
    env: str = ""  # the key itself says what it reaches: "prod", or "dev" for test data


def mask(value: str) -> str:
    """Enough to recognize it, nothing to use: a database's host, a key's ends."""
    if "://" in value:
        host = urlsplit(value).hostname or "?"
        return f"{value.split('://', 1)[0]}://…@{host}"
    return f"{value[:4]}…{value[-2:]}" if len(value) > 10 else "…"


def inside(folder: Path, path: Path) -> bool:
    try:
        return path.is_file() and path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def files_in(folder: Path) -> list[Path]:
    names = [folder / n for n in (*ENV_FILES, *CONFIG_FILES)]
    names += [folder / "config" / n for n in (*ENV_FILES, *CONFIG_FILES)]
    return [p for p in names if inside(folder, p) and p.stat().st_size <= MAX_BYTES]


def _flatten(data: object, found: dict[str, str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():  # pyright: ignore[reportUnknownVariableType]
            if isinstance(value, str):
                found.setdefault(str(key), value)  # pyright: ignore[reportUnknownArgumentType]
            else:
                _flatten(value, found)  # pyright: ignore[reportUnknownArgumentType]


def values_in(path: Path) -> dict[str, str]:
    """KEY -> value, from an env file or a config file's plain keys."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    found: dict[str, str] = {}
    if path.suffix == ".json":
        try:
            _flatten(json.loads(text), found)
        except ValueError:
            return {}
    else:
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = _LINE.match(line)
            if match:
                found.setdefault(match[1], match[2].strip("\"'"))
    found = {k.upper(): v for k, v in found.items()}
    return {k: v for k, v in found.items() if v and not PLACEHOLDER.match(v)}


def finding_id(file: str, service: str, keys: list[str]) -> str:
    return hashlib.sha256(f"{file}|{service}|{','.join(keys)}".encode()).hexdigest()[:16]


def _first(values: dict[str, str], *names: str) -> tuple[str, str] | None:
    return next(((n, values[n]) for n in names if n in values), None)


class Add(Protocol):
    def __call__(
        self, service: str, label: str, keys: list[str], secret: str, *, title: str,
        address: str | None = None, shown: str | None = None, why: str = "", env: str = "",
    ) -> None: ...  # fmt: skip


def _named(*found: tuple[str, str] | None) -> list[str]:
    return [item[0] for item in found if item]


def _sender(values: dict[str, str], *more: str) -> tuple[str, str] | None:
    return _first(values, *more, "SMTP_FROM", "MAIL_FROM", "MAIL_FROM_ADDRESS", "EMAIL_FROM",
                  "FROM_EMAIL", "DEFAULT_FROM_EMAIL", "SENDER_EMAIL")  # fmt: skip


def _email(values: dict[str, str], add: Add) -> None:
    host = _first(values, "SMTP_HOST", "SMTP_SERVER", "MAIL_HOST", "EMAIL_HOST")
    user = _first(values, "SMTP_USER", "SMTP_USERNAME", "MAIL_USERNAME", "EMAIL_HOST_USER")
    password = _first(values, "SMTP_PASS", "SMTP_PASSWORD", "MAIL_PASSWORD",
                      "EMAIL_HOST_PASSWORD")  # fmt: skip
    port = _first(values, "SMTP_PORT", "MAIL_PORT", "EMAIL_PORT")
    if host and password:
        sender = _sender(values)
        address = sender[1] if sender else user[1] if user and "@" in user[1] else ""
        fields = {"host": host[1], "port": port[1] if port else "",
                  "security": "ssl" if port and port[1] == "465" else "starttls",
                  "username": user[1] if user else "", "password": password[1],
                  "from_address": address}  # fmt: skip
        add(
            "smtp", "Email sending", _named(host, user, password, port, sender),
            json.dumps(fields), title=f"Email · {address or host[1]}", shown=host[1],
            why="" if address else "It needs a From address (SMTP_FROM).",
        )  # fmt: skip
    imap = _first(values, "IMAP_HOST", "IMAP_SERVER")
    imap_user = _first(values, "IMAP_USER", "IMAP_USERNAME", "IMAP_EMAIL")
    imap_password = _first(values, "IMAP_PASS", "IMAP_PASSWORD")
    if imap and imap_user and imap_password:
        sending = host[1] if host else imap[1].replace("imap.", "smtp.", 1)
        fields = {"preset": "custom", "address": imap_user[1], "password": imap_password[1],
                  "imap_host": imap[1], "smtp_host": sending,
                  "smtp_port": port[1] if port else "",
                  "smtp_security": "starttls" if port and port[1] == "587" else "ssl"}  # fmt: skip
        add("mailbox", "Mailbox", _named(imap, imap_user, imap_password), json.dumps(fields),
            title=f"Mailbox · {imap_user[1]}", shown=imap_user[1])  # fmt: skip
    resend = _first(values, "RESEND_API_KEY", "RESEND_KEY")
    if resend:
        sender = _sender(values, "RESEND_FROM", "RESEND_FROM_EMAIL")
        fields = {"api_key": resend[1], "from_address": sender[1] if sender else ""}
        add(
            "resend", "Resend", _named(resend, sender), json.dumps(fields), title="Resend",
            shown=mask(resend[1]), why="" if sender else "It needs a From address (RESEND_FROM).",
        )  # fmt: skip


def _apis(values: dict[str, str], add: Add) -> None:
    forms = _first(values, "FOF_API_KEY", "FOF_KEY", "FOF_TOKEN", "FORMSONFIRE_API_KEY",
                   "APPENATE_API_KEY")  # fmt: skip
    if forms:
        address = _first(values, "FOF_BASE_URL", "FOF_URL", "FOF_API_URL", "APPENATE_URL")
        add("http", "Forms On Fire", _named(forms, address),
            json.dumps({"auth": "bearer", "token": forms[1]}), title="Forms On Fire",
            address=address[1] if address else FORMS_ON_FIRE, shown=mask(forms[1]))  # fmt: skip
    login = _first(values, "DATAFORSEO_LOGIN", "DATAFORSEO_USERNAME", "DATAFORSEO_USER")
    password = _first(values, "DATAFORSEO_PASSWORD", "DATAFORSEO_PASS", "DATAFORSEO_API_KEY")
    if login and password:
        fields = {"auth": "basic", "username": login[1], "password": password[1]}
        add("http", "DataForSEO", _named(login, password), json.dumps(fields), title="DataForSEO",
            address=DATAFORSEO, shown=login[1])  # fmt: skip


def _storage(values: dict[str, str], add: Add) -> None:
    key = _first(values, "R2_ACCESS_KEY_ID", "R2_ACCESS_KEY", "CLOUDFLARE_R2_ACCESS_KEY_ID")
    secret = _first(
        values, "R2_SECRET_ACCESS_KEY", "R2_SECRET_KEY", "CLOUDFLARE_R2_SECRET_ACCESS_KEY"
    )
    if key and secret:
        account = _first(values, "R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "CF_ACCOUNT_ID")
        bucket = _first(values, "R2_BUCKET", "R2_BUCKET_NAME")
        fields = {
            "provider": "r2",
            "account_id": account[1] if account else "",
            "access_key": key[1],
            "secret_key": secret[1],
            "bucket": bucket[1] if bucket else "",
        }
        add(
            "s3",
            "Cloudflare R2",
            _named(key, secret, account, bucket),
            json.dumps(fields),
            title=f"Files · {bucket[1]}" if bucket else "Files · R2",
            shown=mask(key[1]),
            why="" if account else "It needs the account ID too (R2_ACCOUNT_ID).",
        )
    key = _first(values, "AWS_ACCESS_KEY_ID")
    secret = _first(values, "AWS_SECRET_ACCESS_KEY")
    if key and secret:
        region = _first(values, "AWS_REGION", "AWS_DEFAULT_REGION", "S3_REGION")
        bucket = _first(
            values, "S3_BUCKET", "AWS_S3_BUCKET", "AWS_BUCKET", "AWS_BUCKET_NAME", "S3_BUCKET_NAME"
        )
        fields = {
            "provider": "aws",
            "access_key": key[1],
            "secret_key": secret[1],
            "region": region[1] if region else "",
            "bucket": bucket[1] if bucket else "",
        }
        add(
            "s3",
            "AWS S3",
            _named(key, secret, region, bucket),
            json.dumps(fields),
            title=f"Files · {bucket[1]}" if bucket else "Files · AWS",
            shown=mask(key[1]),
        )
    blob = _first(values, "BLOB_READ_WRITE_TOKEN")
    if blob:
        add(
            "s3",
            "Vercel Blob",
            [blob[0]],
            json.dumps({"provider": "vercel_blob", "token": blob[1]}),
            title="Files · Vercel Blob",
            shown=mask(blob[1]),
        )


def _vendors(values: dict[str, str], add: Add) -> None:
    """Business systems: the names their own docs give their keys (Vendor.env_vars)."""
    for vendor in ALL:
        keys = sorted(k for k in vendor.env_vars if k in values)
        if not keys or vendor.provider:
            continue
        fields: dict[str, Any] = {"vendor": vendor.key}
        fields |= {vendor.env_vars[k]: values[k] for k in keys}
        says = vendor.env_of(fields) if vendor.env_of else None
        missing = [s.label for s in vendor.secrets if s.required and not fields.get(s.key)]
        add(
            "vendor", vendor.name, keys, json.dumps(fields), title=vendor.name,
            shown=mask(values[keys[0]]), env=says.value if says else "",
            why=f"It needs the {' and '.join(missing)} too." if missing else "",
        )  # fmt: skip


def _oracle(values: dict[str, str], add: Add) -> None:
    dsn = _first(values, "ORACLE_DSN", "ORACLE_CONNECTION_STRING", "ORACLE_CONNECT_STRING")
    user = _first(values, "ORACLE_USER", "ORACLE_USERNAME")
    password = _first(values, "ORACLE_PASSWORD", "ORACLE_PASS")
    if dsn:
        fields = {"dsn": dsn[1], "user": user[1] if user else "",
                  "password": password[1] if password else ""}  # fmt: skip
        add(
            "oracle", "Oracle Database", _named(dsn, user, password), json.dumps(fields),
            title="Oracle Database", shown=mask(dsn[1]),
            why="" if user and password else "It needs the user and password too (ORACLE_USER, "
            "ORACLE_PASSWORD).",
        )  # fmt: skip


def _not_yet(values: dict[str, str], add: Add) -> None:
    """Services with no connection yet: named, so you know it was seen, and left alone."""
    for label, words in NOT_YET:
        keys = sorted(k for k in values if k.startswith(words))
        if keys:
            add(
                "http", label, keys, values[keys[0]], title=label, shown=mask(values[keys[0]]),
                why=f"Connect {label} from its tile under Connectors: it signs in there, so "
                "this stays in the file.",
            )  # fmt: skip


def _in_file(file: str, values: dict[str, str]) -> list[tuple[Finding, str, str | None]]:
    """What this file's keys can connect: (finding, secret, address)."""
    found: list[tuple[Finding, str, str | None]] = []

    def add(
        service: str, label: str, keys: list[str], secret: str, *, title: str,
        address: str | None = None, shown: str | None = None, why: str = "", env: str = "",
    ) -> None:  # fmt: skip
        identity = finding_id(file, service, keys)
        finding = Finding(
            identity, file, service, label, keys, shown or mask(secret), title,
            usable=not why, why=why, env=env,
        )  # fmt: skip
        found.append((finding, secret, address))

    database = _first(
        values, "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "SUPABASE_DB_URL",
        "SUPABASE_DATABASE_URL", "NEON_DATABASE_URL",
    )  # fmt: skip
    if database and database[1].startswith(("postgres://", "postgresql://")):
        host = urlsplit(database[1]).hostname or "database"
        add("supabase", "Database", [database[0]], database[1], title=f"Database · {host}")
    elif "SUPABASE_URL" in values:
        keys = [k for k in values if k.startswith("SUPABASE_")]
        add(
            "supabase", "Supabase", keys, values["SUPABASE_URL"], title="Database",
            why="Its API keys don't reach the database. Add its connection string, or sign in "
            "with the Supabase tile.",
        )  # fmt: skip
    airtable = _first(values, "AIRTABLE_TOKEN", "AIRTABLE_PAT", "AIRTABLE_API_KEY", "AIRTABLE_KEY")
    if airtable:
        add("airtable", "Airtable", [airtable[0]], airtable[1], title="Airtable")
    n8n_key = _first(values, "N8N_API_KEY", "N8N_KEY")
    n8n_url = _first(values, "N8N_BASE_URL", "N8N_URL", "N8N_HOST", "N8N_API_URL")
    if n8n_key:
        host = urlsplit(n8n_url[1]).hostname if n8n_url and "://" in n8n_url[1] else None
        keys = [n8n_key[0], *([n8n_url[0]] if n8n_url else [])]
        add(
            "n8n", "n8n", keys, n8n_key[1], title=f"n8n · {host}" if host else "n8n",
            address=n8n_url[1] if n8n_url else None,
            why="" if n8n_url else "It needs the n8n address too (N8N_BASE_URL).",
        )  # fmt: skip
    vercel = _first(values, "VERCEL_TOKEN", "VERCEL_API_TOKEN")
    if vercel:
        add("vercel", "Vercel", [vercel[0]], vercel[1], title="Vercel")
    sid = values.get("TWILIO_ACCOUNT_SID")
    if sid:
        key = _first(values, "TWILIO_API_KEY", "TWILIO_API_KEY_SID", "TWILIO_KEY_SID")
        secret = _first(values, "TWILIO_API_SECRET", "TWILIO_API_KEY_SECRET", "TWILIO_KEY_SECRET")
        keys = ["TWILIO_ACCOUNT_SID", *([key[0]] if key else []), *([secret[0]] if secret else [])]
        pair = json.dumps({"account_sid": sid, "key_sid": key[1] if key else "",
                           "key_secret": secret[1] if secret else ""})  # fmt: skip
        add(
            "twilio", "Twilio", keys, pair, title="Twilio", shown=mask(sid),
            why="" if key and secret else "It needs an API key and its secret, not the auth token.",
        )  # fmt: skip
    github = _first(values, "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT")
    if github:
        add("github", "GitHub", [github[0]], github[1], title="GitHub")
    _email(values, add)
    _apis(values, add)
    _storage(values, add)
    _vendors(values, add)
    _oracle(values, add)
    _not_yet(values, add)
    return found


def scan(folder: Path) -> list[tuple[Finding, str, str | None]]:
    """Every finding in the project's files, with what it would store. The same key
    in two files shows once, from the first."""
    found: list[tuple[Finding, str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for path in files_in(folder):
        file = path.relative_to(folder).as_posix()
        for finding, secret, address in _in_file(file, values_in(path)):
            mark = (finding.service, secret)
            if mark not in seen:
                seen.add(mark)
                found.append((finding, secret, address))
    return found
