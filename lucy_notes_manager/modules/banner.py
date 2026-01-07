from __future__ import annotations

from datetime import date
from typing import Optional

import pyfiglet

from lucy_notes_manager.lib.args import Template, delete_args_from_string
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Banner(AbstractModule):
    name: str = "banner"
    priority: int = 10

    template: Template = [
        (
            "--banner",
            str,
            None,
            "Insert an ASCII banner (pyfiglet) at the line where the flag appears. "
            "Use '--banner date' to insert today's date. Example: --banner 'LOL' or --banner date.",
        ),
        (
            "--banner-separator",
            str,
            ["---"],
            "Separator line inserted before the banner when the banner is placed at the top of the file. "
            "Example: --banner-separator '---' (default).",
        ),
    ]

    def _apply(
        self, *, path: str, config: dict, arg_lines: dict
    ) -> Optional[IgnoreMap]:
        banner_vals = config["banner"]
        if not banner_vals:
            return None

        banner_text = str(banner_vals[0]).strip()
        if banner_text == "date":
            banner_text = str(date.today())

        sep = str(config["banner_separator"][0]).strip()
        sep_line = sep + ("\n" if not sep.endswith("\n") else "")

        lineno_1based = arg_lines["banner"][0]

        with open(path, "r+", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines:
                lines = ["\n"]

            idx = max(0, min(len(lines) - 1, lineno_1based - 1))

            ascii_lines = pyfiglet.figlet_format(banner_text).splitlines(
                True
            )  # keep '\n'

            while ascii_lines and ascii_lines[0].strip() == "":
                ascii_lines.pop(0)

            while ascii_lines and ascii_lines[-1].strip() == "":
                ascii_lines.pop()

            if ascii_lines and not ascii_lines[-1].endswith("\n"):
                ascii_lines[-1] += "\n"

            if not ascii_lines:
                return None

            if idx == 0:
                lines[0] = delete_args_from_string(lines[0], ["--banner"])

                if lines[0].strip():
                    lines.insert(1, "\n")
                    insert_pos = 2
                else:
                    lines[0] = "\n"
                    insert_pos = 1

                lines[insert_pos:insert_pos] = [sep_line] + ascii_lines
            else:
                cleaned = delete_args_from_string(lines[idx], ["--banner"])

                lines[idx : idx + 1] = ascii_lines

                if cleaned.strip():
                    lines[idx + len(ascii_lines) : idx + len(ascii_lines)] = [cleaned]

            f.seek(0)
            f.truncate()
            f.writelines(lines)

        return {path: 1}

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config, arg_lines=ctx.arg_lines)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config, arg_lines=ctx.arg_lines)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config, arg_lines=ctx.arg_lines)
