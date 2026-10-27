"""Local MCP servers from the catalog: an npm, PyPI or container package,
installed on request into Ringkeeper's data folder and run from there.

1. `plan`: what will be installed and run, for one confirmation screen: the
   publisher (as the registry verified it), the package and its exact
   version, the commands, and what this computer needs (Node.js, uv or
   Docker). The plan has a fingerprint; installing needs it back, so what
   runs is exactly what was shown.
2. `install` (in the background; `job` reports progress): the package goes
   into data/mcp-packages/<type>/<name>@<version>, with npm's install scripts
   off. A container image is pulled and pinned by its digest. The connection
   then starts that installed copy, never a newer one: npx and uvx, which
   fetch the latest version at each start, aren't used.

Nothing updates by itself. When the catalog lists a newer version, the
connection says so; updating is another plan and confirmation.
"""

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError

logger = logging.getLogger(__name__)

INSTALL_SECONDS = 600
NEEDS = {
    "npm": ("npm", "Node.js (nodejs.org)"),
    "pypi": ("uv", "uv (docs.astral.sh/uv)"),
    "oci": ("docker", "Docker Desktop (docker.com)"),
}
SAFE_PART = re.compile(r"[^A-Za-z0-9._@+-]+")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$")


class InstallError(RuleViolationError):
    """The install didn't work. The message is for the user."""


def which(command: str) -> str | None:
    return shutil.which(command)


def run(args: list[str], cwd: Path) -> str:
    """Run an installer command; its output, or InstallError with the end of it."""
    try:
        done = subprocess.run(  # noqa: S603 (fixed installers, arguments checked above)
            args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=INSTALL_SECONDS, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(f"{Path(args[0]).name} didn't run: {error}") from None
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip().splitlines()[-3:]
        raise InstallError(f"{Path(args[0]).name} failed: {' '.join(said)[:400]}")
    return done.stdout


# Tests replace these with fakes that lay out a package.
runner: Callable[[list[str], Path], str] = run
finder: Callable[[str], str | None] = which


@dataclass(frozen=True)
class Plan:
    server: str
    publisher: str
    verified: str
    registry: str  # npm, pypi, oci
    identifier: str
    version: str
    install: str  # the command, for people to read
    runs: str
    needs: str  # the program this computer needs
    missing: bool  # ... and doesn't have
    env: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    arguments: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    @property
    def fingerprint(self) -> str:
        fixed = [self.server, self.registry, self.identifier, self.version, self.install, self.runs]
        return hashlib.sha256(json.dumps(fixed).encode()).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "fingerprint": self.fingerprint}


def _check_identity(registry: str, identifier: str, version: str) -> None:
    if registry not in NEEDS:
        raise InstallError(f"Ringkeeper can't install {registry or 'these'} packages yet.")
    if not identifier or identifier.startswith("-") or any(c.isspace() for c in identifier):
        raise InstallError("This package's name isn't one Ringkeeper can install.")
    if not VERSION.match(version) or version in ("latest", "*"):
        raise InstallError("This package doesn't name an exact version, so it can't be pinned.")


def folder_for(settings: Settings, registry: str, identifier: str, version: str) -> Path:
    name = SAFE_PART.sub("_", identifier.replace("/", "__"))
    return settings.data_dir / "mcp-packages" / registry / f"{name}@{version}"


def _install_command(registry: str, identifier: str, version: str) -> str:
    if registry == "npm":
        return f"npm install --ignore-scripts {identifier}@{version}"
    if registry == "pypi":
        return f"uv pip install {identifier}=={version}"
    return f"docker pull {identifier}:{version}"


def _run_preview(registry: str, identifier: str, version: str) -> str:
    if registry == "npm":
        return f"node <data folder>/mcp-packages/npm/{identifier}@{version} (its program)"
    if registry == "pypi":
        return f"{identifier} {version}, from its own environment in the data folder"
    return f"docker run -i --rm {identifier}@<digest of {version}>"


def plan(summary: dict[str, Any], index: int = 0) -> Plan:
    """What installing the server's `index`th package means, for the confirmation screen."""
    packages = cast(list[dict[str, Any]], summary.get("packages") or [])
    if not 0 <= index < len(packages):
        raise InstallError("This server has no such package.")
    package = packages[index]
    registry, identifier = str(package["registry"]), str(package["identifier"])
    version = str(package["version"])
    _check_identity(registry, identifier, version)
    if not package.get("usable"):
        raise InstallError("This package doesn't run over stdio, so Ringkeeper can't start it.")
    program, label = NEEDS[registry]
    return Plan(
        server=str(summary["name"]), publisher=str(summary["publisher"]),
        verified=str(summary["verified"]), registry=registry, identifier=identifier,
        version=version, install=_install_command(registry, identifier, version),
        runs=_run_preview(registry, identifier, version), needs=label,
        missing=finder(program) is None,
        env=cast(list[dict[str, Any]], package.get("env") or []),
        arguments=cast(list[dict[str, Any]], package.get("arguments") or []),
    )  # fmt: skip


# --- Installing ---


