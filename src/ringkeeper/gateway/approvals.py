"""Run held writes that were approved from the API or Telegram.

Those answers only record the approval. The gateway is the service that can
reach the instances, so it claims each one and runs it here, through the
same re-check and audit path as `ringkeeper decision approve`.
"""

import logging
import threading

import anyio
import anyio.to_thread

from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.listen import start_listener
from ringkeeper.services import calls, inbox
from ringkeeper.vault import Vault

logger = logging.getLogger(__name__)
POLL_SECONDS = 10.0


def _pending() -> list[tuple[str, str]]:
    with session_scope() as session:
        return [
            (str(d.id), str((d.answer or {}).get("by", "unknown")))
            for d in inbox.pending_approvals(session)
        ]


async def run_pending(vault: Vault) -> int:
    """Run every approved-but-not-run held write. Returns how many were tried."""
    pending = await anyio.to_thread.run_sync(_pending)
    for ident, by in pending:
        try:
            decision = await calls.approve(ident, by=by, vault=vault)
            logger.info("held write %s approved by %s: %s", ident, by, decision.status)
        except RingkeeperError as error:  # expired or already claimed: recorded either way
            logger.warning("held write %s was not run: %s", ident, error)
    return len(pending)


async def approval_loop(vault: Vault, settings: Settings, stop: threading.Event) -> None:
    wake = threading.Event()
    start_listener(
        settings, [inbox.APPROVALS_CHANNEL], lambda _c, _p: wake.set(), stop, "rk-approvals"
    )
    while not stop.is_set():
        try:
            await run_pending(vault)
        except Exception:
            logger.exception("approval pass failed")
        await anyio.to_thread.run_sync(wake.wait, POLL_SECONDS, abandon_on_cancel=True)
        wake.clear()
