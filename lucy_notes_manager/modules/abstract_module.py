from abc import ABC
from typing import List, Tuple

from watchdog.events import FileSystemEvent


class AbstractModule(ABC):
    """
    Base interface for all processing modules.

    Each module:
    - has a unique name
    - has a numeric priority (lower = earlier execution)
    - optionally handles events: created, modified, moved, deleted
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

    Example:
        return (
            ("-banner", str),
            ("-rename", str),
        )
    """
    template: Tuple[Tuple[str, type], ...] = ()

    def created(self, args: List[str], event: FileSystemEvent) -> bool:
        """
        Called when a file is created.
        Return True/False == Did func change something in file?
        """
        return False

    def modified(self, args: List[str], event: FileSystemEvent) -> bool:
        """
        Called when a file is modified.
        Return True/False == Did func change something in file?
        """
        return False

    def moved(self, args: List[str], event: FileSystemEvent) -> bool:
        """
        Called when a file is moved.
        Return True/False == Did func change something in file?
        """
        return False

    def deleted(self, args: List[str], event: FileSystemEvent) -> bool:
        """
        Called when a file is deleted.
        Return True/False == Did func change something in file?
        """
        return False
