import os
from typing import List

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import parse_args
from lucy_notes_manager.modules.abstract_module import AbstractModule


class Renamer(AbstractModule):
    name = "renamer"
    priority = 10

    template = (("--r", str),)

    def modified(self, args: List[str], event: FileSystemEvent) -> bool:
        known_args, _ = parse_args(Renamer.template, args)
        if not known_args.get("r"):
            return False

        new_name = known_args["r"]

        old_path = event.src_path
        dir_path = os.path.dirname(old_path)
        new_path = os.path.join(dir_path, new_name[0])

        if old_path == new_path:
            return False

        if os.path.isdir(new_path):
            return False

        try:
            os.rename(old_path, new_path)
            return False
        except FileNotFoundError:
            return False
        except OSError:
            return False
