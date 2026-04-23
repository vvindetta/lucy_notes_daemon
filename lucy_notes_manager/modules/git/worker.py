from __future__ import annotations

import logging
import subprocess
import time
from queue import Empty

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.modules.git.types import _RepoBatch

logger = logging.getLogger(__name__)


def enqueue(
    self,
    repo_root: str,
    event_type: str,
    paths: list[str],
    config_snapshot: dict,
    wants_pull: bool,
) -> None:
    self._event_queue.put((repo_root, event_type, paths, dict(config_snapshot), wants_pull))


def worker_loop(self) -> None:
    while True:
        try:
            repo_root, event_type, paths, config_snapshot, wants_pull = self._event_queue.get(
                timeout=0.2
            )
            now_timestamp = time.time()

            environment = self._git_environment(config_snapshot)

            base_message = config_snapshot.get("git_msg") or self.default_commit_message
            add_timestamp_to_message = config_snapshot.get("git_tsmsg", False)
            timestamp_format = config_snapshot.get("git_tsfmt") or self.default_timestamp_format

            debounce_seconds = float(config_snapshot.get("git_debounce_seconds", 0.8))
            git_timeout_seconds = float(config_snapshot.get("git_timeout_sec", 8.0))
            pull_timeout_seconds = float(config_snapshot.get("git_pull_timeout_sec", 30.0))
            push_timeout_seconds = float(config_snapshot.get("git_push_timeout_sec", 20.0))
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
                for path_item in paths:
                    if path_item:
                        existing_batch.hinted_paths.add(path_item)

        except Empty:
            pass

        current_timestamp = time.time()
        due_batches: list[_RepoBatch] = []
        with self._pending_lock:
            for repo_root_key, batch in list(self._pending_batches.items()):
                if current_timestamp - batch.last_event_at >= float(batch.debounce_seconds):
                    due_batches.append(batch)
                    del self._pending_batches[repo_root_key]

        for batch in due_batches:
            self._process_batch(batch)


def process_batch(self, batch: _RepoBatch) -> None:
    repo_root = batch.repo_root
    environment = batch.environment

    git_timeout_seconds = float(batch.git_timeout_seconds)
    pull_timeout_seconds = float(batch.pull_timeout_seconds)
    push_timeout_seconds = float(batch.push_timeout_seconds)
    backoff_start_seconds = float(batch.backoff_start_seconds)
    backoff_max_seconds = float(batch.backoff_max_seconds)

    if self._merge_in_progress(repo_root, environment, git_timeout_seconds):
        resolved = self._auto_resolve_merge_conflicts(
            repo_root,
            environment,
            git_timeout_seconds,
            autoresolve_mode=batch.autoresolve_mode,
        )
        if not resolved:
            self._run_git(
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

        self._safe_pull_merge(
            repo_root,
            environment,
            pull_timeout_seconds=pull_timeout_seconds,
            operation_timeout_seconds=git_timeout_seconds,
            autoresolve_mode=batch.autoresolve_mode,
            auto_set_upstream=batch.auto_set_upstream,
        )
        return

    try:
        add_result = self._run_git(
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
        add_error = (add_result.stderr or add_result.stdout or "git add failed").strip()
        logger.error("git add failed | repo=%s | error=%s", repo_root, add_error[:1200])
        safe_notify(
            name=f"addfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nError:\n{add_error[:1200]}",
        )
        return

    try:
        status_result = self._run_git(
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
        status_error = (status_result.stderr or status_result.stdout or "git status failed").strip()
        logger.error("git status failed | repo=%s | error=%s", repo_root, status_error[:1200])
        safe_notify(
            name=f"statusfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nError:\n{status_error[:1200]}",
        )
        return

    porcelain_text = (status_result.stdout or "").strip()
    changed_paths = self._parse_porcelain_paths(porcelain_text)

    if porcelain_text:
        commit_message = self._build_commit_message(batch, changed_paths)
        try:
            commit_result = self._run_git(
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
                    commit_result.stderr or commit_result.stdout or "git commit failed"
                ).strip()
                logger.error(
                    "git commit failed | repo=%s | error=%s", repo_root, commit_error[:1200]
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
            self._safe_pull_merge(
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

    def run_push() -> subprocess.CompletedProcess[str] | None:
        try:
            return self._run_git(
                repo_root,
                ["push"],
                environment,
                timeout_seconds=push_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._register_push_failure(repo_root, backoff_start_seconds, backoff_max_seconds)
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
        combined_push_output = ((push_result.stderr or "") + "\n" + (push_result.stdout or "")).strip()

        if batch.auto_merge_on_push and self._push_rejected_needs_pull(combined_push_output):
            pulled = self._safe_pull_merge(
                repo_root,
                environment,
                pull_timeout_seconds=pull_timeout_seconds,
                operation_timeout_seconds=git_timeout_seconds,
                autoresolve_mode=batch.autoresolve_mode,
                auto_set_upstream=batch.auto_set_upstream,
            )
            if pulled:
                second_push_result = run_push()
                if second_push_result is not None and second_push_result.returncode == 0:
                    self._push_backoff_seconds[repo_root] = backoff_start_seconds
                    self._push_next_allowed_at[repo_root] = 0.0
                    return

        self._register_push_failure(repo_root, backoff_start_seconds, backoff_max_seconds)
        push_error = (push_result.stderr or push_result.stdout or "git push failed").strip()
        logger.error("git push failed | repo=%s | error=%s", repo_root, push_error[:1200])
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
