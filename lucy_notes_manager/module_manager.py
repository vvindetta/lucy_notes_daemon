import logging
from typing import Dict, List

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib.args import (
    Template,
    get_args_from_file,
    merge_known_args,
    parse_args,
)
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System

logger = logging.getLogger(__name__)


class ModuleManager:
    def __init__(self, modules: List[AbstractModule], args):
        self.modules = modules
        self.template: Template = [
            (
                "--force",
                str,
                [],
                "Force-enable modules by name even if they are excluded. "
                "Example: --force git todo",
            ),
            (
                "--exclude",
                str,
                [],
                "Disable modules by name. Can be overridden per module via --force. "
                "Example: --exclude git todo",
            ),
            (
                "--sys-priority",
                str,
                None,
                "Override module execution order (lower runs first). "
                "Format: name=int. Example: --sys-priority banner=5 renamer=20 todo=30",
            ),
            (
                "--sys-use_only_first_line",
                bool,
                False,
                "If true, parse module arguments only from the first line of the file (faster, but ignores flags below).",
            ),
        ]

        for module in self.modules:
            self.template.extend(module.template)

        self.config, _ = parse_args(args=args, template=self.template)

        priority_dict = self._parse_priority_list(self.config.get("sys_priority") or [])
        self.modules.sort(key=lambda m: priority_dict.get(m.name, m.priority))

    def run(self, path: str, event: FileSystemEvent) -> Dict[str, int] | None:
        def _update_config():
            known_args, _, arg_lines = get_args_from_file(
                path=path,
                template=self.template,
                only_first_line=self.config["sys_use_only_first_line"],
            )
            merged_known_args = merge_known_args(
                args=self.config, overwrite_args=known_args
            )
            return merged_known_args, arg_lines

        config, arg_lines = _update_config()

        ignore_paths: Dict[str, int] = {}

        for module in self.modules:
            if (
                module.name in self.config["exclude"]
                and module.name not in self.config["force"]
            ):
                continue

            if event.event_type not in module.__class__.__dict__:  # not from parent
                continue

            action = getattr(module, event.event_type)

            logger.info(f"STARTING: {module.name}")
            event_ignore = action(
                Context(
                    path=path,
                    config=config,
                    arg_lines=arg_lines,
                ),
                System(
                    event=event,
                    global_template=self.template,
                    modules=self.modules,
                ),
            )
            logger.info(f"END: {module.name}")

            if event_ignore:
                for path, times in event_ignore.items():
                    if not times:
                        continue
                    ignore_paths[path] = ignore_paths.get(path, 0) + int(times)

                config, arg_lines = _update_config()

        return ignore_paths or None

    def _parse_priority_list(self, values: List[str]) -> Dict[str, int]:
        """
        Values example: ["banner=5", "renamer=20", "todo=30"]

        Returns: {"banner": 5, "renamer": 20, "todo": 30}
        """
        priorities: Dict[str, int] = {}

        if not values:
            return priorities

        for item in values:
            if "=" not in item:
                raise ValueError(
                    "Invalid --priority arg. Example: --priority banner=5 renamer=20 todo=30"
                )

            name, raw = item.split("=", 1)
            name = name.strip()
            raw = raw.strip()

            if not name:
                raise ValueError(f"Invalid --priority item '{item}': empty module name")

            try:
                pr = int(raw)
            except ValueError:
                raise ValueError(
                    f"Invalid --priority item '{item}': priority must be an integer"
                )

            priorities[name] = pr

        return priorities
