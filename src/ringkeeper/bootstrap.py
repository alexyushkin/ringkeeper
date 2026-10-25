"""First start without a terminal: what the api sets up before the web app can.

Runs when the api service starts (it is the only service with a writable
data dir besides the CLI):

1. the master key (data_dir/master.key), unless RINGKEEPER_MASTER_KEY is set;
2. the API admin token and the web token in the vault (the web service gets
   the web token through the supervisor's token file);
3. while there is no owner yet, the one-time setup code: printed to the log
   and written to data_dir/setup-code.txt. The web app's wizard asks for it
   before creating the owner, so whoever reaches a freshly exposed server
   first can't claim it. It is deleted once the owner exists.

Files are handed to RINGKEEPER_DATA_UID (the gateway's user) when set.
"""

import logging
import os
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from ringkeeper.admin_token import (
    generate_api_token,
    generate_web_token,
    stored_api_token,
    stored_web_token,
)
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.keys import key_file_path, load_master_key
from ringkeeper.services import owner
from ringkeeper.vault import get_vault

logger = logging.getLogger(__name__)

SETUP_CODE_FILE = "setup-code.txt"


def setup_code_path(settings: Settings) -> Path:
    return settings.data_dir / SETUP_CODE_FILE


def _hand_over(settings: Settings, path: Path) -> None:
    if settings.data_uid is not None and sys.platform != "win32" and path.exists():
        os.chown(path, settings.data_uid, -1)


WEAK_PASSWORDS = frozenset({"ringkeeper", "postgres", "change-me-to-32-random-letters-and-digits"})


def warn_weak_database_password(settings: Settings) -> None:
    password = make_url(settings.database_url).password or ""
    if password in WEAK_PASSWORDS or len(password) < 16:
        logger.warning(
            "the database password is weak or the default: set POSTGRES_PASSWORD in .env "
            "(docs/INSTALL.md, 'Change the database password')"
        )


def ensure_master_key(settings: Settings) -> None:
    load_master_key(settings, create=True)
    if settings.master_key is None:
        _hand_over(settings, key_file_path(settings))


def ensure_admin_token(settings: Settings) -> None:
    if stored_api_token() is None:
        get_vault(settings).store(settings.api_token_ref, generate_api_token())
        logger.info("created the API admin token (see `ringkeeper auth api-token`)")
    if stored_web_token() is None:
        get_vault(settings).store(settings.web_token_ref, generate_web_token())


def ensure_setup_code(settings: Settings, *, announce: bool = True) -> str | None:
    """The setup code while no owner exists (reused across restarts); None afterwards.

    `announce` logs it (the api's start); the CLI prints it itself instead.
    """
    path = setup_code_path(settings)
    with session_scope() as session:
        if owner.has_owner(session):
            path.unlink(missing_ok=True)
            return None
        code = None
        if path.exists():
            saved = path.read_text(encoding="utf-8").strip()
            if owner.setup_code_matches(session, saved):
                code = saved
        if code is None:
            code = owner.new_setup_code()
            owner.store_setup_code(session, code)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(code + "\n")
            _hand_over(settings, path)
    if announce:
        logger.warning(
            "Ringkeeper setup code: %s  (open the web app to finish setup; the code is also "
            "in %s in Ringkeeper's data folder, .ringkeeper)",
            code, path.name,
        )  # fmt: skip
    return code


def clear_setup_code(settings: Settings) -> None:
    setup_code_path(settings).unlink(missing_ok=True)


def run(settings: Settings) -> None:
    """Each step on its own: a read-only data dir (CLI-managed installs) is fine."""
    warn_weak_database_password(settings)
    for step in (ensure_master_key, ensure_admin_token, ensure_setup_code):
        try:
            step(settings)
        except (RingkeeperError, OSError) as error:
            logger.warning("setup step %s skipped: %s", step.__name__, error)
