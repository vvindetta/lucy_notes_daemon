import os
from typing import List

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import parse_args
from lucy_notes_manager.modules.abstract_module import AbstractModule


class Renamer(AbstractModule):
    name: str = "renamer"
    priority: int = 10

    template = (("--r", str),)

    def modified(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        known_args, _ = parse_args(Renamer.template, args)
        r_vals = known_args.get("r")
        if not r_vals:
            return None

        # parse_args returns List[...] for each key
        new_name_raw = r_vals[0]
        if not isinstance(new_name_raw, str) or not new_name_raw.strip():
            return None

        old_path = getattr(event, "src_path", None)
        if not old_path:
            return None

        old_path = os.path.abspath(str(old_path))
        dir_path = os.path.dirname(old_path)
        new_path = os.path.abspath(os.path.join(dir_path, new_name_raw.strip()))

        if old_path == new_path:
            return None

        # Prevent renaming into an existing directory
        if os.path.isdir(new_path):
            return None

        try:
            os.rename(old_path, new_path)

            # We changed filesystem. We want to ignore the follow-up events
            # produced by this rename. Return BOTH paths to ignore.
            return [old_path, new_path]

        except FileNotFoundError:
            return None
        except OSError:
            return None
