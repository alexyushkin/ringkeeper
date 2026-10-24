"""The worker's only output channel: one JSON object per line on stdout."""

import dataclasses
import json
import sys
from typing import Any, TextIO, cast

_stream: TextIO = sys.stdout


def set_stream(stream: TextIO) -> None:
    global _stream  # noqa: PLW0603 (tests redirect the one output channel)
    _stream = stream


def to_jsonable(value: object) -> Any:
    """SDK messages are dataclasses (with nested blocks); make them plain JSON."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        data = {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
        data["_kind"] = type(value).__name__
        return data
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in cast(dict[object, object], value).items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in cast(list[object], value)]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def emit(event_type: str, **data: Any) -> None:
    line = json.dumps({"type": event_type, **to_jsonable(data)}, ensure_ascii=False, default=str)
    _stream.write(line + "\n")
    _stream.flush()


def message_type(message: object) -> str:
    """'sdk.result', 'sdk.assistant', ... from the SDK's class names."""
    name = type(message).__name__.removesuffix("Message").removesuffix("Event")
    return "sdk." + "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")
