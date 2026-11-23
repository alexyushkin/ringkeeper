"""Run one task on OpenAI's Codex, for a model on the owner's ChatGPT plan.

Codex's app-server runs as a child process and speaks JSON-RPC, one message per line, on its
stdin and stdout. Each task gets one thread (resumed with its id, like Claude Code's session):

- The model is reached only through the gateway's proxy for the plan (RINGKEEPER_MODEL_URL),
  with this task's own token (RINGKEEPER_MODEL_TOKEN). The plan's token stays in the gateway.
- The project's connections are the gateway's MCP endpoints, as for Claude, with the task's
  gateway token. Nothing a project or the computer has set up for Codex loads: its home is
  the task's own (CODEX_HOME), and everything is given here.
- ask_user and not_done are tools this process answers itself (Codex's dynamic tools), with
  inbox.py's words. After a question the turn is interrupted, so the session ends there.
- Every item Codex reports is written as the events Ringkeeper already reads from Claude Code
  (sdk.assistant, sdk.user, sdk.result), so the task's page, the steps and the edits work the
  same way.

The program is fetched on first use: the pinned release's app-server for this computer, checked
against the sha256 pinned beside this file (codex.json), and kept in a cache the next task
reuses (RINGKEEPER_CODEX_DIR).
"""

import contextlib
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import IO, Any, cast

from ringkeeper_worker import inbox as words
from ringkeeper_worker.agent import HEADLESS_NOTE, SHORT_PROMPT, shell_line
from ringkeeper_worker.config import WorkerConfig
from ringkeeper_worker.edits import EDITS
from ringkeeper_worker.emit import emit
from ringkeeper_worker.privileges import WORKER_GID, WORKER_UID, as_worker_kwargs, is_root

ENGINE = "codex"
PIN = Path(__file__).with_name("codex.json")
CACHE_ENV = "RINGKEEPER_CODEX_DIR"
MODEL_URL_ENV = "RINGKEEPER_MODEL_URL"
MODEL_TOKEN_ENV = "RINGKEEPER_MODEL_TOKEN"  # noqa: S105 (an env var name)
GATEWAY_TOKEN_ENV = "RK_GATEWAY_TOKEN"  # noqa: S105 (an env var name, for Codex's MCP client)
RESUME_PREFIX = "codex:"
PROVIDER = "ringkeeper"
INBOX = "ringkeeper-inbox"
TIMEOUT = 60
CHUNK = 1 << 20
MAX_OUTPUT_CHARS = 20000


class InstallError(RuntimeError):
    pass


# --- The program ---


def target() -> str:
    machine = "aarch64" if platform.machine().lower() in ("arm64", "aarch64") else "x86_64"
    if sys.platform == "win32":
        return f"{machine}-pc-windows-msvc"
    if sys.platform == "darwin":
        return f"{machine}-apple-darwin"
    return f"{machine}-unknown-linux-musl"


def pinned(pin: Path = PIN) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(pin.read_text(encoding="utf-8")))


def _download(url: str, into: Path, sha256: str) -> None:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response, into.open("wb") as out:  # noqa: S310 (a pinned https URL)
        while chunk := response.read(CHUNK):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != sha256:
        raise InstallError(
            "The downloaded Codex didn't match the copy this version of Ringkeeper expects, so "
            "it wasn't used. Run the task again; if it happens again, report it."
        )


def _extract(archive: Path, folder: Path) -> Path:
    """The one program in the release's archive: codex-app-server-<target>."""
    target_name = "codex-app-server.exe" if sys.platform == "win32" else "codex-app-server"
    out = folder / target_name
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zipped:
            names = [n for n in zipped.namelist() if Path(n).name.startswith("codex-app-server")]
            if len(names) != 1:
                raise InstallError("the Codex package didn't contain the program")
            with zipped.open(names[0]) as source, out.open("wb") as sink:
                shutil.copyfileobj(source, sink, CHUNK)
    else:
        with tarfile.open(archive, "r:gz") as tar:
            members = [
                m for m in tar.getmembers()
                if m.isfile() and Path(m.name).name.startswith("codex-app-server")
            ]  # fmt: skip
            source = tar.extractfile(members[0]) if len(members) == 1 else None
            if source is None:
                raise InstallError("the Codex package didn't contain the program")
            with source, out.open("wb") as sink:
                shutil.copyfileobj(source, sink, CHUNK)
    out.chmod(0o755)
    return out


