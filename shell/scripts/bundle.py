"""Lays out what the installed app runs, in shell/resources, for `tauri build`.

    uv run python shell/scripts/bundle.py [--skip-web]

- python/   a standalone CPython with Ringkeeper, its dependencies, the worker
            and the stt modules, and the Claude Code CLI that comes inside the
            Agent SDK, unmodified; plus voice-packages.json, the voice extra's
            wheels (address, size, hash), which the app downloads only when
            voice is first used (ringkeeper.voice_engine)
- web/      the static web app (web/scripts/build-desktop.mjs)
- mingit/   git for the workers, on Windows (fetch-mingit.ps1)
- notices/  third-party license notices, shown in Settings > About

Everything is pinned: Python by version (uv checks the download), packages by
uv.lock with hashes, MinGit by checksum.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from email.parser import Parser
from pathlib import Path

PYTHON_VERSION = "3.12.12"
REPO = Path(__file__).resolve().parents[2]
SHELL = REPO / "shell"
OUT = SHELL / "resources"
# The voice wheels' license files, for the notices (beside the bundle, not in it).
VOICE_LICENSES = SHELL / "target" / "voice-licenses"
WINDOWS = sys.platform == "win32"

# Parts of the standard install the app never uses.
PYTHON_TRIM = [
    "tcl",
    "include",
    "libs",
    "Scripts",
    "share",
    "Lib/tkinter",
    "Lib/idlelib",
    "Lib/turtledemo",
    "Lib/ensurepip",
    "Lib/lib2to3",
    "Lib/test",
    "Lib/turtle.py",
    "DLLs/_tkinter.pyd",
    "DLLs/tcl86t.dll",
    "DLLs/tk86t.dll",
    "lib/tcl8",
    "lib/tcl8.6",
    "lib/tk8.6",
    "lib/itcl4.2.4",
    "lib/thread2.8.9",
]
IMPORT_CHECK = "; ".join(
    [
        "import ringkeeper.desktop.server, ringkeeper_worker, ringkeeper_stt, claude_agent_sdk",
        "import keyring",
        "print('keychain:', keyring.get_keyring())",
    ]
)
SITE_TRIM = (".pyi", ".pxd", ".pyx", ".f90", ".h", ".c", ".cpp")
CLI_NOTICE = """\
Claude Code (the `claude` program, version {version})
Copyright (c) Anthropic PBC. All rights reserved.

