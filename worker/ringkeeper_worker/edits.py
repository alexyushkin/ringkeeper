"""What the agent itself changed: the files its edit tools wrote (or a gateway tool saved for
it, in attachments/), and whether it ran a shell command (which can change any file). A code
task commits only these files; anything else that changed in the folder during the run was
someone else's (an editor, another program) and isn't swept onto the task's branch
unnoticed."""

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, cast

EDIT_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}  # fmt: skip
SHELL_TOOLS = {"Bash", "BashOutput"}
# What a gateway tool that saved a file answers (adapters.files), and the folder it saves in.
SAVED_KEY, SAVED_FOLDER = "saved_to", "attachments"


@dataclass
class Edits:
    paths: set[str] = field(default_factory=set[str])  # as the tools gave them
    shell: bool = False

    def tool(self, name: str, args: dict[str, Any]) -> None:
        if name in SHELL_TOOLS:
            self.shell = True
        elif (key := EDIT_TOOLS.get(name)) and isinstance(path := args.get(key), str):
            self.paths.add(path)

    def saved(self, result: object) -> None:
        """A tool result that saved a file in the project for the task to work on (a gateway's
        save_attachment): the file is the task's own, like one its edit tools wrote. Only a
        path in the attachments folder counts."""
        content = getattr(result, "content", None)
        items = cast(list[object], content) if isinstance(content, list) else [content]
        for item in items:
            text = cast(dict[str, Any], item).get("text") if isinstance(item, dict) else item
            if not isinstance(text, str) or SAVED_KEY not in text:
                continue
            try:
                found: Any = json.loads(text)
            except ValueError:
                continue
            fields = cast(dict[str, Any], found) if isinstance(found, dict) else {}
            path = fields.get(SAVED_KEY)
            parts = PurePosixPath(path).parts if isinstance(path, str) else ()
            if len(parts) == 2 and parts[0] == SAVED_FOLDER and ".." not in parts:
                self.paths.add(f"{SAVED_FOLDER}/{parts[1]}")

    def message(self, message: object) -> None:
        """An SDK message: its tool calls, if it's the assistant's, and the files the tools
        saved for it."""
        content = getattr(message, "content", None)
        blocks = cast(list[object], content) if isinstance(content, list) else []
        for block in blocks:
            name, args = getattr(block, "name", None), getattr(block, "input", None)
            if isinstance(name, str) and isinstance(args, dict):
                self.tool(name, cast(dict[str, Any], args))
            elif getattr(block, "tool_use_id", None) is not None:
                self.saved(block)


EDITS = Edits()  # this worker run's
