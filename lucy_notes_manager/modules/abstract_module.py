from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Dict, List, Optional

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import Template

IgnoreMap = Dict[str, int]


@dataclass(frozen=True)
class System:
    """
    Runtime system info.

    - event: watchdog event that triggered the run
    - global_template: full args template used by ModuleManager
    - modules: ordered module instances in the pipeline
    """

    event: FileSystemEvent
    global_template: Template
    modules: List["AbstractModule"]


@dataclass(frozen=True)
class Context:
    """
    Module input for one run.

    - path: absolute file path (event.src_path; for moved = event.dest_path)
    - config: resolved args for this file (global + file flags; includes defaults)
    - arg_lines: arg -> 1-based line numbers where it appeared in the file
    - system: runtime info (see class System)
    """

    path: str
    config: dict
    arg_lines: dict


class AbstractModule(ABC):
    """
    Base interface for all processing modules.

    Every module optionally handles events: created, modified, moved, deleted.

    Return value
    - None:
        No filesystem changes were made.

    - {'path1': 1, 'path2', 3, ...}:
        Filesystem paths WAS changed N times by this module.
        The daemon will ignore the next events for these paths to prevent loops.

    Prioriry
    - 'priority': lower runs earlier.

    Template:
    - 'template': flags this module adds to the global argument template.

    - example:
        [
            ("--flag", type, ["default value"], "manual string"),
            ("--rename", str, None, "Will rename file),
            ("--banner", str, ["date"], "Draws ASCII banner),
        ]
    """

    name: str
    priority: int = 15
    template: Template = []

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def on_opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None
