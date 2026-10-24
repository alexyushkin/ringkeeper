from collections.abc import Generator, Iterable, Sequence
from contextlib import contextmanager

import typer
from pydantic import ValidationError
from sqlalchemy import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from interlock.config import get_settings
from interlock.errors import InterlockError
from interlock.schemas import SECRET_REF_ERROR

# Backstop messages for when a database constraint, not a service, catches something.
CONSTRAINT_MESSAGES = {
    "uq_project_instances_one_default_per_type": (
        "a project can have only one default instance per type"
    ),
    "ck_project_instances_prod_not_default": "a prod instance can never be a default",
    "fk_project_instances_instance": "the instance is still bound to a project",
    "ck_instances_secret_ref_pointer": SECRET_REF_ERROR,
    "projects_name_key": "a project with that name already exists",
    "instances_name_key": "an instance with that name already exists",
}


def fail(message: str) -> typer.Exit:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


def format_validation_error(error: ValidationError) -> str:
    # include_input=False: never print the rejected value (it may be a secret).
    lines: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "input"
        lines.append(f"  {location}: {item['msg']}")
    return "invalid input:\n" + "\n".join(lines)


@contextmanager
def handle_errors() -> Generator[None]:
    """Turn domain and database errors into a one-line message and exit code 1."""
    try:
        yield
    except InterlockError as error:
        raise fail(str(error)) from None
    except ValidationError as error:
        raise fail(format_validation_error(error)) from None
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        message = CONSTRAINT_MESSAGES.get(
            str(constraint), f"rejected by the database ({constraint})"
        )
        raise fail(message) from None
    except OperationalError:
        url = make_url(get_settings().database_url).render_as_string(hide_password=True)
        raise fail(
            f"cannot reach the database at {url}. Is it running? "
            "Set INTERLOCK_DATABASE_URL to point elsewhere."
        ) from None


def split_list(values: Iterable[str] | None) -> list[str]:
    """Accept both repeated options and comma-separated values."""
    return [part for value in values or [] for part in value.split(",")]


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(cells, widths, strict=True)).rstrip()

    return "\n".join([line(headers), *(line(row) for row in rows)])


def echo_table(headers: Sequence[str], rows: Sequence[Sequence[str]], empty: str) -> None:
    typer.echo(render_table(headers, rows) if rows else empty)


def join(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "-"
