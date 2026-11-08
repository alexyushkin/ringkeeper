"""Recipes (services.recipes): an agent and its routines as one file. Export one, read one
from a file or an https link (shown before anything is added), and add it to a project, its
routines paused. The bundled starters are on the routine templates screen."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field, model_validator

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import SessionDep, WriteSessionDep, require_owner
from ringkeeper.schemas import Input
from ringkeeper.services import admin_audit, recipes

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])
OWNER = "owner"


def _shown(key: str, recipe: recipes.Recipe) -> dict[str, Any]:
    return {
        "key": key, "name": recipe.name, "description": recipe.description,
        "needs": recipe.agent.needs,
        "routines": [{"name": r.name, "trigger": r.trigger} for r in recipe.routines],
    }  # fmt: skip


@router.get("/recipes/starters")
def starters() -> list[dict[str, Any]]:
    return [_shown(key, recipe) for key, recipe in recipes.starters().items()]


@router.get("/recipes/starters/{key}")
def starter(key: str) -> dict[str, Any]:
    found = recipes.starters().get(key)
    if found is None:
        raise HTTPException(404, detail="There's no starter recipe by that name.")
    return found.model_dump(exclude_none=True)


class ReadIn(Input):
    text: Annotated[str, Field(max_length=recipes.MAX_BYTES)] | None = None
    url: Annotated[str, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def _one(self) -> "ReadIn":
        if bool(self.text) == bool(self.url):
            raise ValueError("give the recipe's file or its link")
        return self


@router.post("/recipes/read")
def read(data: ReadIn) -> dict[str, Any]:
    """Read a recipe, from a file's text or an https link, to show before adding it."""
    found = recipes.parse(data.text) if data.text else recipes.fetch(data.url or "")
    return found.model_dump(exclude_none=True)


class NeedsIn(Input):
    recipe: dict[str, Any]


@router.post("/projects/{name}/recipes/needs")
def needs(name: str, data: NeedsIn, session: SessionDep) -> dict[str, Any]:
    return recipes.needs(session, name, recipes.parse_data(data.recipe))


class AddIn(Input):
    recipe: dict[str, Any]
    connections: dict[str, str] = Field(default_factory=dict[str, str])
    events: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    tz: Annotated[str, Field(max_length=64)] | None = None


@router.post("/projects/{name}/recipes", status_code=201)
def add(name: str, data: AddIn, session: WriteSessionDep) -> dict[str, Any]:
    """The recipe's agent and routines in the project. The routines start paused."""
    recipe = recipes.parse_data(data.recipe)
    agent, made = recipes.add(
        session, name, recipe, connections=data.connections, events=data.events, tz=data.tz
    )
    admin_audit.record(
        session, "recipe.add",
        f"added the recipe {recipe.name}: the agent {agent.name} and {len(made)} paused "
        f"routine{'s' if len(made) != 1 else ''}",
        by=OWNER, project=name,
    )  # fmt: skip
    return {"agent": agent.name, "routines": [str(r.id) for r in made]}


@router.get("/projects/{name}/agents/{agent}/recipe")
def export(name: str, agent: str, session: SessionDep) -> Response:
    """The agent and its routines as a recipe file: no keys, no connection names."""
    recipe = recipes.export(session, name, agent)
    return Response(
        recipes.as_text(recipe), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{recipes.file_name(recipe)}"'},
    )  # fmt: skip
