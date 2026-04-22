from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime
from queue import Empty, Queue
from typing import Optional

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from lucy_notes_manager.modules.git import commands, paths
from lucy_notes_manager.modules.git.batch import _RepoBatch
from lucy_notes_manager.modules.git.conflicts import auto_resolve_merge_conflicts
from lucy_notes_manager.modules.git.parsing import (
    parse_porcelain_paths,
    push_rejected_needs_pull,
    union_resolve_text,
)
from lucy_notes_manager.modules.git.pull import safe_pull_merge

logger = logging.getLogger(__name__)


class Git(AbstractModule):
    name: str = "git"
    priority: int = 50

    default_commit_message: str = "Auto-commit"
    default_timestamp_format: str = "%Y-%m-%d_%H-%M-%S"

    template: Template = [
        (
            "--git-msg",
            str,
            None,
            "Base commit message. Example: --git-msg 'Notes update'. If not set, uses 'Auto-commit'.",
        ),
        (
            "--git-tsmsg",
            bool,
            False,
            "Append a timestamp to the commit message. Example: --git-tsmsg true.",
        ),
        (
            "--git-tsfmt",
            str,
            None,
            "Timestamp format for --git-tsmsg (Python strftime). Example: --git-tsfmt '%Y-%m-%d %H:%M:%S'.",
        ),
        (
            "--git-key",
            str,
            None,
            "Path to SSH private key for Git operations (no .pub). Used via GIT_SSH_COMMAND. Example: --git-key ~/.ssh/id_ed25519.",
        ),
        (
            "--git-auto-pull",
            bool,
            True,
            "Automatically run 'git pull --no-rebase' when a repo is opened. Never uses rebase or force.",
        ),
        (
            "--git-pull-cooldown-min-sec",
            float,
            10.0,
            "Minimum cooldown (seconds) between auto-pulls triggered by on_opened.",
        ),
        (
            "--git-pull-cooldown-max-sec",
            float,
            200.0,
            "Maximum cooldown cap (seconds). Cooldown progresses (doubles) if on_opened triggers too often.",
        ),
        (
            "--git-auto-merge-on-push",
            bool,
            True,
            "If 'git push' is rejected because the remote is ahead, automatically run 'git pull --no-rebase' (merge) and retry push. No rebase, no force.",
        ),
        (
            "--git-auto-set-upstream",
            bool,
            True,
            "If the current branch has no upstream, try to set it to <remote>/<branch> (prefer remote 'origin') when that remote branch exists.",
        ),
        (
            "--git-autoresolve",
            str,
            "union",
            "How to auto-resolve merge conflicts during auto-merge: "
            "'none' (do not resolve), 'ours' (keep local), 'theirs' (keep remote), 'union' (keep both sides, remove markers).",
        ),
        (
            "--git-debounce-seconds",
            float,
            0.8,
            "Debounce window in seconds: group file events and commit/push once after changes calm down.",
        ),
        (
            "--git-timeout-sec",
            float,
            8.0,
            "Timeout (seconds) for git add/status/commit operations.",
        ),
        (
            "--git-pull-timeout-sec",
            float,
            30.0,
            "Timeout (seconds) for git pull (merge). Increase for slow networks or large repos.",
        ),
        ("--git-push-timeout-sec", float, 20.0, "Timeout (seconds) for git push."),
        (
            "--git-push-backoff-start-sec",
            float,
            5.0,
            "Initial backoff (seconds) before retrying push after a failure.",
        ),
        (
            "--git-push-backoff-max-sec",
            float,
            120.0,
            "Maximum backoff (seconds) cap for repeated push failures.",
        ),
        (
            "--git-update-source",
            str,
            "poll",
            "How to detect remote updates: 'poll' (default, pull when files are opened), "
            "'rss' (poll a provider RSS/Atom feed), 'webhook' (listen for push notifications), "
            "'off' (never auto-pull; commit/push only).",
        ),
    ]

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

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    _to_str = staticmethod(paths.to_str)
    _abs = staticmethod(paths.abs_path)
    _path_is_inside_git_dir = staticmethod(paths.path_is_inside_git_dir)
    _find_git_root = staticmethod(paths.find_git_root)
    _git_environment = staticmethod(paths.git_environment)

    _run_git = staticmethod(commands.run_git)
    _has_upstream = staticmethod(commands.has_upstream)
    _current_branch = staticmethod(commands.current_branch)
    _pick_remote = staticmethod(commands.pick_remote)
    _remote_branch_exists = staticmethod(commands.remote_branch_exists)
    _try_set_upstream = staticmethod(commands.try_set_upstream)
    _merge_in_progress = staticmethod(commands.merge_in_progress)
    _conflicted_files = staticmethod(commands.conflicted_files)

    _parse_porcelain_paths = staticmethod(parse_porcelain_paths)
    _push_rejected_needs_pull = staticmethod(push_rejected_needs_pull)
    _union_resolve_text = staticmethod(union_resolve_text)

    _auto_resolve_merge_conflicts = staticmethod(auto_resolve_merge_conflicts)
    _safe_pull_merge = staticmethod(safe_pull_merge)

    def _build_commit_message(self, batch: _RepoBatch, changed_paths: list[str]) -> str:
        import os

        event_summary = (
            "+".join(sorted(batch.event_types)) if batch.event_types else "change"
        )

        file_names = [
            os.path.basename(path_item) for path_item in changed_paths if path_item
        ]
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

    def _enqueue(
        self,
        repo_root: str,
        event_type: str,
        paths: list[str],
        config_snapshot: dict,
        wants_pull: bool,
    ) -> None:
        self._event_queue.put(
            (repo_root, event_type, paths, dict(config_snapshot), wants_pull)
        )

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
            new_cd = min(
                max(current_cd, cooldown_min_seconds) * 2.0, cooldown_max_seconds
            )
            self._pull_cooldown_seconds[repo_root] = new_cd
            self._pull_next_allowed_at[repo_root] = max(next_allowed, now + new_cd)
            return False

        self._pull_cooldown_seconds[repo_root] = cooldown_min_seconds
        self._pull_next_allowed_at[repo_root] = now + cooldown_min_seconds
        return True

    def _worker_loop(self) -> None:
        while True:
            try:
                repo_root, event_type, path_items, config_snapshot, wants_pull = (
                    self._event_queue.get(timeout=0.2)
                )
                now_timestamp = time.time()

                environment = paths.git_environment(config_snapshot)

                base_message = (
                    config_snapshot.get("git_msg") or self.default_commit_message
                )
                add_timestamp_to_message = config_snapshot.get("git_tsmsg", False)
                timestamp_format = (
                    config_snapshot.get("git_tsfmt") or self.default_timestamp_format
                )

                debounce_seconds = float(
                    config_snapshot.get("git_debounce_seconds", 0.8)
                )
                git_timeout_seconds = float(config_snapshot.get("git_timeout_sec", 8.0))
                pull_timeout_seconds = float(
                    config_snapshot.get("git_pull_timeout_sec", 30.0)
                )
                push_timeout_seconds = float(
                    config_snapshot.get("git_push_timeout_sec", 20.0)
                )
                backoff_start_seconds = float(
                    config_snapshot.get("git_push_backoff_start_sec", 5.0)
                )
                backoff_max_seconds = float(
                    config_snapshot.get("git_push_backoff_max_sec", 120.0)
                )

                pull_cooldown_min_seconds = float(
                    config_snapshot.get("git_pull_cooldown_min_sec", 10.0)
                )
                pull_cooldown_max_seconds = float(
                    config_snapshot.get("git_pull_cooldown_max_sec", 120.0)
                )

                auto_merge_on_push = config_snapshot.get("git_auto_merge_on_push", True)
                auto_set_upstream = config_snapshot.get("git_auto_set_upstream", True)
                autoresolve_mode = config_snapshot.get("git_autoresolve", "union")

                with self._pending_lock:
                    existing_batch = self._pending_batches.get(repo_root)
                    if not existing_batch:
                        existing_batch = _RepoBatch(
                            repo_root=repo_root,
                            base_message=base_message,
                            add_timestamp_to_message=add_timestamp_to_message,
                            timestamp_format=timestamp_format,
                            environment=environment,
                            debounce_seconds=debounce_seconds,
                            git_timeout_seconds=git_timeout_seconds,
                            pull_timeout_seconds=pull_timeout_seconds,
                            push_timeout_seconds=push_timeout_seconds,
                            backoff_start_seconds=backoff_start_seconds,
                            backoff_max_seconds=backoff_max_seconds,
                            pull_cooldown_min_seconds=pull_cooldown_min_seconds,
                            pull_cooldown_max_seconds=pull_cooldown_max_seconds,
                            wants_pull=wants_pull,
                            auto_merge_on_push=auto_merge_on_push,
                            auto_set_upstream=auto_set_upstream,
                            autoresolve_mode=autoresolve_mode,
                        )
                        self._pending_batches[repo_root] = existing_batch

                    existing_batch.base_message = base_message
                    existing_batch.add_timestamp_to_message = add_timestamp_to_message
                    existing_batch.timestamp_format = timestamp_format
                    existing_batch.environment = environment

                    existing_batch.debounce_seconds = debounce_seconds
                    existing_batch.git_timeout_seconds = git_timeout_seconds
                    existing_batch.pull_timeout_seconds = pull_timeout_seconds
                    existing_batch.push_timeout_seconds = push_timeout_seconds
                    existing_batch.backoff_start_seconds = backoff_start_seconds
                    existing_batch.backoff_max_seconds = backoff_max_seconds

                    existing_batch.pull_cooldown_min_seconds = pull_cooldown_min_seconds
                    existing_batch.pull_cooldown_max_seconds = pull_cooldown_max_seconds

                    existing_batch.auto_merge_on_push = auto_merge_on_push
                    existing_batch.auto_set_upstream = auto_set_upstream
                    existing_batch.autoresolve_mode = autoresolve_mode

                    existing_batch.wants_pull = existing_batch.wants_pull or wants_pull
                    existing_batch.last_event_at = now_timestamp
                    existing_batch.event_types.add(event_type)
                    for path_item in path_items:
                        if path_item:
                            existing_batch.hinted_paths.add(path_item)

            except Empty:
                pass

            current_timestamp = time.time()
            due_batches: list[_RepoBatch] = []
            with self._pending_lock:
                for repo_root_key, batch in list(self._pending_batches.items()):
                    if current_timestamp - batch.last_event_at >= float(
                        batch.debounce_seconds
                    ):
                        due_batches.append(batch)
                        del self._pending_batches[repo_root_key]

            for batch in due_batches:
                self._process_batch(batch)

    def _process_batch(self, batch: _RepoBatch) -> None:
        import os  # noqa: F401

        repo_root = batch.repo_root
        environment = batch.environment

        git_timeout_seconds = float(batch.git_timeout_seconds)
        pull_timeout_seconds = float(batch.pull_timeout_seconds)
        push_timeout_seconds = float(batch.push_timeout_seconds)
        backoff_start_seconds = float(batch.backoff_start_seconds)
        backoff_max_seconds = float(batch.backoff_max_seconds)

        if commands.merge_in_progress(repo_root, environment, git_timeout_seconds):
            resolved = auto_resolve_merge_conflicts(
                repo_root,
                environment,
                git_timeout_seconds,
                autoresolve_mode=batch.autoresolve_mode,
            )
            if not resolved:
                commands.run_git(
                    repo_root,
                    ["merge", "--abort"],
                    environment,
                    timeout_seconds=git_timeout_seconds,
                )
                logger.error(
                    "found unfinished merge; auto-resolve failed; merge aborted | repo=%s",
                    repo_root,
                )
                safe_notify(
                    name=f"merge-stuck:{repo_root}",
                    message=(
                        f"Repository:\n{repo_root}\n\n"
                        f"Found unfinished merge; auto-resolve failed; merge aborted."
                    ),
                )
                return

        opened_only = batch.event_types == {"opened"}
        if opened_only and batch.wants_pull:
            if not self._pull_allowed_with_progression(
                repo_root=repo_root,
                cooldown_min_seconds=float(batch.pull_cooldown_min_seconds),
                cooldown_max_seconds=float(batch.pull_cooldown_max_seconds),
            ):
                return

            safe_pull_merge(
                repo_root,
                environment,
                pull_timeout_seconds=pull_timeout_seconds,
                operation_timeout_seconds=git_timeout_seconds,
                autoresolve_mode=batch.autoresolve_mode,
                auto_set_upstream=batch.auto_set_upstream,
            )
            return

        try:
            add_result = commands.run_git(
                repo_root,
                ["add", "-A"],
                environment,
                timeout_seconds=git_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            logger.error("git add timed out | repo=%s", repo_root)
            safe_notify(
                name=f"timeout:add:{repo_root}",
                message=f"git add timed out:\n{repo_root}",
            )
            return

        if add_result.returncode != 0:
            add_error = (
                add_result.stderr or add_result.stdout or "git add failed"
            ).strip()
            logger.error(
                "git add failed | repo=%s | error=%s", repo_root, add_error[:1200]
            )
            safe_notify(
                name=f"addfail:{repo_root}",
                message=f"Repository:\n{repo_root}\n\nError:\n{add_error[:1200]}",
            )
            return

        try:
            status_result = commands.run_git(
                repo_root,
                ["status", "--porcelain"],
                environment,
                timeout_seconds=git_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            logger.error("git status timed out | repo=%s", repo_root)
            safe_notify(
                name=f"timeout:status:{repo_root}",
                message=f"git status timed out:\n{repo_root}",
            )
            return

        if status_result.returncode != 0:
            status_error = (
                status_result.stderr or status_result.stdout or "git status failed"
            ).strip()
            logger.error(
                "git status failed | repo=%s | error=%s", repo_root, status_error[:1200]
            )
            safe_notify(
                name=f"statusfail:{repo_root}",
                message=f"Repository:\n{repo_root}\n\nError:\n{status_error[:1200]}",
            )
            return

        porcelain_text = (status_result.stdout or "").strip()
        changed_paths = parse_porcelain_paths(porcelain_text)

        if porcelain_text:
            commit_message = self._build_commit_message(batch, changed_paths)
            try:
                commit_result = commands.run_git(
                    repo_root,
                    ["commit", "-m", commit_message],
                    environment,
                    timeout_seconds=git_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                logger.error("git commit timed out | repo=%s", repo_root)
                safe_notify(
                    name=f"timeout:commit:{repo_root}",
                    message=f"git commit timed out:\n{repo_root}",
                )
                return

            if commit_result.returncode != 0:
                combined_output = (
                    ((commit_result.stderr or "") + "\n" + (commit_result.stdout or ""))
                    .strip()
                    .lower()
                )
                if "nothing to commit" not in combined_output:
                    commit_error = (
                        commit_result.stderr
                        or commit_result.stdout
                        or "git commit failed"
                    ).strip()
                    logger.error(
                        "git commit failed | repo=%s | error=%s",
                        repo_root,
                        commit_error[:1200],
                    )
                    safe_notify(
                        name=f"commitfail:{repo_root}",
                        message=f"Repository:\n{repo_root}\n\nError:\n{commit_error[:1200]}",
                    )
                    return

        if batch.wants_pull:
            if self._pull_allowed_with_progression(
                repo_root=repo_root,
                cooldown_min_seconds=float(batch.pull_cooldown_min_seconds),
                cooldown_max_seconds=float(batch.pull_cooldown_max_seconds),
            ):
                safe_pull_merge(
                    repo_root,
                    environment,
                    pull_timeout_seconds=pull_timeout_seconds,
                    operation_timeout_seconds=git_timeout_seconds,
                    autoresolve_mode=batch.autoresolve_mode,
                    auto_set_upstream=batch.auto_set_upstream,
                )

        now_timestamp = time.time()
        next_allowed_timestamp = self._push_next_allowed_at.get(repo_root, 0.0)
        if now_timestamp < next_allowed_timestamp:
            return

        def run_push() -> Optional[subprocess.CompletedProcess[str]]:
            try:
                return commands.run_git(
                    repo_root,
                    ["push"],
                    environment,
                    timeout_seconds=push_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                self._register_push_failure(
                    repo_root, backoff_start_seconds, backoff_max_seconds
                )
                logger.error("git push timed out | repo=%s", repo_root)
                safe_notify(
                    name=f"timeout:push:{repo_root}",
                    message=f"git push timed out:\n{repo_root}",
                )
                return None

        push_result = run_push()
        if push_result is None:
            return

        if push_result.returncode != 0:
            combined_push_output = (
                (push_result.stderr or "") + "\n" + (push_result.stdout or "")
            ).strip()

            if batch.auto_merge_on_push and push_rejected_needs_pull(
                combined_push_output
            ):
                pulled = safe_pull_merge(
                    repo_root,
                    environment,
                    pull_timeout_seconds=pull_timeout_seconds,
                    operation_timeout_seconds=git_timeout_seconds,
                    autoresolve_mode=batch.autoresolve_mode,
                    auto_set_upstream=batch.auto_set_upstream,
                )
                if pulled:
                    second_push_result = run_push()
                    if (
                        second_push_result is not None
                        and second_push_result.returncode == 0
                    ):
                        self._push_backoff_seconds[repo_root] = backoff_start_seconds
                        self._push_next_allowed_at[repo_root] = 0.0
                        return

            self._register_push_failure(
                repo_root, backoff_start_seconds, backoff_max_seconds
            )
            push_error = (
                push_result.stderr or push_result.stdout or "git push failed"
            ).strip()
            logger.error(
                "git push failed | repo=%s | error=%s", repo_root, push_error[:1200]
            )
            safe_notify(
                name=f"pushfail:{repo_root}",
                message=(
                    f"Repository:\n{repo_root}\n\n"
                    f"Command:\ngit push\n\n"
                    f"Error:\n{push_error[:1200]}"
                ),
            )
        else:
            self._push_backoff_seconds[repo_root] = backoff_start_seconds
            self._push_next_allowed_at[repo_root] = 0.0

    def _register_push_failure(
        self, repo_root: str, backoff_start_seconds: float, backoff_max_seconds: float
    ) -> None:
        current_backoff = self._push_backoff_seconds.get(
            repo_root, backoff_start_seconds
        )
        new_backoff = min(
            max(current_backoff, backoff_start_seconds) * 2.0, backoff_max_seconds
        )
        self._push_backoff_seconds[repo_root] = new_backoff
        self._push_next_allowed_at[repo_root] = time.time() + new_backoff

    def trigger_pull(self, repo_root: str, config_snapshot: dict) -> None:
        """
        Trigger a pull-merge for `repo_root` outside of file events.

        Used by notification providers (webhook/RSS) to pull only when the
        remote actually changes, instead of polling on every `on_opened`.
        """
        self._enqueue(
            repo_root=repo_root,
            event_type="opened",
            paths=[],
            config_snapshot=config_snapshot,
            wants_pull=True,
        )

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        ctx_path = (
            paths.abs_path(paths.to_str(ctx.path)) if getattr(ctx, "path", None) else ""
        )
        if ctx_path and paths.path_is_inside_git_dir(ctx_path):
            return None

        repo_root = paths.find_git_root(ctx.path)
        if not repo_root:
            return None

        if not ctx.config.get("git_auto_pull", True):
            return None

        update_source = (ctx.config.get("git_update_source") or "poll").strip().lower()
        if update_source != "poll":
            # External sources (webhook/RSS/off) handle pulls; skip on_opened pulls.
            return None

        self._enqueue(
            repo_root=repo_root,
            event_type="opened",
            paths=[paths.to_str(ctx.path)],
            config_snapshot=ctx.config,
            wants_pull=True,
        )
        return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "created")

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "modified")

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "deleted")

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "moved")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> Optional[IgnoreMap]:
        event = system.event

        source_path_raw = paths.to_str(getattr(event, "src_path", "") or "")
        destination_path_raw = getattr(event, "dest_path", None)
        destination_path_value = (
            paths.to_str(destination_path_raw)
            if destination_path_raw is not None
            else ""
        )

        source_path = paths.abs_path(source_path_raw) if source_path_raw else ""
        destination_path = (
            paths.abs_path(destination_path_value) if destination_path_value else ""
        )

        if (source_path and paths.path_is_inside_git_dir(source_path)) or (
            destination_path and paths.path_is_inside_git_dir(destination_path)
        ):
            return None

        repo_root = paths.find_git_root(ctx.path) or paths.find_git_root(
            destination_path or source_path
        )
        if not repo_root:
            return None

        paths_to_hint: list[str] = []
        if event_type != "moved":
            paths_to_hint = [paths.to_str(ctx.path)]
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
