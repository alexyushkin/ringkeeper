"""file:// backend: one AES-256-GCM encrypted JSON file in the data dir.

Each entry is encrypted with a key derived from the master key, with the ref
as associated data: an entry copied under another ref fails to decrypt.
"""

import base64
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from interlock.config import Settings
from interlock.keys import derive_key, load_master_key
from interlock.vault import VaultError

VAULT_FILE_NAME = "vault.json"
NONCE_BYTES = 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FileBackend:
    def __init__(self, path: Path, master_key: bytes) -> None:
        self.path = path
        self._aead = AESGCM(derive_key(master_key, "vault"))

    @classmethod
    def from_settings(cls, settings: Settings) -> "FileBackend":
        return cls(settings.data_dir / VAULT_FILE_NAME, load_master_key(settings))

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise VaultError(f"cannot read vault file {self.path}: {error}") from None
        if not isinstance(data, dict) or data.get("version") != 1:  # pyright: ignore[reportUnknownMemberType]
            raise VaultError(f"{self.path} is not an Interlock vault file")
        return cast(dict[str, dict[str, str]], data.get("entries", {}))  # pyright: ignore[reportUnknownMemberType]

    def get(self, ref: str) -> str:
        entry = self._read().get(ref)
        if entry is None:
            raise VaultError(
                f"secret_ref '{ref}' is not in the vault; "
                f"store it with `interlock secret set {ref}`"
            )
        try:
            plain = self._aead.decrypt(
                base64.b64decode(entry["nonce"]),
                base64.b64decode(entry["ciphertext"]),
                ref.encode(),
            )
        except (InvalidTag, KeyError, ValueError):
            raise VaultError(
                f"cannot decrypt secret_ref '{ref}': "
                "wrong master key, or the vault file was altered"
            ) from None
        return plain.decode()

    def put(self, ref: str, value: str) -> None:
        entries = self._read()
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = self._aead.encrypt(nonce, value.encode(), ref.encode())
        entries[ref] = {"nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}
        self._write({"version": 1, "entries": dict(sorted(entries.items()))})

    def _write(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash never leaves a half-written vault.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".vault-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
