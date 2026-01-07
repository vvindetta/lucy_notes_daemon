from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Queue
from typing import Any, Dict, Optional, Union

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

PathLike = Union[str, bytes]


@dataclass
class _RepoBatch:
    repo_root: str
    base_message: str
    add_timestamp_to_message: bool
    timestamp_format: str
    environment: Dict[str, str]

    debounce_seconds: float
    git_timeout_seconds: float
    pull_timeout_seconds: float
    push_timeout_seconds: float
    backoff_start_seconds: float
    backoff_max_seconds: float

    wants_pull: bool = False
    auto_merge_on_push: bool = True
    autoresolve_mode: str = "union"  # none|ours|theirs|union

    last_event_at: float = field(default_factory=time.time)
    event_types: set[str] = field(default_factory=set)
    hinted_paths: set[str] = field(default_factory=set)


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
            "--git-auto-merge-on-push",
            bool,
            True,
            "If 'git push' is rejected because the remote is ahead, automatically run 'git pull --no-rebase' (merge) and retry push. No rebase, no force.",
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
    ]

    def __init__(self) -> None:
        super().__init__()
        self._event_queue: Queue[tuple[str, str, list[str], dict, bool]] = Queue()
        self._pending_batches: dict[str, _RepoBatch] = {}
        self._pending_lock = threading.Lock()

        self._push_next_allowed_at: dict[str, float] = {}
        self._push_backoff_seconds: dict[str, float] = {}

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    @staticmethod
    def _to_str(path_value: PathLike) -> str:
        if isinstance(path_value, bytes):
            return path_value.decode(errors="surrogateescape")
        return path_value

    @staticmethod
    def _abs(path_value: str) -> str:
        return os.path.abspath(os.path.expanduser(path_value))

    @staticmethod
    def _cfg_first(config: dict, key: str) -> Any:
        value = config.get(key)
        if isinstance(value, list):
            return value[0] if value else None
        return value

    @staticmethod
    def _cfg_float(config: dict, key: str, default: float) -> float:
        value = Git._cfg_first(config, key)
        try:
            if value is None or value == "":
                return float(default)
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _cfg_bool(config: dict, key: str, default: bool) -> bool:
        value = Git._cfg_first(config, key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _cfg_str(config: dict, key: str, default: str) -> str:
        value = Git._cfg_first(config, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    @staticmethod
    def _path_is_inside_git_dir(path_value: str) -> bool:
        path_components = os.path.abspath(path_value).split(os.sep)
        return ".git" in path_components

    @staticmethod
    def _find_git_root(path_value: str) -> str | None:
        current_path = os.path.abspath(path_value)
        if not os.path.isdir(current_path):
            current_path = os.path.dirname(current_path)

        while True:
            if os.path.isdir(os.path.join(current_path, ".git")):
                return current_path
            parent_path = os.path.dirname(current_path)
            if parent_path == current_path:
                return None
            current_path = parent_path

    def _git_environment(self, config: dict) -> Dict[str, str]:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"

        key_path_raw = self._cfg_first(config, "git_key")
        if not isinstance(key_path_raw, str) or not key_path_raw:
            return environment

        key_path = self._abs(key_path_raw)
        if not os.path.isfile(key_path):
            safe_notify(
                name=f"gkey-missing:{key_path}",
                message=f"SSH key not found:\n{key_path}",
            )
            return environment

        environment["GIT_SSH_COMMAND"] = (
            f'ssh -i "{key_path}" '
            f"-o IdentitiesOnly=yes "
            f"-o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new"
        )
        return environment

    def _run_git(
        self,
        repo_root: str,
        arguments: list[str],
        environment: Dict[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + arguments,
            cwd=repo_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )

    def _base_message(self, config: dict) -> str:
        message_value = self._cfg_first(config, "git_msg")
        if isinstance(message_value, str) and message_value:
            return message_value
        return self.default_commit_message

    def _timestamp_format(self, config: dict) -> str:
        format_value = self._cfg_first(config, "git_tsfmt")
        if isinstance(format_value, str) and format_value:
            return format_value
        return self.default_timestamp_format

    @staticmethod
    def _parse_porcelain_paths(porcelain_text: str) -> list[str]:
        result_paths: list[str] = []
        for line_text in (porcelain_text or "").splitlines():
            trimmed_line = line_text.rstrip("\n")
            if len(trimmed_line) < 4:
                continue
            path_part = trimmed_line[3:]
            if " -> " in path_part:
                path_part = path_part.split(" -> ", 1)[1]
            result_paths.append(path_part)
        return result_paths

    def _build_commit_message(self, batch: _RepoBatch, changed_paths: list[str]) -> str:
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

    def _has_upstream(
        self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
    ) -> bool:
        result = self._run_git(
            repo_root,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
            environment,
            timeout_seconds,
        )
        return result.returncode == 0 and bool((result.stdout or "").strip())

    def _merge_in_progress(
        self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
    ) -> bool:
        result = self._run_git(
            repo_root,
            ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
            environment,
            timeout_seconds,
        )
        return result.returncode == 0 and bool((result.stdout or "").strip())

    def _conflicted_files(
        self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
    ) -> list[str]:
        result = self._run_git(
            repo_root,
            ["diff", "--name-only", "--diff-filter=U"],
            environment,
            timeout_seconds,
        )
        if result.returncode != 0:
            return []
        return [
            line_text.strip()
            for line_text in (result.stdout or "").splitlines()
            if line_text.strip()
        ]

    @staticmethod
    def _push_rejected_needs_pull(output_text: str) -> bool:
        output_lower = (output_text or "").lower()
        indicators = [
            "non-fast-forward",
            "fetch first",
            "failed to push some refs",
            "remote contains work",
            "updates were rejected",
            "rejected",
        ]
        return any(indicator in output_lower for indicator in indicators)

    @staticmethod
    def _union_resolve_text(file_content: str) -> Optional[str]:
        lines = file_content.splitlines(keepends=True)
        resolved_lines: list[str] = []
        line_index = 0
        saw_markers = False

        while line_index < len(lines):
            current_line = lines[line_index]
            if current_line.startswith("<<<<<<< "):
                saw_markers = True
                line_index += 1

                ours_lines: list[str] = []
                while line_index < len(lines) and not lines[line_index].startswith(
                    "======="
                ):
                    ours_lines.append(lines[line_index])
                    line_index += 1
                if line_index >= len(lines) or not lines[line_index].startswith(
                    "======="
                ):
                    return None
                line_index += 1

                theirs_lines: list[str] = []
                while line_index < len(lines) and not lines[line_index].startswith(
                    ">>>>>>> "
                ):
                    theirs_lines.append(lines[line_index])
                    line_index += 1
                if line_index >= len(lines) or not lines[line_index].startswith(
                    ">>>>>>> "
                ):
                    return None
                line_index += 1

                resolved_lines.extend(ours_lines)
                if (
                    ours_lines
                    and theirs_lines
                    and (not ours_lines[-1].endswith("\n"))
                    and (not theirs_lines[0].startswith("\n"))
                ):
                    resolved_lines.append("\n")
                resolved_lines.extend(theirs_lines)
                continue

            resolved_lines.append(current_line)
            line_index += 1

        if not saw_markers:
            return None
        return "".join(resolved_lines)

    def _auto_resolve_merge_conflicts(
        self,
        repo_root: str,
        environment: Dict[str, str],
        timeout_seconds: float,
        autoresolve_mode: str,
    ) -> bool:
        normalized_mode = (autoresolve_mode or "none").strip().lower()
        if normalized_mode not in {"none", "ours", "theirs", "union"}:
            normalized_mode = "none"

        conflicted_paths = self._conflicted_files(
            repo_root, environment, timeout_seconds
        )
        if not conflicted_paths or normalized_mode == "none":
            return False

        for relative_path in conflicted_paths:
            absolute_path = os.path.join(repo_root, relative_path)

            if normalized_mode in {"ours", "theirs"}:
                side_argument = "--ours" if normalized_mode == "ours" else "--theirs"
                checkout_result = self._run_git(
                    repo_root,
                    ["checkout", side_argument, "--", relative_path],
                    environment,
                    timeout_seconds,
                )
                if checkout_result.returncode != 0:
                    return False

            elif normalized_mode == "union":
                try:
                    if os.path.isfile(absolute_path):
                        file_text = open(
                            absolute_path,
                            "r",
                            encoding="utf-8",
                            errors="surrogateescape",
                        ).read()
                        resolved_text = self._union_resolve_text(file_text)
                        if resolved_text is None:
                            checkout_result = self._run_git(
                                repo_root,
                                ["checkout", "--ours", "--", relative_path],
                                environment,
                                timeout_seconds,
                            )
                            if checkout_result.returncode != 0:
                                return False
                        else:
                            open(
                                absolute_path,
                                "w",
                                encoding="utf-8",
                                errors="surrogateescape",
                            ).write(resolved_text)
                    else:
                        checkout_result = self._run_git(
                            repo_root,
                            ["checkout", "--ours", "--", relative_path],
                            environment,
                            timeout_seconds,
                        )
                        if checkout_result.returncode != 0:
                            return False
                except OSError:
                    return False

            add_result = self._run_git(
                repo_root, ["add", "--", relative_path], environment, timeout_seconds
            )
            if add_result.returncode != 0:
                return False

        commit_result = self._run_git(
            repo_root, ["commit", "--no-edit"], environment, timeout_seconds
        )
        return commit_result.returncode == 0

    def _safe_pull_merge(
        self,
        repo_root: str,
        environment: Dict[str, str],
        pull_timeout_seconds: float,
        operation_timeout_seconds: float,
        autoresolve_mode: str,
    ) -> bool:
        if not self._has_upstream(repo_root, environment, operation_timeout_seconds):
            safe_notify(
                name=f"pull-noupstream:{repo_root}",
                message=f"Repository:\n{repo_root}\n\nNo upstream configured for current branch; skip auto-pull.",
            )
            return False

        try:
            pull_result = self._run_git(
                repo_root,
                ["pull", "--no-rebase", "--no-edit"],
                environment,
                timeout_seconds=pull_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            safe_notify(
                name=f"timeout:pull:{repo_root}",
                message=f"git pull timed out:\n{repo_root}",
            )
            return False

        if pull_result.returncode == 0:
            return True

        if self._merge_in_progress(repo_root, environment, operation_timeout_seconds):
            resolved = self._auto_resolve_merge_conflicts(
                repo_root,
                environment,
                operation_timeout_seconds,
                autoresolve_mode=autoresolve_mode,
            )
            if resolved:
                return True

            self._run_git(
                repo_root,
                ["merge", "--abort"],
                environment,
                timeout_seconds=operation_timeout_seconds,
            )
            pull_error = (
                pull_result.stderr or pull_result.stdout or "git pull failed"
            ).strip()
            safe_notify(
                name=f"pull-conflict:{repo_root}",
                message=(
                    f"Repository:\n{repo_root}\n\n"
                    f"Auto-merge conflict resolution failed.\n"
                    f"Merge aborted (no rebase / no force).\n\n"
                    f"Error:\n{pull_error[:1200]}"
                ),
            )
            return False

        pull_error = (
            pull_result.stderr or pull_result.stdout or "git pull failed"
        ).strip()
        safe_notify(
            name=f"pullfail:{repo_root}",
            message=f"Repository:\n{repo_root}\n\nCommand:\ngit pull --no-rebase\n\nError:\n{pull_error[:1200]}",
        )
        return False

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

    def _worker_loop(self) -> None:
        while True:
            try:
                repo_root, event_type, paths, config_snapshot, wants_pull = (
                    self._event_queue.get(timeout=0.2)
                )
                now_timestamp = time.time()

                environment = self._git_environment(config_snapshot)

                base_message = self._base_message(config_snapshot)
                add_timestamp_to_message = self._cfg_bool(
                    config_snapshot, "git_tsmsg", False
                )
                timestamp_format = self._timestamp_format(config_snapshot)

                debounce_seconds = self._cfg_float(
                    config_snapshot, "git_debounce_seconds", 0.8
                )
                git_timeout_seconds = self._cfg_float(
                    config_snapshot, "git_timeout_sec", 8.0
                )
                pull_timeout_seconds = self._cfg_float(
                    config_snapshot, "git_pull_timeout_sec", 30.0
                )
                push_timeout_seconds = self._cfg_float(
                    config_snapshot, "git_push_timeout_sec", 20.0
                )
                backoff_start_seconds = self._cfg_float(
                    config_snapshot, "git_push_backoff_start_sec", 5.0
                )
                backoff_max_seconds = self._cfg_float(
                    config_snapshot, "git_push_backoff_max_sec", 120.0
                )

                auto_merge_on_push = self._cfg_bool(
                    config_snapshot, "git_auto_merge_on_push", True
                )
                autoresolve_mode = self._cfg_str(
                    config_snapshot, "git_autoresolve", "union"
                )

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
                            wants_pull=wants_pull,
                            auto_merge_on_push=auto_merge_on_push,
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

                    existing_batch.auto_merge_on_push = auto_merge_on_push
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
                    if current_timestamp - batch.last_event_at >= float(
                        batch.debounce_seconds
                    ):
                        due_batches.append(batch)
                        del self._pending_batches[repo_root_key]

            for batch in due_batches:
                self._process_batch(batch)

    def _process_batch(self, batch: _RepoBatch) -> None:
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
                safe_notify(
                    name=f"merge-stuck:{repo_root}",
                    message=f"Repository:\n{repo_root}\n\nFound unfinished merge; auto-resolve failed; merge aborted.",
                )
                return

        opened_only = batch.event_types == {"opened"}
        if opened_only and batch.wants_pull:
            self._safe_pull_merge(
                repo_root,
                environment,
                pull_timeout_seconds=pull_timeout_seconds,
                operation_timeout_seconds=git_timeout_seconds,
                autoresolve_mode=batch.autoresolve_mode,
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
            safe_notify(
                name=f"timeout:add:{repo_root}",
                message=f"git add timed out:\n{repo_root}",
            )
            return

        if add_result.returncode != 0:
            add_error = (
                add_result.stderr or add_result.stdout or "git add failed"
            ).strip()
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
            safe_notify(
                name=f"timeout:status:{repo_root}",
                message=f"git status timed out:\n{repo_root}",
            )
            return

        if status_result.returncode != 0:
            status_error = (
                status_result.stderr or status_result.stdout or "git status failed"
            ).strip()
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
                safe_notify(
                    name=f"timeout:commit:{repo_root}",
                    message=f"git commit timed out:\n{repo_root}",
                )
                return

            if commit_result.returncode != 0:
                combined_output = (
                    (
                        (
                            (commit_result.stderr or "")
                            + "\n"
                            + (commit_result.stdout or "")
                        )
                    )
                    .strip()
                    .lower()
                )
                if "nothing to commit" not in combined_output:
                    commit_error = (
                        commit_result.stderr
                        or commit_result.stdout
                        or "git commit failed"
                    ).strip()
                    safe_notify(
                        name=f"commitfail:{repo_root}",
                        message=f"Repository:\n{repo_root}\n\nError:\n{commit_error[:1200]}",
                    )
                    return

        if batch.wants_pull:
            self._safe_pull_merge(
                repo_root,
                environment,
                pull_timeout_seconds=pull_timeout_seconds,
                operation_timeout_seconds=git_timeout_seconds,
                autoresolve_mode=batch.autoresolve_mode,
            )

        now_timestamp = time.time()
        next_allowed_timestamp = self._push_next_allowed_at.get(repo_root, 0.0)
        if now_timestamp < next_allowed_timestamp:
            return

        def run_push() -> Optional[subprocess.CompletedProcess[str]]:
            try:
                return self._run_git(
                    repo_root,
                    ["push"],
                    environment,
                    timeout_seconds=push_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                self._register_push_failure(
                    repo_root, backoff_start_seconds, backoff_max_seconds
                )
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

            if batch.auto_merge_on_push and self._push_rejected_needs_pull(
                combined_push_output
            ):
                pulled = self._safe_pull_merge(
                    repo_root,
                    environment,
                    pull_timeout_seconds=pull_timeout_seconds,
                    operation_timeout_seconds=git_timeout_seconds,
                    autoresolve_mode=batch.autoresolve_mode,
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
            safe_notify(
                name=f"pushfail:{repo_root}",
                message=f"Repository:\n{repo_root}\n\nCommand:\ngit push\n\nError:\n{push_error[:1200]}",
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

    def on_opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        ctx_path = (
            self._abs(self._to_str(ctx.path)) if getattr(ctx, "path", None) else ""
        )
        if ctx_path and self._path_is_inside_git_dir(ctx_path):
            return None

        repo_root = self._find_git_root(ctx.path)
        if not repo_root:
            return None

        if not self._cfg_bool(ctx.config, "git_auto_pull", True):
            return None

        self._enqueue(
            repo_root=repo_root,
            event_type="opened",
            paths=[ctx.path],
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

        source_path_raw = self._to_str(getattr(event, "src_path", "") or "")
        destination_path_raw = getattr(event, "dest_path", None)
        destination_path_value = (
            self._to_str(destination_path_raw)
            if destination_path_raw is not None
            else ""
        )

        source_path = self._abs(source_path_raw) if source_path_raw else ""
        destination_path = (
            self._abs(destination_path_value) if destination_path_value else ""
        )

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
            paths_to_hint = [ctx.path]
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
