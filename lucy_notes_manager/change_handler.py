import logging
import os
from typing import List, Optional, Tuple

from watchdog.events import FileSystemEventHandler

from lucy_notes_manager.lib.args import (
    get_args_from_first_file_line,
    merge_args,
    parse_args,
)
from lucy_notes_manager.modules.abstract_module import AbstractModule

logger = logging.getLogger(__name__)


class ChangeHandler(FileSystemEventHandler):
    def __init__(
        self,
        modules: List[Tuple[int, AbstractModule]],
        args: Optional[List[str]] = None,
    ):
        self.modules = [m for _, m in sorted(modules, key=lambda x: x[0])]
        self.template = (
            ("--force", str),
            ("--exclude", str),
        )

        if args:
            self.system_args, self.modules_args = parse_args(
                args=args, template=self.template
            )
            logging.info(f"\n {args}")

        self._ignore_paths: set[str] = set()

    def _mark_to_ignore(self, input_path: str):
        input_path = os.path.abspath(input_path)

        self._ignore_paths.add(input_path)
        logger.info(f"Ivent: {input_path} marked to IGNORE")
        return False

    def _check_and_delete_ignore(self, input_path: str) -> bool:
        input_path = os.path.abspath(input_path)

        if input_path in self._ignore_paths:
            self._ignore_paths.remove(input_path)
            logger.info(f"Ivent: {input_path} IGNORED")
            return True
        return False

    def _process_file(self, event):
        if event.is_directory or os.path.basename(event.src_path).startswith("."):
            return

        file_path = event.dest_path if event.event_type == "moved" else event.src_path

        abs_path = os.path.abspath(file_path)
        if ".git" in abs_path.split(os.sep):
            return

        if self._check_and_delete_ignore(input_path=file_path):
            return

        if event.event_type == "moved":
            logger.info(f"Moved: {event.src_path} → {event.dest_path}")
        else:
            logger.info(f"{str(event.event_type).capitalize()}: {event.src_path}")

        event_known_file_args, event_unknown_file_args = get_args_from_first_file_line(
            path=file_path, template=self.template
        )

        event_system_merged_args = merge_args(
            args=self.system_args,
            overwrite_args=event_known_file_args,
        )
        event_modules_args = self.modules_args + event_unknown_file_args

        force_modules = event_system_merged_args.get("force") or []
        exclude_modules = event_system_merged_args.get("exclude") or []

        for module in self.modules:
            if module.name in exclude_modules and module.name not in force_modules:
                continue

            method = getattr(module, event.event_type, None)
            if method:
                result = method(args=event_modules_args, event=event)

                if result:
                    logger.info(f"Module {module.name} change something.")
                    self._mark_to_ignore(file_path)

    def on_modified(self, event):
        self._process_file(event=event)

    def on_created(self, event):
        self._process_file(event=event)

    def on_moved(self, event):
        self._process_file(event=event)

    def on_deleted(self, event):
        for module in self.modules:
            module.deleted(event=event, args=self.modules_args)
