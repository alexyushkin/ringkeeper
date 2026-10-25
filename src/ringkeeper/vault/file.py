"""file:// backend: one AES-256-GCM encrypted JSON file in the data dir.

Each entry is encrypted with a key derived from the master key, with the ref
as associated data: an entry copied under another ref fails to decrypt.
"""

import base64
import json
import os
import secrets
import sys
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ringkeeper.config import Settings
from ringkeeper.keys import derive_key, load_master_key
from ringkeeper.vault import VaultError

VAULT_FILE_NAME = "vault.json"
NONCE_BYTES = 12


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class FileBackend:
    def __init__(self, path: Path, master_key: bytes, owner_uid: int | None = None) -> None:
        self.path = path
        self._aead = AESGCM(derive_key(master_key, "vault"))
        # The api writes as root; the gateway reads as its own user (RINGKEEPER_DATA_UID).
        self._owner_uid = owner_uid

    @classmethod
    def from_settings(cls, settings: Settings) -> "FileBackend":
        return cls(
            settings.data_dir / VAULT_FILE_NAME, load_master_key(settings), settings.data_uid
        )

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            data: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise VaultError(f"cannot read vault file {self.path}: {error}") from None
        if not isinstance(data, dict) or data.get("version") != 1:  # pyright: ignore[reportUnknownMemberType]
            raise VaultError(f"{self.path} is not an Ringkeeper vault file")
        return cast(dict[str, dict[str, str]], data.get("entries", {}))  # pyright: ignore[reportUnknownMemberType]

    def get(self, ref: str) -> str:
        entry = self._read().get(ref)
        if entry is None:
            raise VaultError(
                f"secret_ref '{ref}' is not in the vault; "
                f"store it with `ringkeeper secret set {ref}`"
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
        with self._locked():
            entries = self._read()
            nonce = secrets.token_bytes(NONCE_BYTES)
            ciphertext = self._aead.encrypt(nonce, value.encode(), ref.encode())
            entries[ref] = {"nonce": _b64(nonce), "ciphertext": _b64(ciphertext)}
            self._write({"version": 1, "entries": dict(sorted(entries.items()))})

    def delete(self, ref: str) -> None:
        with self._locked():
            entries = self._read()
            if entries.pop(ref, None) is not None:
                self._write({"version": 1, "entries": dict(sorted(entries.items()))})

    @contextmanager
    def _locked(self) -> Generator[None]:
        """One writer at a time (api threads, the CLI): read-modify-write never loses an entry."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415

                for _ in range(200):  # up to ~10 s
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise VaultError("the vault is busy; try again")
                try:
                    yield
                finally:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _write(self, data: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash never leaves a half-written vault.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".vault-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())  # on disk before it replaces the old vault
            os.chmod(tmp, 0o600)
            if self._owner_uid is not None and sys.platform != "win32":
                os.chown(tmp, self._owner_uid, -1)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