def ensure(
    cache: Path | None = None, pin: Path = PIN, say: Callable[[str], None] | None = None
) -> Path:
    """The pinned Codex app-server for this computer, fetched first by the first task."""
    cache = cache or Path(os.environ.get(CACHE_ENV) or "/opt/codex")
    spec = pinned(pin)
    version = str(spec["version"])
    found = cast(dict[str, Any], spec["targets"]).get(target())
    if found is None:
        raise InstallError(f"Codex {version} isn't published for this computer ({target()}).")
    final = cache / version
    program = final / ("codex-app-server.exe" if sys.platform == "win32" else "codex-app-server")
    if program.is_file():
        return program
    if say is not None:
        say(f"Getting Codex {version} ready for ChatGPT plan tasks. Only the first task does this.")
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".partial-", dir=cache))
    try:
        archive = work / str(found["asset"])
        try:
            _download(f"{spec['release']}/{found['asset']}", archive, str(found["sha256"]))
        except OSError as error:
            raise InstallError(
                f"Couldn't download Codex {version}, which tasks on your ChatGPT plan need "
                f"({error}). Check the internet connection, then run the task again."
            ) from None
        _extract(archive, work)
        archive.unlink()
        work.chmod(0o755)
        try:
            os.rename(work, final)  # atomic; another task may have won the race
        except OSError:
            if not program.is_file():
                raise
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(work)
    return program


# --- What Codex is told and allowed ---


ASK_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}
NOT_DONE_SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "needs": {"type": "string"},
        "done": {"type": "string"},
    },
    "required": ["reason"],
    "additionalProperties": False,
}


def dynamic_tools() -> list[dict[str, Any]]:
    return [
        {"name": "ask_user", "description": words.ASK_DESCRIPTION, "inputSchema": ASK_SCHEMA},
        {
            "name": "not_done", "description": words.NOT_DONE_DESCRIPTION,
            "inputSchema": NOT_DONE_SCHEMA,
        },
    ]  # fmt: skip


def instructions(config: WorkerConfig) -> str:
    note = HEADLESS_NOTE.format(workspace=config.workspace) + "\n" + shell_line(config)
    if config.git:
        return note
    from datetime import datetime  # noqa: PLC0415

    today = datetime.now().astimezone().strftime("%A, %Y-%m-%d")
    return SHORT_PROMPT.format(today=today) + "\n\n" + note


def _toml(value: object) -> str:
    return json.dumps(value)  # a TOML string, number, boolean or array


