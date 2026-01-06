import os
from typing import Optional

from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Renamer(AbstractModule):
    name: str = "renamer"
    priority: int = 20

    template = [
        ("--r", str, None),
    ]

    def _apply(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        r_vals = config["r"]
        if not r_vals:
            return None

        new_name = str(r_vals[0]).strip()
        if not new_name:
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        dir_path = os.path.dirname(old_path)
        new_path = os.path.abspath(os.path.join(dir_path, new_name))

        if old_path == new_path:
            return None

        if os.path.isdir(new_path) or os.path.exists(new_path):
            return None

        try:
            os.rename(old_path, new_path)
            return {old_path: 1, new_path: 1}
        except (FileNotFoundError, OSError):
            return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)
