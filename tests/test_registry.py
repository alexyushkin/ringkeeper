from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from ringkeeper.cli.main import app
from ringkeeper.services import projects, registry

EXAMPLE = Path(__file__).parents[1] / "examples" / "registry.yaml"
runner = CliRunner()


def apply(path: Path, *flags: str, exit_code: int = 0) -> str:
    result = runner.invoke(app, ["registry", "apply", str(path), *flags])
    assert result.exit_code == exit_code, result.output
    return result.stdout if exit_code == 0 else result.stderr


def write(tmp_path: Path, data: object, name: str = "registry.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def instance(
    name: str, env: str = "dev", type: str = "supabase", **extra: object
) -> dict[str, object]:
    return {
        "name": name,
        "type": type,
        "env": env,
        "secret_ref": f"aws-sm://ringkeeper/{name}",
        "owner": "acme",
        **extra,
    }


def project(name: str, *bindings: dict[str, object], **extra: object) -> dict[str, object]:
    return {"name": name, "owner": "acme", "bindings": list(bindings), **extra}


# --- The example file ---


def test_example_applies_twice_with_no_changes_the_second_time(database_url: str) -> None:
    first = apply(EXAMPLE)
    assert "+ instance acme-supabase-dev" in first
    assert "+ binding acme-web -> acme-supabase-dev" in first
    assert "18 to create, 0 to update, 0 unchanged" in first

    assert apply(EXAMPLE).strip() == "No changes."


def test_dry_run_shows_diff_and_writes_nothing(database_url: str, engine: Engine) -> None:
    output = apply(EXAMPLE, "--dry-run")
    assert "+ project globex-portal" in output
    assert "Dry run: nothing was written." in output
    with Session(engine) as session:
        assert projects.list_projects(session) == []


def test_dry_run_diff_lists_changed_fields(database_url: str, tmp_path: Path) -> None:
    apply(
        write(
            tmp_path,
            {"instances": [instance("acme-n8n", type="n8n", base_url="https://a.example")]},
        )
    )
    changed = write(
        tmp_path, {"instances": [instance("acme-n8n", type="n8n", base_url="https://b.example")]}
    )
    output = apply(changed, "--dry-run")
    assert "~ instance acme-n8n\n    base_url: 'https://a.example' -> 'https://b.example'" in output
    assert "0 to create, 1 to update, 0 unchanged" in output
    # Still the old value: the dry run rolled back.
    assert "base_url: 'https://a.example'" in apply(changed, "--dry-run")


def test_export_round_trips(database_url: str, tmp_path: Path, engine: Engine) -> None:
    apply(EXAMPLE)
    exported = tmp_path / "exported.yaml"
    result = runner.invoke(app, ["registry", "export", "-o", str(exported)])
    assert result.exit_code == 0, result.output

    assert apply(exported).strip() == "No changes."
    source = registry.load_registry_file(EXAMPLE)
    with Session(engine) as session:
        again = registry.export_registry(session)
    assert registry.dump_registry(again) == exported.read_text(encoding="utf-8")
    assert sorted(i.name for i in again.instances) == sorted(i.name for i in source.instances)
    assert sorted(p.name for p in again.projects) == sorted(p.name for p in source.projects)


def test_export_to_stdout(database_url: str) -> None:
    apply(EXAMPLE)
    result = runner.invoke(app, ["registry", "export"])
    assert result.exit_code == 0
    data = yaml.safe_load(result.stdout)
    assert [p["name"] for p in data["projects"]] == ["acme-reports", "acme-web", "globex-portal"]


# --- Rules apply to files exactly as to the CLI ---


def test_dry_run_reports_rule_violations(database_url: str, tmp_path: Path) -> None:
    path = write(
        tmp_path,
        {
            "instances": [instance("acme-supabase-prod", env="prod")],
            "projects": [project("acme-web", {"instance": "acme-supabase-prod", "default": True})],
        },
    )
    error = apply(path, "--dry-run", exit_code=1)
    assert "binding acme-web -> acme-supabase-prod: instance 'acme-supabase-prod' is prod" in error