def overrides(config: WorkerConfig) -> dict[str, str]:
    """Codex's settings for this task, as -c key=value (TOML values). The project's autonomy
    and web access decide the same things they decide for Claude Code."""
    off = set(config.disallowed_tools)
    shell = not ({"Bash", "PowerShell"} & off)
    writes = not ({"Edit", "Write"} & off)
    web = not ({"WebFetch", "WebSearch"} & off)
    found: dict[str, str] = {
        "model_provider": _toml(PROVIDER),
        f"model_providers.{PROVIDER}.name": _toml("Ringkeeper"),
        f"model_providers.{PROVIDER}.base_url": _toml(os.environ.get(MODEL_URL_ENV, "")),
        f"model_providers.{PROVIDER}.env_key": _toml(MODEL_TOKEN_ENV),
        f"model_providers.{PROVIDER}.wire_api": _toml("responses"),
        f"model_providers.{PROVIDER}.requires_openai_auth": "false",
        f"model_providers.{PROVIDER}.supports_websockets": "false",
        "web_search": _toml("live" if web else "disabled"),
        "features.apps": "false",  # OpenAI's own connectors: never a client's data
        "features.multi_agent": "false",
        "features.memories": "false",
        # Plan usage refuses tool_search, which Codex otherwise uses for MCP tools with the
        # models it knows: the connections' tools go in the first request instead. Code mode
        # (tools called from scripts) needs a program the worker doesn't fetch.
        "features.tool_search": "false",
        "features.search_tool": "false",
        "features.tool_search_always_defer_mcp_tools": "false",
        "features.code_mode": "false",
        "features.goals": "false",
        "features.shell_tool": _toml(shell),
        "features.unified_exec": _toml(shell),
        # The tokens stay out of the shell's environment.
        "shell_environment_policy.exclude": _toml(
            [MODEL_TOKEN_ENV, GATEWAY_TOKEN_ENV, "RINGKEEPER_*"]
        ),
        "check_for_update_on_startup": "false",
    }
    if config.effort:
        found["model_reasoning_effort"] = _toml(config.effort)
    for name, url in config.mcp_servers.items():
        found[f"mcp_servers.{name}.url"] = _toml(url)
        found[f"mcp_servers.{name}.bearer_token_env_var"] = _toml(GATEWAY_TOKEN_ENV)
        # The gateway is the task's only way to its connections: no starting without it.
        found[f"mcp_servers.{name}.required"] = "true"
        found[f"mcp_servers.{name}.omit_tools_from"] = _toml(["deferred", "code_mode"])
        found[f"mcp_servers.{name}.default_tools_approval_mode"] = _toml("auto")
        found[f"mcp_servers.{name}.tool_timeout_sec"] = "600"
        tools = [t.split("__", 2)[2] for t in off if t.startswith(f"mcp__{name}__")]
        if tools:
            found[f"mcp_servers.{name}.disabled_tools"] = _toml(sorted(tools))
    found["sandbox_mode"] = _toml("danger-full-access" if writes else "read-only")
    return found


# --- One run ---


@dataclass
class Run:
    """What the turn did, as Ringkeeper reads it."""

    model: str
    texts: list[str] = field(default_factory=list[str])
    steps: int = 0
    responses: int = 0
    usage: dict[str, int] = field(default_factory=dict[str, int])
    question: bool = False
    status: str = ""
    error: dict[str, Any] | None = None
    over: bool = False  # past the step limit


def _assistant(run: Run, content: list[dict[str, Any]]) -> None:
    emit("sdk.assistant", message={
        "_kind": "AssistantMessage", "content": content, "model": run.model,
        "parent_tool_use_id": None, "error": None,
    })  # fmt: skip
    EDITS.message(SimpleNamespace(content=[SimpleNamespace(**b) for b in content]))


def _tool(
    run: Run, ident: str, name: str, args: dict[str, Any], result: object, failed: bool
) -> None:
    run.steps += 1
    _assistant(run, [{"_kind": "ToolUseBlock", "id": ident, "name": name, "input": args}])
    block = {
        "_kind": "ToolResultBlock",
        "tool_use_id": ident,
        "content": result,
        "is_error": failed,
    }
    emit(
        "sdk.user", message={"_kind": "UserMessage", "content": [block], "parent_tool_use_id": None}
    )
    EDITS.saved(SimpleNamespace(content=result))


def _text(items: object) -> list[dict[str, Any]]:
    """MCP or dynamic-tool content, as text blocks."""
    out: list[dict[str, Any]] = []
    for item in cast(list[Any], items) if isinstance(items, list) else []:
        if isinstance(item, dict):
            text = cast(dict[str, Any], item).get("text")
            if isinstance(text, str):
                out.append({"type": "text", "text": text})
    return out


