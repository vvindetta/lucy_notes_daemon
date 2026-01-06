import logging
import os
from typing import Dict, List

from watchdog.events import FileSystemEventHandler

from lucy_notes_manager.module_manager import ModuleManager

logger = logging.getLogger(__name__)


class FileHandler(FileSystemEventHandler):
    def __init__(
        self,
        modules: ModuleManager,
    ):
        self._ignore_paths: Dict[str, int] = {}
        self.modules = modules

    def _process_file(self, event):
        if event.is_directory or os.path.basename(event.src_path).startswith("."):
            return

        file_path = event.dest_path if event.event_type == "moved" else event.src_path

        file_path = os.path.abspath(file_path)
        if any(part == ".git" for part in file_path.split(os.sep)):
            return

        if event.event_type == "moved":
            if self._check_and_delete_ignore(
                event.src_path
            ) or self._check_and_delete_ignore(event.dest_path):
                return
            logger.info(f"EVENT: Moved: {event.src_path} → {event.dest_path}")
        else:
            if self._check_and_delete_ignore(event.src_path):
                return
            logger.info(
                f"EVENT: {str(event.event_type).capitalize()}: {event.src_path}"
            )

        ignore_paths = self.modules.run(path=file_path, event=event)
        if ignore_paths:
            self._mark_to_ignore(ignore_paths=ignore_paths)

        logging.info("--- END ---\n\n")

    def _mark_to_ignore(self, ignore_paths: List[str]) -> None:
        for path in ignore_paths:
            abs_path = os.path.abspath(path)
            self._ignore_paths[abs_path] = self._ignore_paths.get(abs_path, 0) + 1

            logger.info(
                "MARKED TO IGNORE: %s (count=%d)",
                abs_path,
                self._ignore_paths[abs_path],
            )

    def _check_and_delete_ignore(self, input_path: str) -> bool:
        input_path = os.path.abspath(input_path)

        count = self._ignore_paths.get(input_path, 0)
        if count <= 0:
            return False

        if count == 1:
            del self._ignore_paths[input_path]
        else:
            self._ignore_paths[input_path] = count - 1

        logger.info(
            "IGNORED: %s (remaining=%d)\n\n",
            input_path,
            self._ignore_paths.get(input_path, 0),
        )
        return True

    def on_modified(self, event):
        self._process_file(event=event)

    def on_created(self, event):
        self._process_file(event=event)

    def on_moved(self, event):
        self._process_file(event=event)

    def on_deleted(self, event):
        self._process_file(event=event)
