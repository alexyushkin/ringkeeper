"""The master key, and the purpose-specific keys derived from it.

One 32-byte master key protects everything: the file vault's encryption key
and the worker-token signing key are both derived from it with HKDF, each with
its own label. Lose it and both the vault and every outstanding token are gone.
"""

import base64
import binascii
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError

KEY_BYTES = 32
KEY_FILE_NAME = "master.key"


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


def load_master_key(settings: Settings, *, create: bool = True) -> bytes:
    """RINGKEEPER_MASTER_KEY if set, else data_dir/master.key (created on first use)."""
    if settings.master_key is not None:
        return _decode(settings.master_key.get_secret_value(), "RINGKEEPER_MASTER_KEY")
    path = key_file_path(settings)
    if not path.exists():
        if not create:
            raise MasterKeyError(f"no master key: set RINGKEEPER_MASTER_KEY or create {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # O_EXCL: two processes starting at once must not both write a key.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(generate_master_key() + "\n")
    return _decode(path.read_text(encoding="ascii"), str(path))


def derive_key(master_key: bytes, purpose: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=None,
        info=f"ringkeeper/{purpose}/v1".encode(),
    ).derive(master_key)
