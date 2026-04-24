from __future__ import annotations

import logging
import os
import subprocess
from typing import Dict, Optional

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.path import abs_expand_path

logger = logging.getLogger(__name__)


def git_environment(self, config: dict) -> Dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"

    key_path_raw = config["git_key"].strip()
    if not key_path_raw:
        return environment

    key_path = abs_expand_path(key_path_raw)
    environment["GIT_SSH_COMMAND"] = (
        f'ssh -i "{key_path}" '
        f"-o IdentitiesOnly=yes "
        f"-o BatchMode=yes "
        f"-o StrictHostKeyChecking=accept-new"
    )
    return environment


def run_git(
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


def has_upstream(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> bool:
    result = self._run_git(
        repo_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def current_branch(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = self._run_git(
        repo_root,
        ["rev-parse", "--abbrev-ref", "HEAD"],
        environment,
        timeout_seconds,
    )
    branch_name = (result.stdout or "").strip()
    if result.returncode != 0 or not branch_name or branch_name == "HEAD":
        return None
    return branch_name


def pick_remote(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = self._run_git(repo_root, ["remote"], environment, timeout_seconds)
    if result.returncode != 0:
        return None
    remote_names = [
        line_text.strip()
        for line_text in (result.stdout or "").splitlines()
        if line_text.strip()
    ]
    if not remote_names:
        return None
    if "origin" in remote_names:
        return "origin"
    return remote_names[0]


def remote_branch_exists(
    self,
    repo_root: str,
    remote_name: str,
    branch_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    result = self._run_git(
        repo_root,
        ["ls-remote", "--heads", remote_name, branch_name],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def try_set_upstream(
    self,
    repo_root: str,
    remote_name: str,
    branch_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    result = self._run_git(
        repo_root,
        [
            "branch",
            "--set-upstream-to",
            f"{remote_name}/{branch_name}",
            branch_name,
        ],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0


def merge_in_progress(
    self, repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> bool:
    result = self._run_git(
        repo_root,
        ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def conflicted_files(
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


def auto_resolve_merge_conflicts(
    self,
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: str,
) -> bool:
    normalized_mode = (autoresolve_mode or "none").strip().lower()
    if normalized_mode not in {"none", "ours", "theirs", "union"}:
        normalized_mode = "none"

    conflicted_paths = self._conflicted_files(repo_root, environment, timeout_seconds)
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
                logger.error(
                    "auto-resolve checkout failed | repo=%s | file=%s | mode=%s | err=%s",
                    repo_root,
                    relative_path,
                    normalized_mode,
                    (checkout_result.stderr or checkout_result.stdout or "")[:1200],
                )
                return False

        elif normalized_mode == "union":
            try:
                if os.path.isfile(absolute_path):
                    with open(
                        absolute_path,
                        "r",
                        encoding="utf-8",
                        errors="surrogateescape",
                    ) as file_obj:
                        file_text = file_obj.read()
                    resolved_text = self._union_resolve_text(file_text)
                    if resolved_text is None:
                        checkout_result = self._run_git(
                            repo_root,
                            ["checkout", "--ours", "--", relative_path],
                            environment,
                            timeout_seconds,
                        )
                        if checkout_result.returncode != 0:
                            logger.error(
                                "auto-resolve union fallback checkout failed | repo=%s | file=%s | err=%s",
                                repo_root,
                                relative_path,
                                (
                                    checkout_result.stderr
                                    or checkout_result.stdout
                                    or ""
                                )[:1200],
                            )
                            return False
                    else:
                        with open(
                            absolute_path,
                            "w",
                            encoding="utf-8",
                            errors="surrogateescape",
                        ) as file_obj:
                            file_obj.write(resolved_text)
                else:
                    checkout_result = self._run_git(
                        repo_root,
                        ["checkout", "--ours", "--", relative_path],
                        environment,
                        timeout_seconds,
                    )
                    if checkout_result.returncode != 0:
                        logger.error(
                            "auto-resolve union non-file checkout failed | repo=%s | path=%s | err=%s",
                            repo_root,
                            relative_path,
                            (
                                checkout_result.stderr or checkout_result.stdout or ""
                            )[:1200],
                        )
                        return False
            except OSError:
                logger.exception(
                    "auto-resolve union IO failed | repo=%s | file=%s",
                    repo_root,
                    relative_path,
                )
                return False

        add_result = self._run_git(
            repo_root, ["add", "--", relative_path], environment, timeout_seconds
        )
        if add_result.returncode != 0:
            logger.error(
                "auto-resolve git add failed | repo=%s | file=%s | err=%s",
                repo_root,
                relative_path,
                (add_result.stderr or add_result.stdout or "")[:1200],
            )
            return False

    commit_result = self._run_git(
        repo_root, ["commit", "--no-edit"], environment, timeout_seconds
    )
    if commit_result.returncode != 0:
        logger.error(
            "auto-resolve commit failed | repo=%s | err=%s",
            repo_root,
            (commit_result.stderr or commit_result.stdout or "")[:1200],
        )
    return commit_result.returncode == 0


def safe_pull_merge(
    self,
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    autoresolve_mode: str,
    auto_set_upstream: bool = True,
) -> bool:
    if not self._has_upstream(repo_root, environment, operation_timeout_seconds):
        branch_name = self._current_branch(repo_root, environment, operation_timeout_seconds)
        remote_name = self._pick_remote(repo_root, environment, operation_timeout_seconds)

        if not branch_name or not remote_name:
            logger.warning(
                "no upstream and cannot infer remote/branch; skip auto-pull | repo=%s",
                repo_root,
            )
            safe_notify(
                name=f"pull-noupstream:{repo_root}",
                message=(
                    f"Repository:\n{repo_root}\n\n"
                    f"No upstream configured and cannot infer remote/branch; skip pull."
                ),
            )
            return False

        remote_branch_exists_value = self._remote_branch_exists(
            repo_root,
            remote_name,
            branch_name,
            environment,
            timeout_seconds=pull_timeout_seconds,
        )
        if not remote_branch_exists_value:
            logger.warning(
                "no upstream and remote branch missing; skip pull | repo=%s | remote=%s | branch=%s",
                repo_root,
                remote_name,
                branch_name,
            )
            safe_notify(
                name=f"pull-noremotebranch:{repo_root}",
                message=(
                    f"Repository:\n{repo_root}\n\n"
                    f"No upstream configured and remote branch does not exist:\n"
                    f"{remote_name}/{branch_name}\n\n"
                    f"Skip pull."
                ),
            )
            return False

        if auto_set_upstream:
            self._try_set_upstream(
                repo_root,
                remote_name,
                branch_name,
                environment,
                timeout_seconds=operation_timeout_seconds,
            )

        try:
            pull_result = self._run_git(
                repo_root,
                ["pull", "--no-rebase", "--no-edit", remote_name, branch_name],
                environment,
                timeout_seconds=pull_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            logger.error("git pull timed out | repo=%s", repo_root)
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
            pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
            logger.error(
                "git pull conflict; auto-resolve failed; merge aborted | repo=%s | error=%s",
                repo_root,
                pull_error[:1200],
            )
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

        pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
        logger.error("git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200])
        safe_notify(
            name=f"pullfail:{repo_root}",
            message=(
                f"Repository:\n{repo_root}\n\n"
                f"Command:\ngit pull --no-rebase {remote_name} {branch_name}\n\n"
                f"Error:\n{pull_error[:1200]}"
            ),
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
        logger.error("git pull timed out | repo=%s", repo_root)
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
        pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
        logger.error(
            "git pull conflict; auto-resolve failed; merge aborted | repo=%s | error=%s",
            repo_root,
            pull_error[:1200],
        )
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

    pull_error = (pull_result.stderr or pull_result.stdout or "git pull failed").strip()
    logger.error("git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200])
    safe_notify(
        name=f"pullfail:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Command:\ngit pull --no-rebase\n\n"
            f"Error:\n{pull_error[:1200]}"
        ),
    )
    return False
