from __future__ import annotations

from datetime import date
from typing import List

import pyfiglet
from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import delete_args_from_string
from lucy_notes_manager.modules.abstract_module import AbstractModule


class BannerInserter(AbstractModule):
    name: str = "banner"
    priority: int = 5

    template = [
        ("--banner", str, None),
        ("--separator", str, ["---"]),
    ]

    def _apply(self, *, path: str, config: dict, arg_lines: dict) -> List[str] | None:
        banner_vals = config["banner"]
        if not banner_vals:
            return None

        banner_text = str(banner_vals[0]).strip()
        if banner_text == "date":
            banner_text = str(date.today())

        sep = str(config["separator"][0]).strip()
        sep_line = sep + ("\n" if not sep.endswith("\n") else "")

        lineno_1based = arg_lines["banner"][0]

        with open(path, "r+", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                lines = ["\n"]

            idx = max(0, min(len(lines) - 1, lineno_1based - 1))

            ascii_banner = pyfiglet.figlet_format(banner_text).rstrip("\n") + "\n"

            if idx == 0:
                lines[0] = delete_args_from_string(lines[0], ["--banner"])

                if lines[0].strip():
                    lines.insert(1, "\n")
                    insert_pos = 2
                else:
                    lines[0] = "\n"
                    insert_pos = 1

                # separator only BEFORE, not after
                lines[insert_pos:insert_pos] = [sep_line, ascii_banner]

            else:
                cleaned = delete_args_from_string(lines[idx], ["--banner"])

                # no separators in middle, banner starts on same line number
                lines[idx : idx + 1] = [ascii_banner]

                if cleaned.strip():
                    lines[idx + 1 : idx + 1] = [cleaned]

            f.seek(0)
            f.truncate()
            f.writelines(lines)

        return [path]

    def created(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ):
        return self._apply(path=path, config=config, arg_lines=arg_lines)

    def modified(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ):
        return self._apply(path=path, config=config, arg_lines=arg_lines)

    def moved(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ):
        return self._apply(path=path, config=config, arg_lines=arg_lines)
