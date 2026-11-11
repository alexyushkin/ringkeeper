"""How every tool that reads a file answers: Gmail, Google, a mailbox, Microsoft and storage.

Three things can be done with a file. Its text is read (adapters.extract): a long one first
as its outline, then page by page (`pages=`), so one PDF doesn't fill a task's context. A page
or a picture is shown as an image (`view`), which keeps the layout of a table or a chart.
And a copy is saved in the project's folder, under attachments/ (where files dropped on a
request go too), for the task to work on; it then counts as one of the task's files.
"""

import re
from functools import partial
from pathlib import Path
from typing import Annotated

import anyio.to_thread
from pydantic import Field

from ringkeeper.adapters import extract
from ringkeeper.adapters.base import IMAGE, JSON, ToolError
from ringkeeper.adapters.paging import PAGE_CHARS, page

OUTLINE_CHARS = 30_000  # a file longer than this starts with its outline
FIRST_PAGES = 2  # shown with the outline
MOST_SAVED = 1000  # copies of one name in the attachments folder
PAGES = r"^\s*\d{1,4}\s*(-\s*\d{1,4})?\s*$"
Pages = Annotated[
    str | None,
    Field(
        pattern=PAGES,
        description="Pages (or slides, sheets) to read, like 3 or 3-6: next_pages says which "
        "come next.",
    ),
]
PageNumber = Annotated[int, Field(ge=1, le=extract.MAX_PDF_PAGES, description="Page or slide.")]
PictureNumber = Annotated[
    int | None,
    Field(ge=1, le=1000, description="A picture on the page by number; leave out for the page."),
]
Inside = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=500,
        description="A file inside this one: its path in a .zip, or an attached email's "
        "attachment by number.",
    ),
]
OUTLINE_NOTE = (
    "A long file: this is its outline and its first {first}. Read the {unit}s you need with "
    "pages= (next_pages comes next) before answering, or say your answer is based on part of it."
)
MORE_PAGES = "More follows: read on with pages=next_pages, or say your answer is based on part."
LONG_NOTE = (
    "A long file: this is its first {shown:,} of {total:,} characters. Read on from next_offset "
    "before answering, or say your answer is based on part of it."
)


def _range(pages: str, count: int, unit: str) -> tuple[int, int]:
    if not re.fullmatch(PAGES, pages):  # a vendor operation's value isn't checked by a schema
        raise ToolError("pages= takes a page or a range of them, like 3 or 3-6")
    first, _, last = pages.replace(" ", "").partition("-")
    start, end = int(first), int(last or first)
    if start > end:
        start, end = end, start
    if start > count:
        raise ToolError(f"it has {count} {unit}{'' if count == 1 else 's'}")
    return start, min(end, count)


def _next(rest: list[extract.Page]) -> str | None:
    """The pages to read next: as many as fit in about one outline's worth of text."""
    if not rest:
        return None
    size, last = 0, rest[0].number
    for item in rest:
        if size and size + len(item.text) > OUTLINE_CHARS:
            break
        size += len(item.text)
        last = item.number
    return str(last) if last == rest[0].number else f"{rest[0].number}-{last}"


def _pictures(found: extract.Extracted, see: str) -> str | None:
    """Where the pictures are, and how to look at them."""
    unit = found.unit
    pictured = [p.number for p in found.pages if p.pictures]
    if pictured and found.kind == "pdf":
        return f"Pages {extract.spans(pictured)} have pictures: {see} shows a page as an image."
    if pictured:
        return (
            f"{unit.title()}s {extract.spans(pictured)} have pictures: {see} shows one (page= "
            "the slide, picture= its number)."
        )
    if found.pictures:
        many = "s" if found.pictures > 1 else ""
        return f"It has {found.pictures} picture{many}: {see} shows them."
    return None