Included unmodified, as shipped inside the Claude Agent SDK for Python, which
Ringkeeper uses to run its agents. Your use of it is subject to Anthropic's
Commercial Terms of Service (https://www.anthropic.com/legal/commercial-terms)
and Usage Policy (https://www.anthropic.com/legal/aup). Ringkeeper is not made
or endorsed by Anthropic.
"""
WEBVIEW2_NOTICE = """\
Microsoft Edge WebView2 Runtime

Ringkeeper's window uses the WebView2 Runtime that comes with Windows 11. On
Windows 10, the installer runs Microsoft's Evergreen Bootstrapper, which
installs the runtime from Microsoft. The runtime is Microsoft's software,
under Microsoft's license terms:
https://developer.microsoft.com/en-us/microsoft-edge/webview2/
"""
MINGIT_NOTICE = """\
MinGit {version} (Git for Windows, without bash)

Git is free software under the GNU General Public License, version 2; Git for
Windows also includes other free software under its own licenses (listed in
the files below). The complete corresponding source code for this exact build
is published by the Git for Windows project:
https://github.com/git-for-windows/git/releases/tag/v{version}
and, for its other components, https://github.com/git-for-windows/MINGW-packages
and https://github.com/msys2/MINGW-packages. Write to the Ringkeeper project
(https://github.com/alexyushkin/ringkeeper-releases/issues) if you'd like a copy.
"""


def run(*args: str | Path, cwd: Path = REPO, env: dict[str, str | None] | None = None) -> str:
    """Runs a tool; `env` adds variables, or removes those set to None."""
    print("+", " ".join(str(a) for a in args), flush=True)
    merged = {**os.environ, **(env or {})}
    done = subprocess.run(  # noqa: S603 (fixed tools, our own arguments)
        [str(a) for a in args],
        cwd=cwd,
        check=False,  # checked below, after showing what it said
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={k: v for k, v in merged.items() if v is not None},
    )
    if done.returncode:
        # What it said, so a failed build shows why.
        print(done.stdout, done.stderr, sep="\n", flush=True)
        done.check_returncode()
    return done.stdout


def python_exe(root: Path) -> Path:
    return root / "python.exe" if WINDOWS else root / "bin" / "python3"


def site_packages(root: Path) -> Path:
    if WINDOWS:
        return root / "Lib" / "site-packages"
    return root / "lib" / f"python{PYTHON_VERSION.rsplit('.', 1)[0]}" / "site-packages"


def remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def export(work: Path, name: str, *extra: str) -> list[str]:
    """uv.lock's pinned requirements (with hashes), one block per package."""
    out = work / name
    run(
        "uv", "export", "--frozen", "--no-dev", *extra, "--no-emit-project",
        "--format", "requirements-txt", "--output-file", out,
    )  # fmt: skip
    blocks: list[str] = []
    for line in out.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith((" ", "#")):
            blocks.append(line)
        elif line.strip().startswith("--hash") and blocks:
            blocks[-1] += "\n" + line
    return blocks


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_tags(filename: str) -> set[str]:
    """A wheel file name's tags, expanded (py3.py2-none-any: two tags)."""
    python, abi, platform = filename.removesuffix(".whl").split("-")[-3:]
    return {
        f"{p}-{a}-{t}"
        for p in python.split(".")
        for a in abi.split(".")
        for t in platform.split(".")
    }


def voice_wheels(work: Path, root: Path, base: list[str]) -> Path:
    """The voice extra's own packages (beyond the app's): installed into a
    scratch folder here, the way uv picks them for this Python, to learn which
    wheel each one is, then listed with uv.lock's address, size and hash."""
    voice = [b for b in export(work, "voice.txt", "--extra", "voice") if b not in base]
    requirements = work / "voice-only.txt"
    requirements.write_text("\n".join(voice) + "\n", encoding="utf-8")
    scratch = work / "voice-site"
    python = python_exe(root)
    run(
        "uv", "pip", "install", "--python", python, "--target", scratch,
        "--no-deps", "--require-hashes", "--no-compile", "-r", requirements,
    )  # fmt: skip
    lock = {
        (normalized(p["name"]), p["version"]): p
        for p in tomllib.loads((REPO / "uv.lock").read_text(encoding="utf-8"))["package"]
    }
    wheels = []
    for info in sorted(scratch.glob("*.dist-info")):
        meta = Parser().parsestr((info / "METADATA").read_text(encoding="utf-8"))
        tags = {
            line.split(":", 1)[1].strip()
            for line in (info / "WHEEL").read_text(encoding="utf-8").splitlines()
            if line.startswith("Tag:")
        }
        entry = lock[(normalized(meta["Name"]), meta["Version"])]
        [chosen] = [w for w in entry["wheels"] if wheel_tags(w["url"].rsplit("/", 1)[-1]) == tags]
        algorithm, digest = chosen["hash"].split(":", 1)
        if algorithm != "sha256":
            sys.exit(f"{entry['name']}: uv.lock has a {algorithm} hash, not sha256")
        wheels.append(
            {
                "name": entry["name"],
                "version": entry["version"],
                "url": chosen["url"],
                "sha256": digest,
                "size": chosen["size"],
            }
        )
    listed = root / "voice-packages.json"
    listed.write_text(
        json.dumps({"python": PYTHON_VERSION.rsplit(".", 1)[0], "wheels": wheels}, indent=1),
        encoding="utf-8",
    )
    # Their licenses go in the notices too: kept beside the bundle (not in it).
    remove(VOICE_LICENSES)
    VOICE_LICENSES.mkdir(parents=True)
    for info in scratch.glob("*.dist-info"):
        shutil.copytree(info, VOICE_LICENSES / info.name)
    total = sum(w["size"] for w in wheels)
    print(f"voice: {len(wheels)} wheels, {total / 1e6:.1f} MB to download when first used")
    return listed


def build_python(work: Path) -> Path:
    """A standalone CPython (from uv) with everything installed into it."""
    installs = work / "pythons"
    run("uv", "python", "install", PYTHON_VERSION, "--install-dir", installs)
    [found] = [p for p in installs.iterdir() if p.name.startswith(f"cpython-{PYTHON_VERSION}")]
    target = OUT / "python"
    remove(target)
    shutil.copytree(found, target, symlinks=True)
    for part in PYTHON_TRIM:
        remove(target / part)
    site = site_packages(target)
    for leftover in site.glob("pip*"):
        remove(leftover)

    base = export(work, "base.txt")
    requirements = work / "requirements.txt"
    requirements.write_text("\n".join(base) + "\n", encoding="utf-8")
    install = ["uv", "pip", "install", "--python", python_exe(target), "--target", site]
    run(*install, "--no-deps", "--require-hashes", "--no-compile", "-r", requirements)
    run("uv", "build", "--wheel", "--out-dir", work / "wheel")
    [wheel] = (work / "wheel").glob("ringkeeper-*.whl")
    run(*install, "--no-deps", "--no-compile", wheel)
    for package in (REPO / "worker" / "ringkeeper_worker", REPO / "stt" / "ringkeeper_stt"):
        shutil.copytree(package, site / package.name, ignore=shutil.ignore_patterns("__pycache__"))
    for cache in list(target.rglob("__pycache__")):
        remove(cache)
    # Packages' own tests, and files only for compiling against them or type checking.
    for folder in [p for p in site.rglob("*") if p.is_dir() and p.name in ("tests", "test")]:
        remove(folder)
    for suffix in SITE_TRIM:
        for leftover in site.rglob(f"*{suffix}"):
            remove(leftover)
    # Compiled once here, so the first start doesn't, and nothing is written later.
    subprocess.run(  # noqa: S603
        [
            str(python_exe(target)),
            "-I",
            "-m",
            "compileall",
            "-q",
            "-j0",
            "--invalidation-mode",
            "unchecked-hash",
            str(target),
        ],
        check=False,
    )
    # What the app and its workers import, from the trimmed install.
    run(python_exe(target), "-I", "-c", IMPORT_CHECK)
    # Voice: the list of its wheels, then the app's own download of them (from
    # PyPI, as a user's would be), checked by importing the engine.
    listed = voice_wheels(work, target, base)
    run(python_exe(target), "-I", "-m", "ringkeeper.voice_engine", listed, work / "voice-check")
    return target


def build_web(skip: bool) -> None:
    built = REPO / "web" / ".desktop" / "out"
    if not skip or not (built / "index.html").is_file():
        run("node", "scripts/build-desktop.mjs", cwd=REPO / "web")
    remove(OUT / "web")
    shutil.copytree(built, OUT / "web")


MINGIT_SCRIPT = SHELL / "scripts" / "fetch-mingit.ps1"


def _mingit_version() -> str | None:
    match = re.search(r'\$Version = "([^"]+)"', MINGIT_SCRIPT.read_text())
    return match.group(1) if match and WINDOWS else None


def fetch_mingit() -> str | None:
    if not WINDOWS:
        return None
    # Started from PowerShell 7 (as in CI), PSModulePath names its modules, and
    # Windows PowerShell then can't load its own (Get-FileHash). Without it, it
    # uses its defaults. (os.environ's keys are upper case on Windows.)
    run(
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", MINGIT_SCRIPT,
        env={"PSMODULEPATH": None},
    )  # fmt: skip
    return _mingit_version()


# --- Notices ---


def license_texts(folder: Path, patterns: tuple[str, ...]) -> list[tuple[str, str]]:
    found = []
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.is_file() and path.name.upper().startswith(patterns):
            found.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    return found


LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT")


def python_components(site: Path, kind: str = "Python package") -> list[dict]:
    components = []
    for info in sorted(site.glob("*.dist-info")):
        meta = Parser().parsestr((info / "METADATA").read_text(encoding="utf-8", errors="replace"))
        if meta["Name"] == "ringkeeper":
            continue  # listed first, as this app
        license_name = meta.get("License-Expression") or ""
        if not license_name:
            classifiers = [
                c.split("::")[-1].strip()
                for c in meta.get_all("Classifier") or []
                if c.startswith("License ::")
            ]
            license_name = ", ".join(classifiers) or (meta.get("License") or "").split("\n")[0]
        texts = license_texts(info / "licenses", LICENSE_NAMES) or license_texts(
            info, LICENSE_NAMES
        )
        for nested in (
            sorted((info / "licenses").rglob("*")) if (info / "licenses").is_dir() else []
        ):
            if nested.is_file() and nested.parent != info / "licenses":
                texts.append(
                    (
                        str(nested.relative_to(info / "licenses")),
                        nested.read_text(encoding="utf-8", errors="replace"),
                    )
                )
        components.append(
            {
                "name": meta["Name"],
                "version": meta["Version"],
                "license": license_name[:120],
                "kind": kind,
                "texts": texts,
            }
        )
    return components


def rust_components() -> list[dict]:
    metadata = json.loads(
        run(
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--filter-platform",
            run("rustc", "-vV").split("host: ")[1].split()[0],
            cwd=SHELL,
        )
    )
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    components = []
    for package in sorted(metadata["packages"], key=lambda p: (p["name"], p["version"])):
        if package["id"] not in resolved or package["source"] is None:
            continue  # not built for this platform, or the shell itself
        folder = Path(package["manifest_path"]).parent
        components.append(
            {
                "name": package["name"],
                "version": package["version"],
                "license": package.get("license") or "see text",
                "kind": "Rust crate",
                "texts": license_texts(folder, LICENSE_NAMES),
            }
        )
    return components


def web_components() -> list[dict]:
    """The packages whose code is in the web app's files."""
    listing = subprocess.run(
        ["npm", "ls", "--omit=dev", "--all", "--json", "--long"],  # noqa: S607
        cwd=REPO / "web",
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=WINDOWS,
        check=False,
    )
    seen: dict[str, dict] = {}

    def walk(deps: dict) -> None:
        for name, info in (deps or {}).items():
            key = f"{name}@{info.get('version')}"
            if key in seen or not info.get("path"):
                continue
            folder = Path(info["path"])
            seen[key] = {
                "name": name,
                "version": info.get("version", ""),
                "license": str(info.get("license") or "see text"),
                "kind": "web package",
                "texts": license_texts(folder, LICENSE_NAMES),
            }
            walk(info.get("dependencies"))

    walk(json.loads(listing.stdout or "{}").get("dependencies"))
    return sorted(seen.values(), key=lambda c: (c["name"], c["version"]))


def write_notices(python_root: Path, mingit_version: str | None) -> None:
    notices = OUT / "notices"
    remove(notices)
    notices.mkdir(parents=True)
    site = site_packages(python_root)
    cli_version = re.search(
        r'"([^"]+)"', (site / "claude_agent_sdk" / "_cli_version.py").read_text()
    )
    components: list[dict] = [
        {
            "name": "Ringkeeper",
            "version": json.loads((SHELL / "tauri.conf.json").read_text())["version"],
            "license": "MIT",
            "kind": "this app",
            "texts": [("LICENSE", (REPO / "LICENSE").read_text())],
        },
        {
            "name": "Python",
            "version": PYTHON_VERSION,
            "license": "PSF-2.0 (and others, below)",
            "kind": "runtime",
            "texts": license_texts(python_root, ("LICENSE",)),
        },
        {
            "name": "Claude Code CLI",
            "version": cli_version.group(1) if cli_version else "",
            "license": "Proprietary (Anthropic Commercial Terms)",
            "kind": "agent runtime",
            "texts": [
                (
                    "NOTICE",
                    CLI_NOTICE.format(version=cli_version.group(1) if cli_version else "bundled"),
                )
            ],
        },
    ]
    if WINDOWS:
        components.append(
            {
                "name": "Microsoft Edge WebView2 Runtime",
                "version": "",
                "license": "Microsoft software license",
                "kind": "window",
                "texts": [("NOTICE", WEBVIEW2_NOTICE)],
            }
        )
    if mingit_version:
        mingit = OUT / "mingit"
        texts = [("NOTICE", MINGIT_NOTICE.format(version=mingit_version))]
        texts += license_texts(mingit, ("LICENSE",))
        # Its components' own licenses (ucrt64/share/licenses/<package>/..., and the like).
        for path in sorted(mingit.rglob("*")):
            inside = path.parent != mingit
            if inside and path.is_file() and path.name.upper().startswith(LICENSE_NAMES):
                texts.append(
                    (
                        str(path.relative_to(mingit)),
                        path.read_text(encoding="utf-8", errors="replace"),
                    )
                )
        components.append(
            {
                "name": "MinGit",
                "version": mingit_version,
                "license": "GPL-2.0",
                "kind": "git for workers",
                "texts": texts,
            }
        )
    components += python_components(site)
    components += python_components(VOICE_LICENSES, "Python package, downloaded for voice")
    components += rust_components() + web_components()

    lines = [
        "Ringkeeper: third-party notices",
        "",
        "Ringkeeper includes the software listed below. Each part is under its own",
        "license, reproduced in full after its entry.",
        "",
    ]
    for component in components:
        title = f"{component['name']} {component['version']}".strip()
        lines += [
            "=" * 78,
            f"{title}  [{component['kind']}]",
            f"License: {component['license']}",
            "=" * 78,
            "",
        ]
        if not component["texts"]:
            lines += ["(No license file is included in this package; see its license above.)", ""]
        for name, text in component["texts"]:
            lines += [f"--- {name} ---", text.rstrip(), ""]
    (notices / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")
    listing = [{k: c[k] for k in ("name", "version", "license", "kind")} for c in components]
    (notices / "components.json").write_text(json.dumps(listing, indent=1), encoding="utf-8")
    print(f"notices: {len(components)} components")


# Windows paths stop at 260 characters: the installer allows install folders
# of up to 90 (shell/windows/hooks.nsh), which leaves this much below them.
# (macOS allows 1024, and its python3.12/ folder makes every path longer.)
MAX_RELATIVE_PATH = 165


def check_path_lengths() -> None:
    if not WINDOWS:
        return
    longest = max((p for p in OUT.rglob("*") if p.is_file()), key=lambda p: len(str(p)))
    relative = str(longest.relative_to(OUT))
    if len(relative) > MAX_RELATIVE_PATH:
        sys.exit(f"{relative} is {len(relative)} characters; at most {MAX_RELATIVE_PATH} fit")


def size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--skip-web", action="store_true", help="reuse an existing web build")
    parser.add_argument("--notices-only", action="store_true", help="only rewrite notices/")
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.notices_only:
        mingit_version = fetch_mingit() if not (OUT / "mingit").is_dir() else _mingit_version()
        write_notices(OUT / "python", mingit_version)
        return
    with tempfile.TemporaryDirectory(prefix="rk-bundle-") as temp:
        python_root = build_python(Path(temp))
    build_web(args.skip_web)
    mingit_version = fetch_mingit()
    write_notices(python_root, mingit_version)
    check_path_lengths()
    for part in sorted(OUT.iterdir()):
        print(f"{part.name:10} {size(part) / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
