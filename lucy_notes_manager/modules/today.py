from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Today(AbstractModule):
    name: str = "today"
    priority: int = 25

    template: Template = [
        (
            "--today-now-name",
            str,
            ["now.md"],
            "Name of active note file to archive when stale. Default: now.md",
        ),
        (
            "--today-past-name",
            str,
            ["past.md"],
            "Name of archive file (same directory as now file). Default: past.md",
        ),
        (
            "--today-idle-hours",
            float,
            [12.0],
            "Archive now file when its last modification age is >= this many hours. Default: 12",
        ),
    ]

    def _one(self, config: dict, key: str, default):
        value = config.get(key, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value

    def _resolve_paths(self, ctx: Context) -> tuple[str, str] | None:
        now_name = str(self._one(ctx.config, "today_now_name", "now.md")).strip() or "now.md"
        past_name = str(self._one(ctx.config, "today_past_name", "past.md")).strip() or "past.md"

        now_path = os.path.abspath(ctx.path)
        if os.path.basename(now_path) != now_name:
            return None

        if now_name == past_name:
            return None

        parent_dir = os.path.dirname(now_path)
        past_path = os.path.abspath(os.path.join(parent_dir, past_name))
        return now_path, past_path

    def _is_stale(self, now_path: str, idle_hours: float) -> bool:
        try:
            mtime = os.path.getmtime(now_path)
        except OSError:
            return False
        age_seconds = time.time() - float(mtime)
        return age_seconds >= max(0.0, float(idle_hours)) * 3600.0

    def _append_entry(self, past_path: str, entry: str) -> bool:
        old_content = ""
        if os.path.exists(past_path):
            try:
                with open(past_path, "r", encoding="utf-8") as file_handle:
                    old_content = file_handle.read()
            except OSError:
                return False

        sep = ""
        if old_content:
            if not old_content.endswith("\n"):
                sep = "\n\n"
            elif not old_content.endswith("\n\n"):
                sep = "\n"

        try:
            with open(past_path, "a", encoding="utf-8") as file_handle:
                file_handle.write(sep + entry)
        except OSError:
            return False
        return True

    def _archive_if_needed(self, ctx: Context) -> Optional[IgnoreMap]:
        resolved = self._resolve_paths(ctx)
        if not resolved:
            return None
        now_path, past_path = resolved

        idle_hours = float(self._one(ctx.config, "today_idle_hours", 12.0))
        if not self._is_stale(now_path, idle_hours):
            return None

        try:
            with open(now_path, "r", encoding="utf-8") as now_handle:
                now_text = now_handle.read()
        except OSError:
            return None

        body = now_text.strip()
        if not body:
            return None

        date_label = datetime.now().strftime("%d.%m")
        entry = f"-- {date_label}\n{body}\n"

        if not self._append_entry(past_path, entry):
            return None

        try:
            with open(now_path, "w", encoding="utf-8") as now_handle:
                now_handle.write("")
        except OSError:
            return None

        return {now_path: 1, past_path: 1}

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx)

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx)