def text_answer(
    meta: JSON, found: extract.Extracted, offset: int, limit: int, pages: str | None, see: str
) -> JSON:
    """A file's text as a tool answers it. `see` names the tool that shows its pictures."""
    answer = dict(meta)
    if said := _pictures(found, see):
        answer["pictures"] = said
    unit, total = found.unit, len(found.pages)
    if pages:
        if not found.pages:
            raise ToolError(f"'{meta.get('name')}' has no pages: read on with offset instead")
        start, end = _range(pages, found.pages[-1].number, unit)
        chosen = [p for p in found.pages if start <= p.number <= end]
        answer |= page(extract.joined(chosen, unit, total), offset, limit)
        answer[f"{unit}s_shown"] = extract.spans([p.number for p in chosen])
        answer[f"{unit}s"] = total
        if following := _next([p for p in found.pages if p.number > end]):
            answer |= {"next_pages": following, "truncated": True, "note": MORE_PAGES}
    elif offset == 0 and limit == PAGE_CHARS and len(found.text) > OUTLINE_CHARS:
        # The first answer for a long file: its outline, or its start.
        if total > FIRST_PAGES:
            first = found.pages[:FIRST_PAGES]
            answer["outline"] = outline(found)
            answer |= page(extract.joined(first, unit, total), 0, OUTLINE_CHARS)
            answer |= {
                f"{unit}s_shown": extract.spans([p.number for p in first]),
                "next_pages": _next(list(found.pages[FIRST_PAGES:])),
                "truncated": True,
                "note": OUTLINE_NOTE.format(first=f"{FIRST_PAGES} {unit}s", unit=unit),
            }
        else:
            answer |= page(found.text, 0, OUTLINE_CHARS)
            answer["note"] = LONG_NOTE.format(shown=OUTLINE_CHARS, total=len(found.text))
    else:
        answer |= page(found.text, offset, limit)
    if found.note:
        answer["read_note"] = found.note
    return answer


def outline(found: extract.Extracted) -> JSON:
    """What a long file holds, page by page, in a few lines."""
    unit = found.unit
    numbers = [p.number for p in found.pages]
    worded = [p.number for p in found.pages if p.text]
    shown: JSON = {f"{unit}s": len(numbers), "total_chars": len(found.text)}
    shown[f"{unit}s_with_text"] = extract.spans(worded) or "none"
    if blank := [n for n in numbers if n not in set(worded)]:
        shown[f"{unit}s_without_text"] = extract.spans(blank)
    if pictured := [p.number for p in found.pages if p.pictures]:
        shown[f"{unit}s_with_pictures"] = extract.spans(pictured)
    if titles := {str(p.number): p.title for p in found.pages[:200] if p.title}:
        shown["titles"] = titles
    return shown


def picture_answer(meta: JSON, shown: extract.Shown) -> JSON:
    """A page or a picture, as a tool shows it: what it is, and the picture itself (IMAGE),
    which the gateway sends as an image."""
    image = shown.image
    return (
        dict(meta)
        | shown.where
        | {"shown_as": image.mime, "width": image.width, "height": image.height}
        | {"bytes": len(image.data), IMAGE: image}
    )


def _root(project: str) -> Path:
    from ringkeeper.config import get_settings  # noqa: PLC0415 (no cycle at import)
    from ringkeeper.db import session_scope  # noqa: PLC0415
    from ringkeeper.services import projects  # noqa: PLC0415
    from ringkeeper.workers.manager import resolve_workspace  # noqa: PLC0415 (a heavy import)

    with session_scope(commit=False) as session:
        folder = projects.get_project(session, project).workspace_path
    try:
        return Path(resolve_workspace(folder, get_settings().workspaces_root))
    except Exception as error:
        raise ToolError(str(error)) from None


def _saved(project: str | None, name: str, data: bytes) -> JSON:
    from ringkeeper.services import attachments  # noqa: PLC0415 (no cycle at import)

    if project is None:
        raise ToolError("a file is saved in a project's folder, and this call has no project")
    if len(data) > attachments.MAX_BYTES:
        raise ToolError(f"'{name}' is too large to save here ({len(data):,} bytes)")
    root = _root(project)
    if not root.is_dir():
        raise ToolError(
            "this project's folder can't be reached from here (a server's gateway doesn't see "
            "projects' folders): read or view the file instead"
        )
    folder = root / attachments.FOLDER
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise ToolError("the project's attachments folder isn't a plain folder")
    folder.mkdir(exist_ok=True)
    clean = attachments.clean_name(name)
    stem, suffix = Path(clean).stem, Path(clean).suffix
    path = folder / clean
    for number in range(1, MOST_SAVED):
        path = folder / (clean if number == 1 else f"{stem}-{number}{suffix}")
        if path.is_symlink():
            continue
        if not path.exists():
            path.write_bytes(data)
            break
        if path.is_file() and path.stat().st_size == len(data) and path.read_bytes() == data:
            break  # saved already
    else:
        raise ToolError(f"there are too many copies of '{clean}' in the attachments folder")
    return {
        "saved_to": f"{attachments.FOLDER}/{path.name}",
        "size": len(data),
        "note": "Saved in the project's folder: work on it from that path. It's listed with "
        "this task's files.",
    }


async def save(project: str | None, name: str, data: bytes) -> JSON:
    """A copy of the file in the calling project's attachments folder, and its path from the
    folder the task works in."""
    return await anyio.to_thread.run_sync(partial(_saved, project, name, data))


def suffixed(name: str, suffix: str) -> str:
    """A name that ends as a file of that kind does."""
    return name if name.lower().endswith(suffix) else re.sub(r"\s+$", "", name) + suffix
