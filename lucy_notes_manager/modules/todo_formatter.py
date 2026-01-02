import os
import re
from typing import List

from watchdog.events import FileSystemEvent

from lucy_notes_manager.modules.abstract_module import AbstractModule


class TodoFormatter(AbstractModule):
    name: str = "todo"
    priority: int = 20

    template = (("--todo", str),)

    def created(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        if "--todo" not in args:
            return None
        return self._convert_to_checklist(str(event.src_path))

    def modified(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        if "--todo" not in args:
            return None
        return self._convert_to_checklist(str(event.src_path))

    def _convert_to_checklist(self, path: str) -> List[str] | None:
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

        # Match lines starting with "- " (or indented), but not already checklist:
        # "- [ ] ..." or "- [x] ...".
        pattern = re.compile(r"^(\s*)-\s+(?!\[[ xX]\])(.+)$")

        new_lines: List[str] = []

        for original_line in lines:
            # Preserve newline style
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

        return [os.path.abspath(path)]
