import logging
import os
from typing import Dict

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
            if self._check_and_delete_ignore(file_path):
                return
            logger.info(
                f"EVENT: {str(event.event_type).capitalize()}: {event.src_path}"
            )

        ignore_paths = self.modules.run(path=file_path, event=event)
        if ignore_paths:
            self._mark_to_ignore(ignore_paths=ignore_paths)

        logging.info("--- END ---\n\n")

    def _mark_to_ignore(self, ignore_paths: Dict[str, int]) -> None:
        for path, count in ignore_paths.items():
            new_count = self._bump_ignore(path, count)
            logger.info("MARKED TO IGNORE: %s (count=%d)", self._abs(path), new_count)

    def _check_and_delete_ignore(self, input_path: str) -> bool:
        cur = self._ignore_paths.get(self._abs(input_path), 0)
        if cur <= 0:
            return False

        remaining = self._bump_ignore(input_path, -1)
        logger.info("IGNORED: %s (remaining=%d)\n\n", self._abs(input_path), remaining)
        return True

    def _bump_ignore(self, path: str, delta: int) -> int:
        """
        Apply +delta (can be negative) to ignore counter for path.
        Removes key when counter reaches 0.
        Returns new counter value (0 if removed).
        """
        abs_path = self._abs(path)
        cur = self._ignore_paths.get(abs_path, 0)
        new = cur + int(delta)

        if new <= 0:
            if abs_path in self._ignore_paths:
                del self._ignore_paths[abs_path]
            return 0

        self._ignore_paths[abs_path] = new
        return new

    def _abs(self, p: str) -> str:
        return os.path.abspath(p)

    def on_modified(self, event):
        self._process_file(event=event)

    def on_created(self, event):
        self._process_file(event=event)

    def on_moved(self, event):
        self._process_file(event=event)

    def on_deleted(self, event):
        self._process_file(event=event)

    # def on_opened(self, event):
    #     self._process_file(event=event)
