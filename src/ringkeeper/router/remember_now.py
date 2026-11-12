"""'Remember that ...': when the project is clear, the note is kept straight away (the same
filter and place as an agent's `remember`), and no task starts. When it isn't clear, the message
is routed as usual."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.models import Message, Project
from ringkeeper.router import follow_up
from ringkeeper.services import mentions

ASKED = re.compile(r"^\s*(?:please\s+)?remember(?:\s+that\b|\s*:)\s*(?P<note>.+)$", re.I | re.S)


def note_in(body: str) -> str | None:
    """The note a "remember that ..." message asks to keep. Not a question ("remember that
    file?"), and not "remember to ...", which asks for something later."""
    found = ASKED.match(body)
    if found is None:
        return None
    note = found["note"].strip()
    if not note or note.endswith("?"):
        return None
    return note[0].upper() + note[1:]


def project_for(session: Session, message: Message, earlier: list[str]) -> str | None:
    """The one project this note is for, or None when that isn't clear: the project chosen at
    the top, the one project the message names or @-mentions, the one project this
    conversation's work is in, or the only project there is."""
    if message.target_project:
        return message.target_project
    named = mentions.find(session, message.body)
    if named:
        return named.projects[0] if len(set(named.projects)) == 1 and not named.clients else None
    projects, clients = follow_up.named_in(session, message.body)
    if projects or clients:
        return projects[0] if len(projects) == 1 and not clients else None
    if earlier:
        return earlier[0] if len(set(earlier)) == 1 else None
    every = session.scalars(select(Project.name)).all()
    return every[0] if len(every) == 1 else None


def said(title: str, kept: dict[str, str]) -> str:
    where = "in Notion" if kept["where"] == "Notion" else "here in Ringkeeper"
    return f"Saved to {title}'s memory, {where}: “{kept['note']}”"