def _npm(settings: Settings, p: Plan) -> dict[str, Any]:
    folder = folder_for(settings, "npm", p.identifier, p.version)
    folder.mkdir(parents=True, exist_ok=True)
    npm = finder("npm") or "npm"
    runner(
        [npm, "install", "--prefix", str(folder), "--no-save", "--no-audit", "--no-fund",
         "--ignore-scripts", "--omit=dev", f"{p.identifier}@{p.version}"],
        folder,
    )  # fmt: skip
    package_dir = folder / "node_modules" / Path(*p.identifier.split("/"))
    try:
        manifest: Any = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstallError("npm finished, but the package isn't where it should be.") from None
    if str(manifest.get("version")) != p.version:
        raise InstallError(f"npm installed {manifest.get('version')}, not {p.version}.")
    bins: Any = manifest.get("bin")
    if isinstance(bins, str):
        entry = bins
    elif isinstance(bins, dict) and bins:
        named = cast(dict[str, str], bins)
        short = p.identifier.split("/")[-1]
        entry = named.get(short) or named[sorted(named)[0]]
    else:
        raise InstallError("This package has no program to start.")
    script = (package_dir / entry).resolve()
    if not script.is_relative_to(folder.resolve()) or not script.is_file():
        raise InstallError("This package's program isn't inside the package.")
    return {"command": finder("node") or "node", "args": [str(script)]}


def _pypi(settings: Settings, p: Plan) -> dict[str, Any]:
    folder = folder_for(settings, "pypi", p.identifier, p.version)
    folder.mkdir(parents=True, exist_ok=True)
    uv = finder("uv") or "uv"
    environment = folder / "venv"
    runner([uv, "venv", "--quiet", str(environment)], folder)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    runner([uv, "pip", "install", "--python", str(python), f"{p.identifier}=={p.version}"], folder)
    scripts = environment / ("Scripts" if sys.platform == "win32" else "bin")
    suffix = ".exe" if sys.platform == "win32" else ""
    wanted = scripts / f"{p.identifier}{suffix}"
    if not wanted.is_file():
        theirs = ("python", "activate", "deactivate", "pip", "uv")
        own = sorted(
            f for f in scripts.glob(f"*{suffix}")
            if f.is_file() and not f.stem.lower().startswith(theirs)
        )  # fmt: skip
        if len(own) != 1:
            raise InstallError(f"Couldn't tell which program {p.identifier} starts.")
        wanted = own[0]
    return {"command": str(wanted), "args": []}


def _oci(_settings: Settings, p: Plan, env_names: list[str]) -> dict[str, Any]:
    docker = finder("docker") or "docker"
    cwd = Path.cwd()
    image = f"{p.identifier}:{p.version}"
    runner([docker, "pull", image], cwd)
    digest = runner(
        [docker, "image", "inspect", "--format", "{{index .RepoDigests 0}}", image], cwd
    )
    digest = digest.strip()
    if "@sha256:" not in digest:
        raise InstallError("Docker didn't report the image's digest, so it can't be pinned.")
    args = ["run", "-i", "--rm"]
    for name in env_names:
        args += ["-e", name]  # the value comes from the server's environment, not the command line
    return {"command": docker, "args": [*args, digest], "pinned": digest}


def _arguments(p: Plan, values: dict[str, str]) -> list[str]:
    """The package's arguments: fixed ones as listed, the rest from what the user filled in."""
    args: list[str] = []
    for argument in p.arguments:
        name = str(argument["name"])
        value = str(argument.get("value") or values.get(name) or argument.get("default") or "")
        if not value:
            if argument.get("required"):
                raise InstallError(f"Fill in {name}.")
            continue
        if argument.get("type") == "named":
            args += [name, value] if name.startswith("-") else [f"--{name}", value]
        else:
            args.append(value)
    return args


def environment(p: Plan, values: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for variable in p.env:
        name = str(variable["name"])
        value = str(values.get(name) or variable.get("value") or variable.get("default") or "")
        if value:
            env[name] = value
        elif variable.get("required"):
            raise InstallError(f"Fill in {name}.")
    return env


def install(settings: Settings, p: Plan, values: dict[str, str]) -> dict[str, Any]:
    """Install the planned package; returns the server's settings (for the vault)."""
    if finder(NEEDS[p.registry][0]) is None:
        raise InstallError(f"Installing this needs {p.needs}, which isn't on this computer.")
    env = environment(p, values)
    extra = _arguments(p, values)
    if p.registry == "npm":
        spec = _npm(settings, p)
    elif p.registry == "pypi":
        spec = _pypi(settings, p)
    else:
        spec = _oci(settings, p, sorted(env))
    return {
        "command": spec["command"],
        "args": [*spec["args"], *extra],
        "env": env,
        "pinned": spec.get("pinned") or f"{p.identifier}@{p.version}",
    }


# --- Background jobs, one per connection name ---


@dataclass
class Job:
    state: str = "installing"  # installing, done, failed
    error: str | None = None


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


def start(name: str, work: Callable[[], None]) -> Job:
    with _jobs_lock:
        current = _jobs.get(name)
        if current is not None and current.state == "installing":
            raise InstallError(f"'{name}' is already being installed.")
        job = _jobs[name] = Job()

    def go() -> None:
        try:
            work()
            job.state = "done"
        except RuleViolationError as error:
            job.state, job.error = "failed", str(error)
        except Exception as error:  # an installer's surprise: say what, keep the app up
            logger.exception("install of %s failed", name)
            job.state, job.error = "failed", f"The install failed: {type(error).__name__}"

    threading.Thread(target=go, daemon=True, name=f"rk-install-{name}").start()
    return job


def job(name: str) -> Job | None:
    return _jobs.get(name)


def remove_folder(settings: Settings, pinned: str, registry: str) -> None:
    """Drop an installed package's folder (a removed connection's), if it's ours."""
    identifier, _, version = pinned.rpartition("@")
    if registry in ("npm", "pypi") and identifier and version:
        folder = folder_for(settings, registry, identifier, version)
        root = (settings.data_dir / "mcp-packages").resolve()
        if folder.resolve().is_relative_to(root):
            shutil.rmtree(folder, ignore_errors=True)
