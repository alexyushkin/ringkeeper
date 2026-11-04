"""A project's title: what people read. Its slug is for URLs, IDs and the AI."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from ringkeeper.channels import cards
from ringkeeper.models import AuthMode, Decision, DecisionKind, Task, TaskStatus
from ringkeeper.services import digest, inbox, mentions, projects, titles
from tests.helpers import make_instance, make_project
from tests.test_web_api import (  # noqa: F401 (fixtures)
    admin,  # pyright: ignore[reportUnusedImport]
    owner,  # pyright: ignore[reportUnusedImport]
    setup_code,  # pyright: ignore[reportUnusedImport]
)


def named(session: Session, slug: str, title: str) -> None:
    make_project(session, slug).title = title
    session.flush()


def test_a_project_nobody_named_reads_like_its_slug(session: Session) -> None:
    make_project(session, "acme-web")
    assert titles.of(session, "acme-web") == "Acme web"
    assert titles.of(session, "long-gone") == "Long gone"


def test_the_api_takes_and_returns_the_title(owner: TestClient) -> None:  # noqa: F811
    made = {"name": "geninfos-web", "title": "geninfos.com website", "owner": "General Informatics"}
    assert owner.post("/projects", json=made).status_code == 201
    assert owner.get("/projects/geninfos-web").json()["title"] == "geninfos.com website"
    assert owner.patch("/projects/geninfos-web", json={"title": "The site"}).status_code == 200
    shown = owner.get("/projects/geninfos-web").json()
    assert (shown["name"], shown["title"]) == ("geninfos-web", "The site")


def test_changing_something_else_keeps_the_title(session: Session) -> None:
    named(session, "acme-web", "Acme's site")
    assert projects.rename_project(session, "acme-web", "acme-site").title == "Acme's site"


def test_a_mention_by_title_is_a_firm_choice(session: Session) -> None:
    named(session, "acme-web", "Acme site")
    named(session, "acme-web-shop", "Acme site shop")
    assert mentions.find(session, "fix @Acme site now").projects == ["acme-web"]
    assert mentions.find(session, "fix @acme site shop").projects == ["acme-web-shop"]
    assert mentions.find(session, "fix @acme-web").projects == ["acme-web"]
    assert not mentions.find(session, "mail me@Acme site")


def test_a_connection_of_that_name_is_more_specific_than_a_title(session: Session) -> None:
    named(session, "acme-web", "Reports")
    make_instance(session, "reports")
    found = mentions.find(session, "@Reports please")
    assert (found.projects, found.connections) == ([], ["reports"])


def test_a_client_is_mentioned_by_its_name_too(session: Session) -> None:
    make_project(session, "acme-web", owner="Acme Windows & Doors")
    assert mentions.find(session, "for @Acme Windows & Doors").clients == ["acme-windows-doors"]
    assert mentions.find(session, "for @acme-windows-doors").clients == ["acme-windows-doors"]
    [offer] = [o for o in mentions.offered(session) if o["kind"] == "client"]
    assert offer["token"] == "Acme Windows & Doors"


def test_the_box_offers_a_project_by_its_title(session: Session) -> None:
    named(session, "acme-web", "Acme site")
    [offer] = [o for o in mentions.offered(session) if o["kind"] == "project"]
    assert (offer["token"], offer["label"]) == ("Acme site", "Acme site")


def test_channels_say_the_title_and_answer_with_the_slug(session: Session) -> None:
    named(session, "acme-web", "Acme site")
    named(session, "acme-shop", "Acme store")
    decision = Decision(
        kind=DecisionKind.ROUTE,
        project="acme-web",
        payload={"candidates": [{"project": "acme-web"}, {"project": "acme-shop"}]},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(decision)
    session.flush()
    card = cards.decision_card(decision, None)
    assert "Acme site" in card.text
    assert "acme-web" not in card.text
    assert [b.label for b in card.buttons][:2] == ["Acme site", "Acme store"]
    assert inbox.options(decision)[:2] == ["acme-web", "acme-shop"]
    assert "Acme site or Acme store" in inbox.summary(decision)


def test_a_finished_task_and_the_digest_say_the_title(session: Session) -> None:
    named(session, "acme-web", "Acme site")
    task = Task(
        project="acme-web",
        prompt="Do it",
        status=TaskStatus.FAILED,
        auth_mode=AuthMode.API_KEY,
        max_turns=1,
        max_runtime_s=60,
        budget_usd=1,
    )
    session.add(task)
    session.flush()
    assert "Acme site" in cards.task_card(task, None).text
    [day] = digest.build(session)["clients"]
    assert day["failed"] == ["Acme site: Do it"]
