"""What people read: a long table from paged results stays readable in a chat, and two
connections never share a name without a way to tell them apart."""

from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType
from ringkeeper.services import instances
from ringkeeper.wording import plain
from tests.helpers import make_instance, make_project


def three_pages() -> str:
    """62 rows as Claude writes them from three pages of a query: a blank line per page."""
    pages = [range(1, 26), range(26, 51), range(51, 63)]
    rows = ["\n".join(f"| {n} | Customer {n} | ${n * 10} |" for n in page) for page in pages]
    return "\n".join(["| Order | Customer | Total |", "|---|---|---|", "\n\n".join(rows)])


def test_a_paged_table_reads_as_one_line_per_row_to_the_last() -> None:
    lines = [line for line in plain(three_pages()).splitlines() if line.strip()]
    assert len(lines) == 63  # the header and 62 rows
    assert lines[0] == "Order · Customer · Total"
    assert lines[-1] == "62 · Customer 62 · $620"
    assert not any("|" in line or "---" in line for line in lines)


def test_google_accounts_are_named_by_client(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC")
        assert instances.google_title(
            session, "general-informatics-llc", "a@x.example", shared=False
        ) == ("Google · General Informatics LLC · a@x.example")
        assert instances.google_title(session, "you", "a@x.example", shared=True) == (
            "Google · a@x.example"
        )


def test_a_duplicate_name_gets_a_better_one(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC")
        make_project(session, "acme-web", owner="Acme")
        same = "Google · a.y@example.com"
        first = make_instance(
            session, "gi-google", type=InstanceType.GOOGLE, owner="General Informatics LLC",
            title=same, config={"email": "a.y@example.com"},
        )  # fmt: skip
        second = make_instance(
            session, "acme-google", type=InstanceType.GOOGLE, owner="Acme", title=same,
            config={"email": "a.y@example.com"},
        )  # fmt: skip
        alone = make_instance(session, "acme-db", owner="Acme", title="Acme database")
        # Made with a title another one has: its client's name is added.
        assert second.title == "Google · Acme · a.y@example.com"
        second.title = same  # as two made before that would read
        session.flush()
        assert instances.suggested_title(first) == (
            "Google · General Informatics LLC · a.y@example.com"
        )
        assert instances.suggested_title(second) == "Google · Acme · a.y@example.com"
        assert instances.suggested_title(alone) is None
        second.title = "Google · Acme · a.y@example.com"
        session.flush()
        assert instances.suggested_title(first) is None  # no longer the same


def test_a_client_s_connection_titled_like_another_gets_its_client_s_name(
    database_url: str,
) -> None:
    del database_url
    with session_scope() as session:
        make_project(session, "acme-web", owner="Acme")
        make_project(session, "globex-web", owner="Globex")
        make_instance(session, "acme-db", owner="Acme", title="Database")
        globex = make_instance(session, "globex-db", owner="Globex", title="Database")
        assert globex.title == "Database · Globex"
        mine = make_instance(
            session, "my-db", owner=instances.SHARED_OWNER, shared=True, title="Database"
        )
        assert mine.title == "Database"  # your own keeps the title you gave it
