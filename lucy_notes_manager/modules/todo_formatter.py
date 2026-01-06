import os
import re
from typing import List, Optional

from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class TodoFormatter(AbstractModule):
    name: str = "todo"
    priority: int = 10

    template = [
        ("--todo", bool, False),
    ]

    def _apply(
        self, *, path: str, config: dict, arg_lines: dict
    ) -> Optional[IgnoreMap]:
        todo_vals = config["todo"]
        if not todo_vals:
            return None

        if not os.path.isfile(path):
            return None

        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext != "md":
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                original_text = f.read()
        except (OSError, UnicodeDecodeError):
            return None

        lines = original_text.splitlines(keepends=True)
        changed = False

        pattern = re.compile(r"^(\s*)-\s+(?!\[[ xX]\])(.+)$")

        new_lines: List[str] = []

        for original_line in lines:
            if original_line.endswith("\r\n"):
                newline = "\r\n"
                line = original_line[:-2]
            elif original_line.endswith("\n"):
                newline = "\n"
                line = original_line[:-1]
            else:
                newline = ""
                line = original_line

            match = pattern.match(line)
            if not match:
                new_lines.append(original_line)
                continue

            indent, content = match.groups()
            new_line = f"{indent}- [ ] {content}{newline}"
            new_lines.append(new_line)
            if new_line != original_line:
                changed = True

        if not changed:
            return None

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except OSError:
            return None

        return {os.path.abspath(path): 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config, arg_lines=ctx.arg_lines)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config, arg_lines=ctx.arg_lines)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config, arg_lines=ctx.arg_lines)
