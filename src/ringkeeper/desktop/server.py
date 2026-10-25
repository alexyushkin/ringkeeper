"""The desktop backend: one process, 127.0.0.1 only, no sign-in, still locked.

`run()` starts the API (with the session manager and router) and the gateway
on random local ports, and writes `desktop.json` in the data folder for the
app shell: the address and a per-launch secret. Nothing else in it.

The front door (`FrontDoor`) is the only thing listening for the app:

- it answers only on its own address (127.0.0.1 / localhost and its port),
  so a web page can't reach it through DNS rebinding (421 otherwise);
- the shell trades the launch secret for a one-time start code
  (POST /desktop/codes), and opens the window at /desktop/start?code=...
  (&next=/a/page to land there); that sets an httpOnly, SameSite=Strict
  cookie for this launch;
- with the same secret the shell reaches the API (/desktop/api/...: the
  tray's status, pausing) and stops Ringkeeper (POST /desktop/shutdown);
- /bff/... is the API, for a request carrying that cookie, and for changes
  also the CSRF header (the web app's own scheme) and our own Origin;
- everything else is the web app's static files, if there are any.

A website open in the user's browser can't get the secret or the cookie, so
it can't drive Ringkeeper. Other programs running as the same user could
read desktop.json, but they could read the database too: on a desktop the
user's own programs are inside the trust boundary.
"""

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from collections.abc import Callable, Generator
from http.cookies import SimpleCookie
from pathlib import Path
from typing import IO, Any
from urllib.parse import unquote

import uvicorn
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ringkeeper.api.auth import DESKTOP_MARK

logger = logging.getLogger(__name__)