def test_binding_unknown_instance(database_url: str, tmp_path: Path) -> None:
    path = write(tmp_path, {"projects": [project("acme-web", {"instance": "ghost"})]})
    error = apply(path, exit_code=1)
    assert "binding acme-web -> ghost: cannot bind: instance 'ghost' does not exist" in error


def test_failed_apply_writes_nothing(database_url: str, tmp_path: Path, engine: Engine) -> None:
    path = write(tmp_path, {"projects": [project("acme-web", {"instance": "ghost"})]})
    apply(path, exit_code=1)
    with Session(engine) as session:
        assert projects.find_project(session, "acme-web") is None


def test_one_file_can_move_a_default(database_url: str, tmp_path: Path) -> None:
    instances = [instance("acme-supabase-dev"), instance("acme-supabase-staging", env="staging")]
    apply(
        write(
            tmp_path,
            {
                "instances": instances,
                "projects": [
                    project("acme-web", {"instance": "acme-supabase-dev", "default": True})
                ],
            },
        )
    )
    moved = write(
        tmp_path,
        {
            "instances": instances,
            "projects": [
                project(
                    "acme-web",
                    {"instance": "acme-supabase-staging", "default": True},
                    {"instance": "acme-supabase-dev", "default": False},
                )
            ],
        },
    )
    output = apply(moved)
    assert "~ binding acme-web -> acme-supabase-dev\n    default: True -> False" in output
    assert apply(moved).strip() == "No changes."


def test_one_file_can_demote_a_default_and_make_it_prod(database_url: str, tmp_path: Path) -> None:
    binding: dict[str, object] = {"instance": "acme-supabase-x", "default": True}
    apply(
        write(
            tmp_path,
            {
                "instances": [instance("acme-supabase-x")],
                "projects": [project("acme-web", binding)],
            },
        )
    )

    promoted = write(
        tmp_path,
        {
            "instances": [instance("acme-supabase-x", env="prod")],
            "projects": [project("acme-web", {"instance": "acme-supabase-x", "default": False})],
        },
    )
    output = apply(promoted)
    assert "env: dev -> prod" in output


def test_apply_never_deletes(database_url: str, tmp_path: Path, engine: Engine) -> None:
    apply(EXAMPLE)
    apply(write(tmp_path, {"projects": [project("globex-portal", owner="globex-web")]}))
    with Session(engine) as session:
        names = [p.name for p in projects.list_projects(session)]
    assert names == ["acme-reports", "acme-web", "globex-portal"]


# --- File validation ---


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"instances": [instance("a"), instance("a")]}, "duplicate names in instances: a"),
        ({"projects": [project("p"), project("p")]}, "duplicate names in projects: p"),
        (
            {"projects": [project("p", {"instance": "a"}, {"instance": "a"})]},
            "duplicate names in bindings of project 'p': a",
        ),
        ({"projects": [{**project("p"), "colour": "red"}]}, "projects.0.colour: Extra inputs"),
        ({"version": 2}, "version: Input should be 1"),
    ],
)
def test_invalid_files(database_url: str, tmp_path: Path, data: object, message: str) -> None:
    assert message in apply(write(tmp_path, data), exit_code=1)


def test_secret_value_in_file_is_rejected_and_not_echoed(database_url: str, tmp_path: Path) -> None:
    secret = "ghp_000000000000000000000000000000000000"
    error = apply(write(tmp_path, {"instances": [instance("a", secret_ref=secret)]}), exit_code=1)
    assert "instances.0.secret_ref: Value error, secret_ref must be a pointer" in error
    assert secret not in error


def test_unreadable_and_malformed_files(database_url: str, tmp_path: Path) -> None:
    assert "cannot read" in apply(tmp_path / "missing.yaml", exit_code=1)
    bad = tmp_path / "bad.yaml"
    bad.write_text("projects: [", encoding="utf-8")
    assert "is not valid YAML" in apply(bad, exit_code=1)
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("just a string", encoding="utf-8")
    assert "must contain a mapping" in apply(scalar, exit_code=1)


def test_empty_file_is_no_changes(database_url: str, tmp_path: Path) -> None:
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert apply(empty).strip() == "No changes."