def item_done(run: Run, item: dict[str, Any]) -> None:
    """One finished item, as the events Claude Code's would be."""
    kind, ident = item.get("type"), str(item.get("id") or "")
    if kind == "agentMessage":
        text = str(item.get("text") or "")
        if text:
            run.texts.append(text)
            _assistant(run, [{"_kind": "TextBlock", "text": text}])
    elif kind == "mcpToolCall":
        error = item.get("error")
        result = cast(dict[str, Any], item.get("result") or {})
        content = _text(result.get("content")) or (
            [{"type": "text", "text": json.dumps(result.get("structuredContent"))}]
            if result.get("structuredContent") is not None else []
        )  # fmt: skip
        if isinstance(error, dict):
            content = [{"type": "text", "text": str(cast(dict[str, Any], error).get("message"))}]
        args = item.get("arguments")
        _tool(
            run, ident, f"mcp__{item.get('server')}__{item.get('tool')}",
            cast(dict[str, Any], args) if isinstance(args, dict) else {"arguments": args},
            content, item.get("status") == "failed" or error is not None,
        )  # fmt: skip
    elif kind == "commandExecution":
        output = str(item.get("aggregatedOutput") or "")[-MAX_OUTPUT_CHARS:]
        code = item.get("exitCode")
        failed = item.get("status") in ("failed", "declined") or (
            isinstance(code, int) and code != 0
        )
        _tool(run, ident, "Bash", {"command": str(item.get("command") or "")}, output, failed)
    elif kind == "fileChange":
        failed = item.get("status") in ("failed", "declined")
        for n, change in enumerate(cast(list[dict[str, Any]], item.get("changes") or [])):
            name = "Write" if change.get("kind") == "add" else "Edit"
            said = "Couldn't apply the change." if failed else "Changed."
            _tool(
                run,
                f"{ident}-{n}",
                name,
                {"file_path": str(change.get("path") or "")},
                said,
                failed,
            )
    elif kind == "dynamicToolCall":
        args = item.get("arguments")
        _tool(
            run, ident, f"mcp__{INBOX}__{item.get('tool')}",
            cast(dict[str, Any], args) if isinstance(args, dict) else {},
            _text(item.get("contentItems")), item.get("success") is False,
        )  # fmt: skip
    elif kind == "webSearch":
        _tool(run, ident, "WebSearch", {"query": str(item.get("query") or "")}, "Searched.", False)


