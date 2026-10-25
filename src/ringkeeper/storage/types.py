"""Column types that work on both databases.

On Postgres each one is exactly the type the schema has always used, so the
server schema doesn't change. On SQLite:

- `TextList` and `Doc` are JSON;
- `BigID` is INTEGER, so an autoincrementing key is SQLite's rowid;
- `UTCDateTime` stores UTC without an offset (SQLite has no time zones) and
  hands back aware datetimes, so comparisons behave as on Postgres;
- `UUIDValue` takes a UUID or its text, as psycopg does (SQLite's UUID type
  only takes uuid.UUID).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Dialect, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import TypeDecorator, TypeEngine

TextList: TypeEngine[Any] = JSON().with_variant(ARRAY(Text()), "postgresql")
Doc: TypeEngine[Any] = JSON().with_variant(JSONB(), "postgresql")
BigID: TypeEngine[int] = BigInteger().with_variant(Integer(), "sqlite")


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None or dialect.name != "sqlite":
            return value
        if value.tzinfo is not None:
            value = value.astimezone(UTC)
        return value.replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


class UUIDValue(TypeDecorator[uuid.UUID]):
    impl = Uuid
    cache_ok = True

    def process_bind_param(
        self, value: uuid.UUID | str | None, dialect: Dialect
    ) -> uuid.UUID | None:
        return uuid.UUID(value) if isinstance(value, str) else value
