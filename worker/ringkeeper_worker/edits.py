"""What the agent itself changed: the files its edit tools wrote, and whether it ran a shell
command (which can change any file). A code task commits only these files; anything else
that changed in the folder during the run was someone else's (an editor, another program)
and isn't swept onto the task's branch unnoticed."""

from dataclasses import dataclass, field
from typing import Any, cast

EDIT_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}  # fmt: skip
SHELL_TOOLS = {"Bash", "BashOutput"}


@dataclass
class Edits:
    paths: set[str] = field(default_factory=set[str])  # as the tools gave them
    shell: bool = False

    def tool(self, name: str, args: dict[str, Any]) -> None:
        if name in SHELL_TOOLS:
            self.shell = True
        elif (key := EDIT_TOOLS.get(name)) and isinstance(path := args.get(key), str):
            self.paths.add(path)

    def message(self, message: object) -> None:
        """An SDK message: its tool calls, if it's the assistant's."""
        content = getattr(message, "content", None)
        blocks = cast(list[object], content) if isinstance(content, list) else []
        for block in blocks:
            name, args = getattr(block, "name", None), getattr(block, "input", None)
            if isinstance(name, str) and isinstance(args, dict):
                self.tool(name, cast(dict[str, Any], args))


EDITS = Edits()  # this worker run's
