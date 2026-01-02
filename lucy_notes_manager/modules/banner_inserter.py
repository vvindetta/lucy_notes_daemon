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

    def modified(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        """
        Insert an ASCII banner below the first line if --banner is present.

        Returns:
            List[str] | None:
                - List of file paths to ignore next (written files)
                - None if nothing changed

        Examples:
            --banner Hello
            --banner date
        """
        known_args, _ = parse_args(args=args, template=BannerInserter.template)
        banner_vals = known_args.get("banner")
        if not banner_vals:
            return None

        banner_text = banner_vals[0]
        if banner_text == "date":
            banner_text = str(date.today())

        src_path = getattr(event, "src_path", None)
        if not src_path:
            return None

        try:
            with open(src_path, "r+", encoding="utf-8") as file:
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

                # Remove --banner from first line
                first = lines[0] if lines else ""
                cleaned = clean_args_from_line(first, flags=["--banner"]).rstrip("\n")
                lines[0] = cleaned + "\n"

                # Rewrite file
                file.seek(0)
                file.truncate()
                file.writelines(lines)

                return [src_path]

        except FileNotFoundError:
            # File might have been moved or deleted
            return None
