from datetime import date
from typing import List

import pyfiglet
from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import clean_args_from_line, parse_args
from lucy_notes_manager.modules.abstract_module import AbstractModule


class BannerInserter(AbstractModule):
    name: str = "banner"
    priority: int = 5

    template = (("--banner", str),)

    def modified(self, args: List[str], event: FileSystemEvent) -> bool:
        """
        Insert an ASCII banner below the first line if --banner is present.

        Examples:
            --banner Hello
            --banner date
        """

        known_args, _ = parse_args(args=args, template=BannerInserter.template)
        banner_text = known_args.get("banner")
        if not banner_text:
            return False
        banner_text = banner_text[0]

        if banner_text == "date":
            banner_text = str(date.today())

        try:
            with open(event.src_path, "r+", encoding="utf-8") as file:
                lines = file.readlines()

                # --- Build banner block ---
                ascii_banner = pyfiglet.figlet_format(banner_text).rstrip() + "\n"
                banner_block = ["---\n", "\n", ascii_banner]

                # Insert banner under the first line
                if len(lines) >= 2:
                    lines[1:2] = banner_block
                elif len(lines) == 1:
                    lines.extend(banner_block)
                else:
                    lines = ["\n"] + banner_block

                # Remove -banner from first line
                lines[0] = clean_args_from_line(lines[0], flags=["--banner"]) + "\n"

                # Rewrite file
                file.seek(0)
                file.truncate()
                file.writelines(lines)

                return True

        except FileNotFoundError:
            # File might have been moved or deleted
            return False