class Client:
    """The app-server child: requests out, and messages back one line at a time."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.process = process
        self.next_id = 0

    def send(self, message: dict[str, Any]) -> None:
        """A message to Codex. If it has already exited, there's nobody to tell: its output
        ends too, and the turn ends as stopped (run)."""
        stdin = cast(IO[str], self.process.stdin)
        with contextlib.suppress(OSError):  # a broken pipe
            stdin.write(json.dumps(message) + "\n")
            stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> int:
        self.next_id += 1
        self.send({"id": self.next_id, "method": method, "params": params})
        return self.next_id

    def reply(self, ident: object, result: dict[str, Any]) -> None:
        self.send({"id": ident, "result": result})

    def messages(self) -> Any:
        for line in cast(IO[str], self.process.stdout):
            with contextlib.suppress(ValueError):
                found = json.loads(line)
                if isinstance(found, dict):
                    yield cast(dict[str, Any], found)


def answer_tool(run: Run, params: dict[str, Any]) -> dict[str, Any]:
    """ask_user and not_done, as inbox.py answers them for Claude."""
    tool, args = params.get("tool"), cast(dict[str, Any], params.get("arguments") or {})

    def said(text: str, ok: bool = True) -> dict[str, Any]:
        return {"contentItems": [{"type": "inputText", "text": text}], "success": ok}

    if run.question:
        return said(words.BLOCKED, ok=False)
    if tool == "ask_user":
        question = str(args.get("question") or "").strip()[: words.MAX_QUESTION_CHARS]
        if not question:
            return said("The question is empty.", ok=False)
        run.question = True
        emit("ringkeeper.ask_user", question=question)
        return said(words.ASKED)
    if tool == "not_done":
        reason = str(args.get("reason") or "").strip()[: words.MAX_QUESTION_CHARS]
        if not reason:
            return said("Say why.", ok=False)
        emit(
            "ringkeeper.not_done", reason=reason, needs=str(args.get("needs") or "").strip(),
            done=str(args.get("done") or "").strip()[: words.MAX_QUESTION_CHARS],
        )  # fmt: skip
        return said(words.NOTED)
    return said(f"There's no tool {tool}.", ok=False)


# What nobody's there to answer: an approval is declined (the gateway holds what needs one).
REFUSALS: dict[str, dict[str, Any]] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "mcpServer/elicitation/request": {"action": "decline", "content": None, "_meta": None},
    "item/tool/requestUserInput": {"answers": {}},
}


def command(program: Path) -> list[str]:
    """How the program is started. (Tests start a stand-in app-server instead.)"""
    return [str(program)]


def _start(config: WorkerConfig, home: Path, program: Path) -> subprocess.Popen[str]:
    args = [*command(program), "--listen", "stdio://"]
    for key, value in overrides(config).items():
        args += ["-c", f"{key}={value}"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("RINGKEEPER_TASK")}
    env |= {"CODEX_HOME": str(home), GATEWAY_TOKEN_ENV: config.gateway_token}
    return subprocess.Popen(  # noqa: S603 (the pinned program, fixed arguments)
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=config.workspace, env=env, text=True, encoding="utf-8", errors="replace",
        bufsize=1, **as_worker_kwargs(),
    )  # fmt: skip


def _home() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".ringkeeper-codex")
    home.mkdir(parents=True, exist_ok=True)
    if is_root():
        os.chown(home, WORKER_UID, WORKER_GID)  # type: ignore[attr-defined,unused-ignore]
    return home


def _thread_params(config: WorkerConfig) -> dict[str, Any]:
    return {
        "model": config.model, "cwd": config.workspace, "approvalPolicy": "never",
        "developerInstructions": instructions(config), "dynamicTools": dynamic_tools(),
        "ephemeral": False,
    }  # fmt: skip


class Turn:
    """One task's turn: what Codex says, line by line, and what this process answers."""

    def __init__(self, config: WorkerConfig, client: Client) -> None:
        self.config = config
        self.client = client
        self.run = Run(model=config.model or "")
        self.thread_id = ""
        self.turn_id = ""
        self.pending: dict[int, str] = {}
        given = config.resume or ""
        self.resume = given.removeprefix(RESUME_PREFIX) if given.startswith(RESUME_PREFIX) else ""

    def ask(self, method: str, params: dict[str, Any], what: str) -> None:
        self.pending[self.client.request(method, params)] = what

    def begin(self) -> None:
        self.ask("initialize", {
            "clientInfo": {"name": "ringkeeper", "title": "Ringkeeper", "version": "1"},
            "capabilities": {"experimentalApi": True},
        }, "initialize")  # fmt: skip

    def requested(self, ident: object, method: str, params: dict[str, Any]) -> None:
        """A request from Codex: a tool of ours, or an approval nobody's there to give."""
        if method == "item/tool/call":
            self.client.reply(ident, answer_tool(self.run, params))
        else:
            self.client.reply(ident, REFUSALS.get(method, {}))

    def answered(self, ident: int, message: dict[str, Any]) -> bool:
        """An answer to one of ours. False: it failed, and the turn is over."""
        what = self.pending.pop(ident, "")
        if "error" in message:
            error = cast(dict[str, Any], message["error"])
            self.run.error = {"message": f"{what}: {error.get('message')}", "codexErrorInfo": None}
            return False
        result = cast(dict[str, Any], message.get("result") or {})
        if what == "initialize":
            self.client.send({"method": "initialized", "params": {}})
            params = _thread_params(self.config)
            if self.resume:
                self.ask("thread/resume", {"threadId": self.resume, **params}, "thread")
            else:
                self.ask("thread/start", params, "thread")
        elif what == "thread":
            self.thread_id = str(cast(dict[str, Any], result.get("thread") or {}).get("id") or "")
            text: list[dict[str, Any]] = [
                {"type": "text", "text": self.config.prompt, "text_elements": []}
            ]
            self.ask("turn/start", {"threadId": self.thread_id, "input": text}, "turn")
        elif what == "turn":
            self.turn_id = str(cast(dict[str, Any], result.get("turn") or {}).get("id") or "")
        return True

    def noticed(self, method: str, params: dict[str, Any]) -> bool:
        """A notification. False: the turn is over."""
        run = self.run
        stop = False
        if method == "mcpServer/startupStatus/updated" and params.get("status") == "failed":
            said = str(params.get("error") or "it didn't answer")[:500]
            emit("ringkeeper.warning", message=f"Couldn't reach {params.get('name')}: {said}")
        if method == "item/completed":
            item = cast(dict[str, Any], params.get("item") or {})
            item_done(run, item)
            # After a question the session ends, as inbox.py's hook makes Claude's do.
            stop = run.question and item.get("type") == "dynamicToolCall"
        elif method == "thread/tokenUsage/updated":
            run.responses += 1  # one model turn, as Claude Code counts max_turns
            usage = cast(dict[str, Any], params.get("tokenUsage") or {})
            total = cast(dict[str, Any], usage.get("total") or {})
            run.usage = {
                "input_tokens": int(total.get("inputTokens") or 0),
                "output_tokens": int(total.get("outputTokens") or 0),
                "cache_read_input_tokens": int(total.get("cachedInputTokens") or 0),
            }
            if run.responses > self.config.max_turns and not run.over:
                run.over = stop = True
        elif method == "turn/completed":
            turn = cast(dict[str, Any], params.get("turn") or {})
            run.status = str(turn.get("status") or "")
            if isinstance(turn.get("error"), dict):
                run.error = cast(dict[str, Any], turn["error"])
            return False
        if stop and self.turn_id:
            interrupt = {"threadId": self.thread_id, "turnId": self.turn_id}
            self.ask("turn/interrupt", interrupt, "interrupt")
        return True

    def handle(self, message: dict[str, Any]) -> bool:
        """One message from Codex. False: the turn is over."""
        method, ident = message.get("method"), message.get("id")
        params = cast(dict[str, Any], message.get("params") or {})
        if method is not None and ident is not None:
            self.requested(ident, str(method), params)
            return True
        if ident is not None:
            return self.answered(cast(int, ident), message)
        return self.noticed(str(method), params)


