"""What a cloud command needs, from the command itself.

The AWS, Google Cloud and OCI servers each offer one tool that runs any command of their
CLI, so the tool's name says nothing. These read the command instead, and fail closed: a
command is shown to read, or to be an edit, and anything else needs the name typed.

- Reads run: list, describe, get.
- Edits wait for approval: update, modify, tag, stop.
- Deleting, creating or starting what costs money, anything on IAM, and whatever hands out
  a secret need the name typed. So does a command these rules don't recognize.

A flag that would reach another account, or send the login somewhere else, is refused
outright (`escape`): a link scoped to one client's account has to stay there.
"""

import re
import shlex
from typing import Any, cast

READ, APPROVE, CONFIRM = "read", "approve", "confirm"
READ_VERBS = frozenset({"list", "describe", "get", "show", "ls", "search", "head", "lookup"})
EDIT_VERBS = frozenset({
    "update", "modify", "tag", "untag", "set", "put", "stop", "cp", "mv", "sync", "patch",
    "enable", "disable",
})  # fmt: skip
# Can't be undone, or adds ongoing cost, or changes who may do what.
FINAL_VERBS = frozenset({
    "delete", "terminate", "remove", "destroy", "purge", "drop", "revoke", "detach",
    "deregister", "attach", "create", "run", "launch", "start", "restore", "transfer",
    "rm", "rb", "mb", "reset", "reboot", "restart", "deploy", "add", "assume", "grant",
    "import", "move", "resize", "scale", "replace", "rotate", "cancel", "buy", "purchase",
})  # fmt: skip
SECRET_WORDS = frozenset({
    "secret", "secrets", "password", "passwords", "credential", "credentials", "token",
    "tokens", "kubeconfig",
})  # fmt: skip
ACCESS_SERVICES = frozenset({"iam", "organizations", "identity", "sts", "sso-admin", "kms"})
SHELL = re.compile(r"[;&|<>`$\n]")
ESCAPES: dict[str, tuple[str, ...]] = {
    "aws": ("--profile", "--endpoint-url", "--no-verify-ssl", "--ca-bundle"),
    "gcloud": ("--account", "--impersonate-service-account", "--configuration",
               "--access-token-file", "--billing-project"),
    "oci": ("--profile", "--config-file", "--auth", "--endpoint", "--cli-rc-file",
            "--auth-purpose"),
}  # fmt: skip


def tokens(cli: str, value: Any) -> list[str] | None:
    """The command's words, without the CLI's own name. None: it can't be read."""
    if isinstance(value, list):
        words = [str(v) for v in cast(list[Any], value)]
    elif isinstance(value, str):
        if SHELL.search(value):
            return None
        try:
            words = shlex.split(value)
        except ValueError:
            return None
    else:
        return None
    if any(SHELL.search(w) for w in words):
        return None
    return words[1:] if words[:1] == [cli] else words


def _flag_name(word: str) -> str:
    return word.split("=", 1)[0]


def positional(words: list[str]) -> list[str]:
    """What comes before the first flag: the service and what's done to it."""
    found: list[str] = []
    for word in words:
        if word.startswith("-"):
            break
        found.append(word)
    return found


def flag_value(words: list[str], names: tuple[str, ...]) -> str | None:
    """The value given to one of these flags, in either form, or None."""
    for index, word in enumerate(words):
        if _flag_name(word) in names:
            if "=" in word:
                return word.split("=", 1)[1]
            return words[index + 1] if index + 1 < len(words) else ""
    return None


def escape(cli: str, value: Any) -> str | None:
    """A flag that would leave this login's account, or None."""
    words = tokens(cli, value) or []
    return next((w for w in map(_flag_name, words) if w in ESCAPES.get(cli, ())), None)


def _words(parts: list[str]) -> set[str]:
    return {word for part in parts for word in part.lower().split("-")}


def _reads(cli: str, verbs: list[str]) -> bool:
    if cli == "aws":  # aws <service> <operation>: the operation's first word
        return len(verbs) >= 2 and verbs[1].lower().split("-")[0] in READ_VERBS
    # gcloud and oci: groups, then the verb, then at most one name.
    last = [v.lower() for v in verbs[-2:]]
    return len(verbs) >= 2 and (
        last[-1] in READ_VERBS or (len(last) == 2 and last[0] in READ_VERBS - {"list"})
    )


def level(cli: str, value: Any) -> str:
    """What this command needs."""
    words = tokens(cli, value)
    verbs = positional(words) if words else []
    if words is None or not verbs:
        return CONFIRM
    named = _words(verbs[1:2] if cli == "aws" else verbs)
    secret = "--with-decryption" in words or named & SECRET_WORDS or verbs[0] in ("secrets",)
    if secret or named & FINAL_VERBS or "--delete" in words:
        return CONFIRM
    if _reads(cli, verbs) and (cli == "aws" or not named & EDIT_VERBS):
        return READ
    if verbs[0].lower() in ACCESS_SERVICES:
        return CONFIRM
    return APPROVE if named & EDIT_VERBS else CONFIRM


def what(cli: str, value: Any) -> str:
    """The command's kind, for a trust: `ec2 modify-instance-attribute`, without its names."""
    return " ".join(positional(tokens(cli, value) or [])[:3])
