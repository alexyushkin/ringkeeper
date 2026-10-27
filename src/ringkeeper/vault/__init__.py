"""Resolve secret_ref pointers to secret values.

One interface, `Vault.resolve(ref) -> Secret`, with a backend per scheme:
`file://` (an encrypted local file) and `aws-sm://` (AWS Secrets Manager).
A `Secret` never prints its value; call `reveal()` at the point of use.
"""

from collections.abc import Callable, Iterable
from typing import Protocol

from ringkeeper import scrub
from ringkeeper.config import Settings, get_settings
from ringkeeper.errors import RingkeeperError
from ringkeeper.schemas import validate_secret_ref


class VaultError(RingkeeperError):
    pass


class Secret:
    """A resolved secret value. Its repr and str never contain the value."""

    __slots__ = ("_value", "ref")

    def __init__(self, ref: str, value: str, extra_fragments: Iterable[str] = ()) -> None:
        self.ref = ref
        self._value = value
        scrub.register(value, *extra_fragments)

    def reveal(self) -> str:
        return self._value

    def also_scrub(self, *fragments: str) -> None:
        scrub.register(*fragments)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return f"Secret({self.ref!r})"

    __str__ = __repr__


class Backend(Protocol):
    def get(self, ref: str) -> str: ...
    def put(self, ref: str, value: str) -> None: ...
    def delete(self, ref: str) -> None: ...


def scheme_of(ref: str) -> str:
    return ref.split("://", 1)[0]


class Vault:
    def __init__(self, backends: dict[str, Callable[[], Backend]]) -> None:
        # Factories, so aws-sm doesn't need credentials unless it is used.
        self._factories = backends
        self._backends: dict[str, Backend] = {}

    def _backend(self, ref: str) -> Backend:
        try:
            validate_secret_ref(ref)
        except ValueError as error:
            raise VaultError(str(error)) from None
        scheme = scheme_of(ref)
        if scheme not in self._backends:
            self._backends[scheme] = self._factories[scheme]()
        return self._backends[scheme]

    def resolve(self, ref: str, extra_fragments: Iterable[str] = ()) -> Secret:
        return Secret(ref, self._backend(ref).get(ref), extra_fragments)

    def delete(self, ref: str) -> None:
        """Remove a secret (a removed connection's). Missing is fine."""
        self._backend(ref).delete(ref)

    def store(self, ref: str, value: str) -> None:
        if not value:
            raise VaultError("refusing to store an empty secret")
        scrub.register(value)
        self._backend(ref).put(ref, value)


def _aws_backend() -> Backend:
    """AWS Secrets Manager, for servers: boto3 is the `aws` extra (the server
    image has it; the desktop app doesn't)."""
    try:
        from ringkeeper.vault.aws import AwsSecretsManagerBackend  # noqa: PLC0415 (boto3 is slow)
    except ImportError:
        raise VaultError(
            "aws-sm:// secrets need the AWS extra: install ringkeeper[aws] "
            "(the server image includes it)."
        ) from None
    return AwsSecretsManagerBackend()


def get_vault(settings: Settings | None = None) -> Vault:
    from ringkeeper.vault.file import FileBackend  # noqa: PLC0415

    settings = settings or get_settings()
    return Vault(
        {
            "file": lambda: FileBackend.from_settings(settings),
            "aws-sm": _aws_backend,
        }
    )
