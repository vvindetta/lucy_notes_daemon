from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from queue import Queue
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from lucy_notes_manager.modules.git.config import (
    DEFAULT_COMMIT_MESSAGE,
    DEFAULT_TIMESTAMP_FORMAT,
    GIT_TEMPLATE,
)
from lucy_notes_manager.modules.git.helpers import (
    abs_path,
    find_git_root,
    parse_porcelain_paths,
    path_is_inside_git_dir,
    push_rejected_needs_pull,
    to_str,
    union_resolve_text,
)
from lucy_notes_manager.modules.git.operations import (
    auto_resolve_merge_conflicts,
    conflicted_files,
    current_branch,
    git_environment,
    has_upstream,
    merge_in_progress,
    pick_remote,
    remote_branch_exists,
    run_git,
    safe_pull_merge,
    try_set_upstream,
)
from lucy_notes_manager.modules.git.types import _RepoBatch
from lucy_notes_manager.modules.git.worker import (
    collect_due_periodic_pull_events,
    enqueue,
    process_batch,
    update_periodic_pull_state,
    worker_loop,
)

logger = logging.getLogger(__name__)


class Git(AbstractModule):
    name: str = "git"
    priority: int = 50

    default_commit_message: str = DEFAULT_COMMIT_MESSAGE
    default_timestamp_format: str = DEFAULT_TIMESTAMP_FORMAT
    template: Template = GIT_TEMPLATE

    _to_str = staticmethod(to_str)
    _abs = staticmethod(abs_path)
    _path_is_inside_git_dir = staticmethod(path_is_inside_git_dir)
    _find_git_root = staticmethod(find_git_root)
    _parse_porcelain_paths = staticmethod(parse_porcelain_paths)
    _push_rejected_needs_pull = staticmethod(push_rejected_needs_pull)
    _union_resolve_text = staticmethod(union_resolve_text)

    _git_environment = git_environment
    _run_git = run_git
    _has_upstream = has_upstream
    _current_branch = current_branch
    _pick_remote = pick_remote
    _remote_branch_exists = remote_branch_exists
    _try_set_upstream = try_set_upstream
    _merge_in_progress = merge_in_progress
    _conflicted_files = conflicted_files
    _auto_resolve_merge_conflicts = auto_resolve_merge_conflicts
    _safe_pull_merge = safe_pull_merge

    _enqueue = enqueue
    _worker_loop = worker_loop
    _process_batch = process_batch
    _update_periodic_pull_state = update_periodic_pull_state
    _collect_due_periodic_pull_events = collect_due_periodic_pull_events

    def __init__(self) -> None:
        super().__init__()
        self._event_queue: Queue[tuple[str, str, list[str], dict, bool]] = Queue()
        self._pending_batches: dict[str, _RepoBatch] = {}
        self._pending_lock = threading.Lock()

        self._push_next_allowed_at: dict[str, float] = {}
        self._push_backoff_seconds: dict[str, float] = {}

        # on_opened pull cooldown progression (per repo)
        self._pull_next_allowed_at: dict[str, float] = {}
        self._pull_cooldown_seconds: dict[str, float] = {}

        # periodic auto-pull state (per repo)
        self._periodic_pull_next_at: dict[str, float] = {}
        self._periodic_pull_intervals_seconds: dict[str, float] = {}
        self._periodic_pull_configs: dict[str, dict] = {}

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _build_commit_message(self, batch: _RepoBatch, changed_paths: list[str]) -> str:
        event_summary = "+".join(sorted(batch.event_types)) if batch.event_types else "change"

        file_names = [os.path.basename(path_item) for path_item in changed_paths if path_item]
        if not file_names and batch.hinted_paths:
            file_names = [
                os.path.basename(path_item) for path_item in sorted(batch.hinted_paths)
            ]

        shown_names = ", ".join(file_names[:8])
        if len(file_names) > 8:
            shown_names += f", +{len(file_names) - 8} more"

        message_text = f"{batch.base_message}: {event_summary}"
        if shown_names:
            message_text += f" {shown_names}"
        if batch.add_timestamp_to_message:
            message_text += f" [{datetime.now().strftime(batch.timestamp_format)}]"
        return message_text

    def _pull_allowed_with_progression(
        self,
        repo_root: str,
        cooldown_min_seconds: float,
        cooldown_max_seconds: float,
    ) -> bool:
        now = time.time()
        next_allowed = self._pull_next_allowed_at.get(repo_root, 0.0)
        current_cd = self._pull_cooldown_seconds.get(repo_root, cooldown_min_seconds)

        if now < next_allowed:
            new_cd = min(max(current_cd, cooldown_min_seconds) * 2.0, cooldown_max_seconds)
            self._pull_cooldown_seconds[repo_root] = new_cd
            self._pull_next_allowed_at[repo_root] = max(next_allowed, now + new_cd)
            return False

        self._pull_cooldown_seconds[repo_root] = cooldown_min_seconds
        self._pull_next_allowed_at[repo_root] = now + cooldown_min_seconds
        return True

    def _register_push_failure(
        self, repo_root: str, backoff_start_seconds: float, backoff_max_seconds: float
    ) -> None:
        current_backoff = self._push_backoff_seconds.get(repo_root, backoff_start_seconds)
        new_backoff = min(
            max(current_backoff, backoff_start_seconds) * 2.0,
            backoff_max_seconds,
        )
        self._push_backoff_seconds[repo_root] = new_backoff
        self._push_next_allowed_at[repo_root] = time.time() + new_backoff

    def on_opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        ctx_path = self._abs(self._to_str(ctx.path)) if getattr(ctx, "path", None) else ""
        if ctx_path and self._path_is_inside_git_dir(ctx_path):
            return None

        repo_root = self._find_git_root(ctx.path)
        if not repo_root:
            return None

        if not ctx.config.get("git_auto_pull", True):
            return None

        self._enqueue(
            repo_root=repo_root,
            event_type="opened",
            paths=[self._to_str(ctx.path)],
            config_snapshot=ctx.config,
            wants_pull=True,
        )
        return None

    def on_created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "created")

    def on_modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "modified")

    def on_deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "deleted")

    def on_moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "moved")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> Optional[IgnoreMap]:
        event = system.event

        source_path_raw = self._to_str(getattr(event, "src_path", "") or "")
        destination_path_raw = getattr(event, "dest_path", None)
        destination_path_value = (
            self._to_str(destination_path_raw)
            if destination_path_raw is not None
            else ""
        )

        source_path = self._abs(source_path_raw) if source_path_raw else ""
        destination_path = self._abs(destination_path_value) if destination_path_value else ""

        if (source_path and self._path_is_inside_git_dir(source_path)) or (
            destination_path and self._path_is_inside_git_dir(destination_path)
        ):
            return None

        repo_root = self._find_git_root(ctx.path) or self._find_git_root(
            destination_path or source_path
        )
        if not repo_root:
            return None

        paths_to_hint: list[str] = []
        if event_type != "moved":
            paths_to_hint = [self._to_str(ctx.path)]
        else:
            if source_path:
                paths_to_hint.append(source_path)
            if destination_path:
                paths_to_hint.append(destination_path)

        self._enqueue(
            repo_root=repo_root,
            event_type=event_type,
            paths=paths_to_hint,
            config_snapshot=ctx.config,
            wants_pull=False,
        )
        return None
