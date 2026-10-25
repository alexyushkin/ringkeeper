"""Voice upload and live updates for the web app.

- POST /voice/transcribe: a recording from the browser (webm from Chrome and
  Android, mp4 from iOS Safari), sent to the stt service and dropped. It is
  only ever in memory here; the response is the transcript alone. The app
  shows "I heard: ..." and sends the text only after you confirm it.
- GET /events: server-sent events for task status, transcript lines,
  approvals and routed messages (ids only; the app fetches the rest). They
  come from Postgres NOTIFY triggers, so every writer is covered. The
  session is checked again at every heartbeat (a stream ends soon after
  sign-out), and only a few streams may be open at once.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from psycopg import sql

from ringkeeper.api.auth import AdminDep
from ringkeeper.api.deps import OwnerToken, require_owner
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.services import owner
from ringkeeper.services.runtime import current_settings
from ringkeeper.voice import SttClient, VoiceError, megabytes, vocabulary

router = APIRouter(dependencies=[AdminDep, Depends(require_owner)])

EVENTS_CHANNEL = "ringkeeper_events"
HEARTBEAT_SECONDS = 15.0
MAX_STREAMS = 10  # one per open tab; each holds a database connection and a thread
_streams = {"open": 0}
AUDIO_TYPES = ("audio/", "video/webm", "video/mp4", "application/octet-stream")


@router.post("/voice/transcribe")
async def transcribe(
    request: Request,
    content_type: Annotated[str, Header()] = "application/octet-stream",
    content_length: Annotated[int | None, Header()] = None,
) -> dict[str, str]:
    settings = current_settings()
    limit = settings.stt_max_bytes
    if not content_type.lower().startswith(AUDIO_TYPES):
        raise HTTPException(415, detail="send the recording as audio")
    if content_length is not None and content_length > limit:
        raise HTTPException(
            413, detail=f"That recording is too big; the limit is {megabytes(limit)}."
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            chunks.clear()
            raise HTTPException(
                413, detail=f"That recording is too big; the limit is {megabytes(limit)}."
            )
        chunks.append(chunk)
    audio = b"".join(chunks)
    chunks.clear()
    if not audio:
        raise HTTPException(422, detail="The recording is empty.")
    try:
        with session_scope(commit=False) as session:
            hint = vocabulary(session)
        stt = SttClient(settings)
        try:
            text = await asyncio.to_thread(stt.transcribe, audio, hint)
        finally:
            stt.close()
    except VoiceError as error:
        raise HTTPException(422, detail=str(error)) from None
    finally:
        del audio  # in memory only, and gone now
    return {"transcript": text}


def _next_payloads(conn: psycopg.Connection[tuple[object, ...]]) -> list[str]:
    """Block until a notification arrives (returning it right away) or the heartbeat is due."""
    return [n.payload for n in conn.notifies(timeout=HEARTBEAT_SECONDS, stop_after=1)]


def _still_signed_in(token: str) -> bool:
    try:
        with session_scope() as session:
            owner.verify_session(session, get_settings(), token)
    except owner.AuthError:
        return False
    return True


async def _notifications(request: Request, token: str) -> AsyncIterator[str]:
    # A blocking connection waited on in a worker thread: psycopg's async mode
    # can't use the Windows event loop, and one stream per signed-in browser is
    # a handful of threads at most.
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    _streams["open"] += 1
    try:
        conn = await asyncio.to_thread(psycopg.connect, url, autocommit=True)
        try:
            conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(EVENTS_CHANNEL)))
            yield "retry: 3000\n\n"
            checked = time.monotonic()
            while not await request.is_disconnected():
                payloads = await asyncio.to_thread(_next_payloads, conn)
                if time.monotonic() - checked >= HEARTBEAT_SECONDS:
                    if not await asyncio.to_thread(_still_signed_in, token):
                        return  # signed out or expired: the browser reconnects and gets a 401
                    checked = time.monotonic()
                if not payloads:
                    # A comment keeps proxies from closing a quiet stream, and lets us
                    # notice a browser that went away.
                    yield ": keep-alive\n\n"
                for payload in payloads:
                    try:
                        kind = str(json.loads(payload).get("type", "message"))
                    except ValueError:
                        continue
                    yield f"event: {kind}\ndata: {payload}\n\n"
        finally:
            await asyncio.to_thread(conn.close)
    finally:
        _streams["open"] -= 1


@router.get("/events")
async def events(request: Request, token: OwnerToken) -> StreamingResponse:
    if _streams["open"] >= MAX_STREAMS:
        raise HTTPException(
            429, detail="too many open live views; close some tabs", headers={"Retry-After": "30"}
        )
    return StreamingResponse(
        _notifications(request, token),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
