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
                if not lines:
                    lines = ["\n"]

                ascii_banner = pyfiglet.figlet_format(banner_text).rstrip() + "\n"
                banner_block = ["---\n", "\n", ascii_banner]

                # Insert banner right after the first line (do not overwrite existing content)
                lines[1:1] = banner_block

                # Remove --banner (and its values) from the first line
                lines[0] = clean_args_from_line(lines[0], flags=["--banner"])

                file.seek(0)
                file.truncate()
                file.writelines(lines)

                return [src_path]

        except FileNotFoundError:
            return None
