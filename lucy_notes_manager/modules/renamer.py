import os
from datetime import datetime
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Renamer(AbstractModule):
    name: str = "renamer"
    priority: int = 20

    template: Template = [
        ("--r", str, None, "Rename file. Example: --r new_name.md."),
        (
            "--auto-rename",
            bool,
            False,  # IMPORTANT: for your argparse bool handling, default is a bool, not [False]
            "On create: t|txt -> DD-MM.txt, m|md -> DD-MM.md. If exists -> HHMM-DD-MM.ext",
        ),
    ]

    def _apply_manual(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        values = config.get("r")
        if not values:
            return None

        new_name = str(values[0]).strip()
        if not new_name:
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        dir_path = os.path.dirname(old_path)
        new_path = os.path.abspath(os.path.join(dir_path, new_name))

        if old_path == new_path:
            return None
        if os.path.exists(new_path):
            return None

        try:
            os.rename(old_path, new_path)
            return {old_path: 1, new_path: 1}
        except (FileNotFoundError, OSError):
            return None

    def _apply_auto_on_create(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        if not config.get("auto_rename", False):
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        base = os.path.basename(old_path)
        stem, _ext = os.path.splitext(base)
        name = (stem or base).strip().lower()

        if name in ("t", "txt"):
            out_ext = ".txt"
        elif name in ("m", "md"):
            out_ext = ".md"
        else:
            return None

        now = datetime.now()
        day_month = now.strftime("%d-%m")
        hour_min = now.strftime("%H%M")

        dir_path = os.path.dirname(old_path)

        new_name = f"{day_month}{out_ext}"
        new_path = os.path.abspath(os.path.join(dir_path, new_name))

        if os.path.exists(new_path):
            new_name = f"{hour_min}-{day_month}{out_ext}"
            new_path = os.path.abspath(os.path.join(dir_path, new_name))
            if os.path.exists(new_path):
                return None

        if old_path == new_path:
            return None

        try:
            os.rename(old_path, new_path)
            return {old_path: 1, new_path: 1}
        except (FileNotFoundError, OSError):
            return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        # manual rename has priority
        changed = self._apply_manual(path=ctx.path, config=ctx.config)
        if changed:
            return changed
        return self._apply_auto_on_create(path=ctx.path, config=ctx.config)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply_manual(path=ctx.path, config=ctx.config)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply_manual(path=ctx.path, config=ctx.config)