def run(config: WorkerConfig, program: Path) -> bool:
    """Runs the task's turn. True if it finished (or asked a question)."""
    process = _start(config, _home(), program)

    def errors() -> None:
        for line in cast(IO[str], process.stderr):
            emit("sdk.stderr", line=line.rstrip())

    threading.Thread(target=errors, daemon=True).start()
    turn = Turn(config, Client(process))
    turn.begin()
    try:
        for message in turn.client.messages():
            if not turn.handle(message):
                break
        if not turn.run.status and turn.run.error is None:
            turn.run.error = {
                "message": "Codex stopped before the task finished.",
                "codexErrorInfo": None,
            }
    finally:
        with contextlib.suppress(OSError):
            process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            process.wait(10)
    return _result(turn.run, turn.thread_id)


def _result(run: Run, thread_id: str) -> bool:
    asked = run.question and run.status in ("completed", "interrupted")
    ok = (run.status == "completed" or asked) and not run.over
    subtype = "success" if ok else "error_max_turns" if run.over else "error_during_execution"
    error = run.error or {}
    said = [str(error["message"])] if error.get("message") else []
    emit("sdk.result", message={
        "_kind": "ResultMessage", "subtype": subtype, "is_error": not ok,
        "num_turns": run.responses,
        # Prefixed: a Claude worker given it starts afresh instead (agent.py).
        "session_id": f"{RESUME_PREFIX}{thread_id}" if thread_id else None,
        "total_cost_usd": None,  # the plan isn't billed by the token
        "usage": run.usage, "model_usage": {run.model: run.usage} if run.model else {},
        "result": run.texts[-1] if run.texts else "", "errors": said,
        "api_error_status": None, "model": run.model, "engine": ENGINE,
        "engine_error": error.get("codexErrorInfo"),  # services.chatgpt.engine_failure
    })  # fmt: skip
    return ok
