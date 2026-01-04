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

    def created(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        """
        Called when a file is created.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None

    def modified(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        """
        Called when a file is modified.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None

    def moved(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        """
        Called when a file is moved.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None

    def deleted(self, args: List[str], event: FileSystemEvent) -> List[str] | None:
        """
        Called when a file is deleted.
        May return List of file paths to ignore. Did func change something in file?
        """
        return None
