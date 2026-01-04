import logging
from typing import Dict, List

from lucy_notes_manager.lib.args import Template, get_args_from_file, parse_args
from lucy_notes_manager.modules.abstract_module import AbstractModule

logger = logging.getLogger(__name__)


class ModuleManager:
    def __init__(self, modules: List[AbstractModule], args):
        self.modules = modules
        self.template: Template = [
            ("--force", str),
            ("--exclude", str),
            ("--priority", str),
            ("--use_only_first_line", bool),
        ]

        for module in self.modules:
            self.template.extend(module.template)

        self.config, _ = parse_args(args=args, template=self.template)

        priority_dict = self._parse_priority_list(self.config.get("priority") or [])
        self.modules.sort(key=lambda m: priority_dict.get(m.name, m.priority))

    def run(self, path: str) -> List[str] | None:
        self.event_config, _ = get_args_from_file(
            path=path,
            template=self.template,
            only_first_line=self.config.get("use_only_first_line", False),
        )

        self.system_args, self.modules_args = parse_args(
            args=args, template=self.template
        )
        logging.info(f"\n {args}")

    def _parse_priority_list(self, values: List[str]) -> Dict[str, int]:
        """
        values example: ["banner=5", "renamer=20", "todo=30"]

        Returns: {"banner": 5, "renamer": 20, "todo": 30}
        """
        priorities: Dict[str, int] = {}

        if not values:
            return priorities

        for item in values:
            if "=" not in item:
                raise ValueError(
                    f"Invalid --priority item '{item}'. Expected name=number, e.g. banner=5"
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
