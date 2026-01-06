from abc import ABC
from typing import List

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import Template


class AbstractModule(ABC):
    """
    Base interface for all processing modules.

    Every module optionally handles events: created, modified, moved, deleted.
    """

    """Unique module identifier (e.g. 'banner')."""
    name: str

    """
    Execution priority.
    Lower numbers run first. Default for all modules is 20.
    """
    priority: int = 20

    """
    CLI-style flags this module understands.

    Template example:
        [
            ("--rename", str, None),
            ("--banner", str, ["date"]),
        ]
    """
    template: Template = []

    def created(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ) -> List[str] | None:
        """
        Called when a file is created.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None

    def modified(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ) -> list[str] | None:
        """
        Called when a file is modified.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None

    def moved(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ) -> List[str] | None:
        """
        Called when a file is moved.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None

    def deleted(
        self, *, path: str, event: FileSystemEvent, config: dict, arg_lines: dict
    ) -> List[str] | None:
        """
        Called when a file is deleted.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None
