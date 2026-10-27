"""The master key, and the purpose-specific keys derived from it.

One 32-byte master key protects everything: the file vault's encryption key
and the worker-token signing key are both derived from it with HKDF, each with
its own label. Lose it and both the vault and every outstanding token are gone.

Where it lives: RINGKEEPER_MASTER_KEY if set; otherwise data_dir/master.key
in server mode, or one item in the OS keychain in desktop mode (Windows
Credential Manager, macOS Keychain). Only the key goes in the keychain; the
secrets stay in the encrypted vault file.
"""

import base64
import binascii
import contextlib
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError

KEY_BYTES = 32
KEY_FILE_NAME = "master.key"
KEYCHAIN_ACCOUNT = "master-key"


class MasterKeyError(RingkeeperError):
    pass


def _decode(encoded: str, source: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded.strip().encode())
    except (binascii.Error, ValueError):
        raise MasterKeyError(f"{source} is not valid base64") from None
    if len(key) != KEY_BYTES:
        raise MasterKeyError(f"{source} must decode to {KEY_BYTES} bytes")
    return key


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode()


def key_file_path(settings: Settings) -> Path:
    return settings.data_dir / KEY_FILE_NAME


def key_location(settings: Settings) -> str:
    """Where the master key is, in words (for the CLI)."""
    if settings.master_key is not None:
        return "RINGKEEPER_MASTER_KEY"
    if settings.mode == "desktop":
        return f"the system keychain ({settings.keychain_service} / {KEYCHAIN_ACCOUNT})"
    return str(key_file_path(settings))


def _from_keychain(settings: Settings, *, create: bool) -> bytes:
    import keyring  # noqa: PLC0415 (desktop mode only)
    from keyring.errors import KeyringError  # noqa: PLC0415

    source = "the master key in the system keychain"
    try:
        stored = keyring.get_password(settings.keychain_service, KEYCHAIN_ACCOUNT)
        if stored is None:
            if not create:
                raise MasterKeyError("no master key in the system keychain yet")
            keyring.set_password(settings.keychain_service, KEYCHAIN_ACCOUNT, generate_master_key())
            stored = keyring.get_password(settings.keychain_service, KEYCHAIN_ACCOUNT)
    except KeyringError as error:
        raise MasterKeyError(f"the system keychain isn't available: {error}") from None
    if stored is None:
        raise MasterKeyError("the system keychain didn't keep the master key")
    return _decode(stored, source)


def load_master_key(settings: Settings, *, create: bool = True) -> bytes:
    """RINGKEEPER_MASTER_KEY if set, else the file or keychain item (created on first use)."""
    if settings.master_key is not None:
        return _decode(settings.master_key.get_secret_value(), "RINGKEEPER_MASTER_KEY")
    if settings.mode == "desktop":
        return _from_keychain(settings, create=create)
    path = key_file_path(settings)
    if not path.exists():
        if not create:
            raise MasterKeyError(f"no master key: set RINGKEEPER_MASTER_KEY or create {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written in full to a private temporary file, then linked into place:
        # the key file appears complete or not at all, and when two processes
        # start at once only the first link wins (the other reads that key).
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(generate_master_key() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            with contextlib.suppress(FileExistsError):
                os.link(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return _decode(path.read_text(encoding="ascii"), str(path))


def derive_key(master_key: bytes, purpose: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=None,
        info=f"ringkeeper/{purpose}/v1".encode(),
    ).derive(master_key)


def forget_keychain_key(settings: Settings) -> bool:
    """Removes the desktop master key from the system keychain (when the user
    uninstalls and deletes their data). True if there was one."""
    import keyring  # noqa: PLC0415 (desktop mode only)
    from keyring.errors import KeyringError  # noqa: PLC0415

    try:
        if keyring.get_password(settings.keychain_service, KEYCHAIN_ACCOUNT) is None:
            return False
        keyring.delete_password(settings.keychain_service, KEYCHAIN_ACCOUNT)
    except KeyringError as error:
        raise MasterKeyError(f"the system keychain isn't available: {error}") from None
    return True
