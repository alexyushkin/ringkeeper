from typing import Annotated

import typer

from ringkeeper.cli.common import handle_errors
from ringkeeper.db import session_scope
from ringkeeper.services import projects, revocations
from ringkeeper.services.decisions import current_user
from ringkeeper.tokens import issue_token, parse_ttl

app = typer.Typer(help="Issue worker tokens for the gateway.", no_args_is_help=True)


@app.command("issue")
def issue(
    project: Annotated[str, typer.Option(help="Project the worker acts for.")],
    ttl: Annotated[str, typer.Option(help="Lifetime, e.g. 30m, 1h, 2d.")] = "1h",
    session: Annotated[
        str | None, typer.Option(help="Session id to embed (default: a new uuid).")
    ] = None,
) -> None:
    """Print a signed worker token. Only the token goes to stdout."""
    with handle_errors():
        with session_scope() as db:
            projects.get_project(db, project)
        token, claims = issue_token(project, parse_ttl(ttl), session_id=session)
    typer.echo(token)
    typer.echo(
        f"token for {claims.project}, session {claims.session_id}, jti {claims.token_id}, "
        f"expires {claims.expires_at.isoformat()}",
        err=True,
    )


@app.command("revoke")
def revoke(
    jti: Annotated[str, typer.Argument(help="The token's jti (shown by `token issue`).")],
    reason: Annotated[str, typer.Option(help="Recorded with the revocation.")] = "revoked by CLI",
) -> None:
    """Revoke a worker token. The gateway refuses it from the next request on."""
    with handle_errors(), session_scope() as db:
        added = revocations.revoke(db, jti, reason=reason, by=current_user())
    typer.echo(f"revoked {jti}" if added else f"{jti} was already revoked")