RUNTIME_FILE = "desktop.json"
LOCK_FILE = "desktop.lock"
SESSION_COOKIE = "rk_desktop"
CSRF_COOKIE = "rk_csrf"
CSRF_HEADER = b"x-csrf-token"
CODE_SECONDS = 60
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Never passed on to the API: credentials are the front door's business.
DROPPED_HEADERS = frozenset(
    {b"authorization", b"cookie", b"x-ringkeeper-session", b"x-ringkeeper-client"}
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
        "'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; media-src 'self' "
        "blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}


class AlreadyRunningError(Exception):
    pass


class Launch:
    """This launch's secrets: the shell's secret, the window cookie, start codes."""

    def __init__(self) -> None:
        self.secret = secrets.token_urlsafe(32)  # for the shell, via desktop.json
        self.session = secrets.token_urlsafe(32)  # the window's cookie
        self.csrf = secrets.token_urlsafe(24)
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()

    def new_code(self) -> str:
        code = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + CODE_SECONDS
        return code

    def take_code(self, code: str) -> bool:
        with self._lock:
            expires = self._codes.pop(code, None)
        return expires is not None and expires > time.monotonic()

    def is_secret(self, presented: str) -> bool:
        return hmac.compare_digest(presented.encode(), self.secret.encode())


def _headers(scope: Scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers", [])}


def _cookies(headers: dict[bytes, bytes]) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    with contextlib.suppress(Exception):
        jar.load(headers.get(b"cookie", b"").decode("latin-1"))
    return {name: morsel.value for name, morsel in jar.items()}


def _local_path(path: str) -> str:
    """Where to go after /desktop/start: a page of this app, never another site."""
    if path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/"


def _same(a: str | None, b: str) -> bool:
    return a is not None and hmac.compare_digest(a.encode(), b.encode())


class FrontDoor:
    def __init__(
        self,
        api: ASGIApp,
        launch: Launch,
        port: int,
        web_dir: Path | None,
        shutdown: Callable[[], None] | None = None,
    ) -> None:
        self.api = api
        self.launch = launch
        self.shutdown = shutdown
        self.web_dir = web_dir.resolve() if web_dir else None
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":  # the API's: it runs the manager and router
            await self.api(scope, receive, send)
            return
        if scope["type"] != "http":
            return
        headers = _headers(scope)
        if headers.get(b"host", b"").decode("latin-1").lower() not in self.hosts:
            await self._plain(421, "This address isn't one Ringkeeper answers on.")(
                scope, receive, send
            )
            return
        path: str = scope["path"]
        method: str = scope["method"].upper()
        if path == "/desktop/health":
            response: Response = JSONResponse({"status": "ok"})
        elif path == "/desktop/codes" and method == "POST":
            response = self._codes() if self._from_shell(headers) else self._no_secret()
        elif path == "/desktop/shutdown" and method == "POST":
            response = self._shutdown() if self._from_shell(headers) else self._no_secret()
        elif path.startswith("/desktop/api/"):
            if self._from_shell(headers):
                await self.api(self._to_api(scope, path, "/desktop/api"), receive, send)
                return
            response = self._no_secret()
        elif path == "/desktop/start" and method == "GET":
            response = self._start(scope)
        elif path == "/bff" or path.startswith("/bff/"):
            refusal = self._refuse_bff(method, headers)
            if refusal is None:
                await self.api(self._to_api(scope, path, "/bff"), receive, send)
                return
            response = refusal
        else:
            response = self._static(path)
        await response(scope, receive, send)

    # --- The shell and the window ---

    def _from_shell(self, headers: dict[bytes, bytes]) -> bool:
        auth = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, presented = auth.partition(" ")
        return scheme.lower() == "bearer" and self.launch.is_secret(presented.strip())

    @staticmethod
    def _no_secret() -> Response:
        return JSONResponse({"detail": "the launch secret is required"}, status_code=401)

    def _codes(self) -> Response:
        return JSONResponse({"code": self.launch.new_code()})

    def _shutdown(self) -> Response:
        if self.shutdown is None:
            return JSONResponse({"detail": "this instance can't be stopped this way"}, 409)
        self.shutdown()
        return JSONResponse({"stopping": True}, status_code=202)

    def _start(self, scope: Scope) -> Response:
        query = dict(
            part.split("=", 1) for part in scope.get("query_string", b"").decode().split("&")
            if "=" in part
        )  # fmt: skip
        if not self.launch.take_code(query.get("code", "")):
            return self._plain(403, "This link has expired. Open Ringkeeper again.")
        response = RedirectResponse(_local_path(unquote(query.get("next", ""))), status_code=303)
        response.set_cookie(
            SESSION_COOKIE, self.launch.session, httponly=True, samesite="strict", path="/"
        )
        response.set_cookie(
            CSRF_COOKIE, self.launch.csrf, httponly=False, samesite="strict", path="/"
        )
        return response

    def _refuse_bff(self, method: str, headers: dict[bytes, bytes]) -> Response | None:
        cookies = _cookies(headers)
        if not _same(cookies.get(SESSION_COOKIE), self.launch.session):
            return JSONResponse({"detail": "open Ringkeeper from its app window"}, 401)
        if method in SAFE_METHODS:
            return None
        origin = headers.get(b"origin", b"").decode("latin-1")
        if origin and origin not in self.origins:
            return JSONResponse({"detail": "not from Ringkeeper's window"}, 403)
        csrf = headers.get(CSRF_HEADER, b"").decode("latin-1")
        if not (
            _same(cookies.get(CSRF_COOKIE), self.launch.csrf) and _same(csrf, self.launch.csrf)
        ):
            return JSONResponse({"detail": "missing or wrong CSRF token"}, 403)
        return None

    @staticmethod
    def _to_api(scope: Scope, path: str, prefix: str) -> Scope:
        inner = path.removeprefix(prefix) or "/"
        state = dict(scope.get("state") or {})
        state[DESKTOP_MARK] = True
        return {
            **scope,
            "path": inner,
            "raw_path": inner.encode(),
            "headers": [(k, v) for k, v in scope["headers"] if k.lower() not in DROPPED_HEADERS],
            "state": state,
        }

    # --- The web app's files ---

    def _static(self, path: str) -> Response:
        if self.web_dir is None:
            return self._plain(404, "Ringkeeper is running. Open it from its app window.")
        relative = path.lstrip("/")
        for candidate in (relative, f"{relative}.html", f"{relative}/index.html".lstrip("/")):
            target = (self.web_dir / candidate).resolve()
            if target.is_file() and target.is_relative_to(self.web_dir):
                return FileResponse(target, headers=SECURITY_HEADERS)
        missing = self.web_dir / "404.html"
        if missing.is_file():
            return FileResponse(missing, status_code=404, headers=SECURITY_HEADERS)
        return self._plain(404, "Not found.")

    @staticmethod
    def _plain(status: int, text: str) -> Response:
        return Response(text, status_code=status, media_type="text/plain", headers=SECURITY_HEADERS)


# --- Running it ---


def _listen() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    return sock


@contextlib.contextmanager
def single_instance(data_dir: Path) -> Generator[None]:
    """Hold an exclusive lock on the data folder while running."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle: IO[str] = (data_dir / LOCK_FILE).open("a+", encoding="utf-8")
    try:
        try:
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415

                handle.seek(0)
                getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)  # noqa: B009
            else:
                import fcntl  # noqa: PLC0415

                flock: Any = getattr(fcntl, "flock")  # noqa: B009
                flock(handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB"))  # noqa: B009
        except OSError:
            raise AlreadyRunningError(f"Ringkeeper is already running for {data_dir}") from None
        yield
    finally:
        handle.close()


def _write_runtime(data_dir: Path, info: dict[str, Any]) -> Path:
    path = data_dir / RUNTIME_FILE
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(info, handle)
    os.replace(tmp, path)
    return path


def prepare_environment(data_dir: Path, gateway_url: str) -> None:
    """Desktop settings for this process (Settings is read from the environment)."""
    from ringkeeper.desktop import paths  # noqa: PLC0415

    defaults = {
        "RINGKEEPER_WORKSPACES_ROOT": str(paths.workspaces_dir()),
        "RINGKEEPER_STT_URL": "",  # voice is an optional download, off until then
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
    os.environ |= {
        "RINGKEEPER_MODE": "desktop",
        "RINGKEEPER_DATA_DIR": str(data_dir),
        "RINGKEEPER_SESSION_MANAGER": "1",
        "RINGKEEPER_ROUTER": "1",
        "RINGKEEPER_WORKER_RUNTIME": "process",
        "RINGKEEPER_GATEWAY_INTERNAL_URL": gateway_url,
    }


def run(
    data_dir: Path,
    *,
    web_dir: Path | None = None,
    ready: threading.Event | None = None,
    stop: threading.Event | None = None,
) -> None:
    """Run until interrupted (or `stop` is set). Writes desktop.json once both listen."""
    with single_instance(data_dir):
        api_sock, gateway_sock = _listen(), _listen()
        api_port, gateway_port = api_sock.getsockname()[1], gateway_sock.getsockname()[1]
        prepare_environment(data_dir, f"http://127.0.0.1:{gateway_port}")

        from ringkeeper import bootstrap, migrate  # noqa: PLC0415 (after the environment is set)
        from ringkeeper.api.app import create_app  # noqa: PLC0415
        from ringkeeper.config import get_settings  # noqa: PLC0415
        from ringkeeper.gateway.app import create_app as create_gateway  # noqa: PLC0415

        settings = get_settings()
        migrate.upgrade(settings.database_url)
        bootstrap.run(settings)  # the keychain's master key, before the gateway needs it
        launch = Launch()
        stopping = stop or threading.Event()  # set by the caller or the shell
        front = FrontDoor(create_app(), launch, api_port, web_dir, shutdown=stopping.set)
        gateway = uvicorn.Server(
            uvicorn.Config(create_gateway(), log_level="warning", lifespan="on")
        )
        # The window's live-updates stream stays open; don't let it hold up Quit.
        api = uvicorn.Server(
            uvicorn.Config(front, log_level="warning", lifespan="on", timeout_graceful_shutdown=3)
        )
        gateway_thread = threading.Thread(
            target=lambda: asyncio.run(gateway.serve(sockets=[gateway_sock])),
            daemon=True, name="rk-gateway",
        )  # fmt: skip
        gateway_thread.start()
        runtime: Path | None = None

        async def watch_stop() -> None:
            while not stopping.is_set():
                await asyncio.sleep(0.1)
            api.should_exit = True

        async def serve() -> None:
            nonlocal runtime
            task = asyncio.create_task(api.serve(sockets=[api_sock]))
            watcher = asyncio.create_task(watch_stop())
            while not (api.started and gateway.started):
                if task.done():
                    break
                await asyncio.sleep(0.05)
            if api.started:
                runtime = _write_runtime(
                    data_dir,
                    {"url": f"http://127.0.0.1:{api_port}", "secret": launch.secret,
                     "pid": os.getpid()},
                )  # fmt: skip
                logger.info("Ringkeeper is running at http://127.0.0.1:%d", api_port)
                if ready is not None:
                    ready.set()
            await task
            watcher.cancel()

        try:
            asyncio.run(serve())
        finally:
            gateway.should_exit = True
            gateway_thread.join(10)
            if runtime is not None:
                runtime.unlink(missing_ok=True)
