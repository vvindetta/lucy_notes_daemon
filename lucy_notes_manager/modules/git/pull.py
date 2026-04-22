from __future__ import annotations

import logging
import subprocess
from typing import Dict

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.modules.git import commands
from lucy_notes_manager.modules.git.conflicts import auto_resolve_merge_conflicts

logger = logging.getLogger(__name__)


def safe_pull_merge(
    repo_root: str,
    environment: Dict[str, str],
    pull_timeout_seconds: float,
    operation_timeout_seconds: float,
    autoresolve_mode: str,
    auto_set_upstream: bool = True,
) -> bool:
    if not commands.has_upstream(repo_root, environment, operation_timeout_seconds):
        branch_name = commands.current_branch(
            repo_root, environment, operation_timeout_seconds
        )
        remote_name = commands.pick_remote(
            repo_root, environment, operation_timeout_seconds
        )

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

        remote_branch_exists = commands.remote_branch_exists(
            repo_root,
            remote_name,
            branch_name,
            environment,
            timeout_seconds=pull_timeout_seconds,
        )
        if not remote_branch_exists:
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
            commands.try_set_upstream(
                repo_root,
                remote_name,
                branch_name,
                environment,
                timeout_seconds=operation_timeout_seconds,
            )

        try:
            pull_result = commands.run_git(
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

        if commands.merge_in_progress(
            repo_root, environment, operation_timeout_seconds
        ):
            resolved = auto_resolve_merge_conflicts(
                repo_root,
                environment,
                operation_timeout_seconds,
                autoresolve_mode=autoresolve_mode,
            )
            if resolved:
                return True

            commands.run_git(
                repo_root,
                ["merge", "--abort"],
                environment,
                timeout_seconds=operation_timeout_seconds,
            )
            pull_error = (
                pull_result.stderr or pull_result.stdout or "git pull failed"
            ).strip()
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

        pull_error = (
            pull_result.stderr or pull_result.stdout or "git pull failed"
        ).strip()
        logger.error(
            "git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200]
        )
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
        pull_result = commands.run_git(
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

    if commands.merge_in_progress(repo_root, environment, operation_timeout_seconds):
        resolved = auto_resolve_merge_conflicts(
            repo_root,
            environment,
            operation_timeout_seconds,
            autoresolve_mode=autoresolve_mode,
        )
        if resolved:
            return True

        commands.run_git(
            repo_root,
            ["merge", "--abort"],
            environment,
            timeout_seconds=operation_timeout_seconds,
        )
        pull_error = (
            pull_result.stderr or pull_result.stdout or "git pull failed"
        ).strip()
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

    pull_error = (
        pull_result.stderr or pull_result.stdout or "git pull failed"
    ).strip()
    logger.error(
        "git pull failed | repo=%s | error=%s", repo_root, pull_error[:1200]
    )
    safe_notify(
        name=f"pullfail:{repo_root}",
        message=(
            f"Repository:\n{repo_root}\n\n"
            f"Command:\ngit pull --no-rebase\n\n"
            f"Error:\n{pull_error[:1200]}"
        ),
    )
    return False
