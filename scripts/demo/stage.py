"""The set for the demo recording: a scratch backend, two made-up clients and a stand-in for
Telegram. Started by record.py with a clean environment; what it needs comes on stdin.

The stand-in answers the Bot API on this computer and shows the chat as a small page, so the
approval on camera goes through Ringkeeper's real Telegram bridge. Nothing here reaches Telegram.
"""

import json
import logging
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

TOKEN = "123456:demo-bot-token-not-a-real-one-abcdef"  # noqa: S105 (a made-up one)
CHAT = 1001
STAGE = "stage.json"
STOP = "stop"
START_SECONDS = 120
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Telegram (simulated)</title>
<style>
body{margin:0;height:100vh;display:grid;place-items:center;background:#e9e6df;
font:20px/1.45 system-ui,sans-serif;color:#1c1c1e}
#phone{width:460px;height:880px;background:#dfe7ef;border-radius:44px;
border:12px solid #1c1c1e;overflow:hidden;display:flex;flex-direction:column}
#top{background:#fff;padding:22px;font-weight:600;text-align:center}
#top small{display:block;font-weight:400;color:#6b6b70;font-size:15px}
#chat{flex:1;padding:16px;overflow:auto;display:flex;flex-direction:column;gap:12px;
justify-content:flex-end}
.m{background:#fff;border-radius:16px;padding:12px 15px;white-space:pre-wrap;overflow-wrap:anywhere}
.b{display:flex;gap:8px;margin-top:10px}
button{flex:1;padding:12px;border:0;border-radius:10px;background:#3478c6;color:#fff;
font:600 18px system-ui;cursor:pointer}
</style></head><body><div id="phone"><div id="top">Ringkeeper bot<small>simulated for this demo
</small></div><div id="chat"></div></div><script>
async function draw(){const r=await fetch('/messages');const all=await r.json();
const c=document.getElementById('chat');c.innerHTML='';
for(const m of all.slice(-2)){const d=document.createElement('div');d.className='m';
d.textContent=m.text;
if(m.buttons.length){const b=document.createElement('div');b.className='b';
for(const x of m.buttons){const k=document.createElement('button');k.textContent=x.text;
k.onclick=()=>fetch('/press',{method:'POST',body:JSON.stringify({id:m.id,data:x.data})});
b.append(k)}d.append(b)}c.append(d)}}
setInterval(draw,600);draw()</script></body></html>"""


class FakeTelegram:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.updates: deque[dict[str, Any]] = deque()
        self.messages: list[dict[str, Any]] = []
        self.next_update = 1
        self.next_message = 100

    def _sent(self, body: dict[str, Any]) -> dict[str, Any]:
        rows = (body.get("reply_markup") or {}).get("inline_keyboard") or []
        buttons = [{"text": b["text"], "data": b.get("callback_data")} for row in rows for b in row]
        with self.lock:
            self.next_message += 1
            ident = self.next_message
            self.messages.append({"id": ident, "text": body.get("text", ""), "buttons": buttons})
        return {"message_id": ident, "chat": {"id": CHAT}, "date": int(time.time())}

    def _edited(self, body: dict[str, Any]) -> bool:
        with self.lock:
            for message in self.messages:
                if message["id"] == body.get("message_id"):
                    message["text"] = body.get("text", message["text"])
                    message["buttons"] = []
        return True

    def call(self, method: str, body: dict[str, Any]) -> object:
        if method == "getMe":
            return {"id": 9, "is_bot": True, "username": "ringkeeper_demo_bot"}
        if method == "getUpdates":
            with self.lock:
                offset = body.get("offset") or 0
                while self.updates and self.updates[0]["update_id"] < offset:
                    self.updates.popleft()
                waiting = list(self.updates)
            if not waiting:
                time.sleep(0.5)
            return waiting
        if method == "sendMessage":
            return self._sent(body)
        if method == "editMessageText":
            return self._edited(body)
        return True

    def press(self, ident: int, data: str) -> None:
        with self.lock:
            self.next_update += 1
            self.updates.append(
                {
                    "update_id": self.next_update,
                    "callback_query": {
                        "id": str(self.next_update),
                        "from": {"id": CHAT, "is_bot": False, "first_name": "You"},
                        "message": {"message_id": ident, "chat": {"id": CHAT}},
                        "data": data,
                    },
                }
            )


def serve(fake: FakeTelegram) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send(self, body: bytes, kind: str = "application/json") -> None:
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/messages":
                with fake.lock:
                    self._send(json.dumps(fake.messages).encode())
            else:
                self._send(PAGE.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            data: dict[str, Any] = json.loads(body or b"{}")
            if self.path == "/press":
                fake.press(int(data["id"]), str(data["data"]))
                self._send(b"{}")
                return
            method = self.path.rsplit("/", 1)[-1]
            self._send(json.dumps({"ok": True, "result": fake.call(method, data)}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="demo-telegram").start()
    return server


def main() -> int:
    import os  # noqa: PLC0415

    folder = Path(sys.argv[1]).resolve()
    web = Path(sys.argv[2]).resolve()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    job: dict[str, Any] = json.loads(sys.stdin.read())

    fake = FakeTelegram()
    bot = serve(fake)
    port = bot.server_address[1]
    data = folder / "data"
    os.environ |= {
        "RINGKEEPER_DATA_DIR": str(data),
        "RINGKEEPER_TELEGRAM": "true",
        "RINGKEEPER_TELEGRAM_CHAT_IDS": json.dumps([CHAT]),
        "RINGKEEPER_TELEGRAM_API_URL": f"http://127.0.0.1:{port}",
        "RINGKEEPER_TELEGRAM_POLL_SECONDS": "1",
    }

    from ringkeeper.claude_auth import credential_ref  # noqa: PLC0415
    from ringkeeper.config import get_settings  # noqa: PLC0415
    from ringkeeper.db import session_scope  # noqa: PLC0415
    from ringkeeper.desktop import server  # noqa: PLC0415
    from ringkeeper.models import AuthMode  # noqa: PLC0415
    from ringkeeper.scenarios import world  # noqa: PLC0415
    from ringkeeper.vault import get_vault  # noqa: PLC0415

    settings = get_settings()
    vault = get_vault(settings)
    vault.store(settings.telegram_bot_token_ref, TOKEN)
    vault.store(credential_ref(settings, AuthMode(job["mode"])), job["secret"])

    ready, stop = threading.Event(), threading.Event()
    backend = threading.Thread(
        target=server.run, args=(data,), kwargs={"ready": ready, "stop": stop, "web_dir": web},
        daemon=True, name="demo-backend",
    )  # fmt: skip
    backend.start()
    if not ready.wait(START_SECONDS):
        return 1
    with session_scope() as session:
        world.build(session, get_settings(), get_vault(), {})
    info = json.loads((data / server.RUNTIME_FILE).read_text(encoding="utf-8"))
    (folder / STAGE).write_text(
        json.dumps(
            {"url": info["url"], "secret": info["secret"], "chat": f"http://127.0.0.1:{port}/"}
        ),
        encoding="utf-8",
    )
    while not (folder / STOP).exists():
        time.sleep(0.5)
    stop.set()
    backend.join(30)
    bot.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
