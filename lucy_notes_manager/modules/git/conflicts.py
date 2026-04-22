from __future__ import annotations

import logging
import os
from typing import Dict

from lucy_notes_manager.modules.git import commands
from lucy_notes_manager.modules.git.parsing import union_resolve_text

logger = logging.getLogger(__name__)


def auto_resolve_merge_conflicts(
    repo_root: str,
    environment: Dict[str, str],
    timeout_seconds: float,
    autoresolve_mode: str,
) -> bool:
    normalized_mode = (autoresolve_mode or "none").strip().lower()
    if normalized_mode not in {"none", "ours", "theirs", "union"}:
        normalized_mode = "none"

    conflicted_paths = commands.conflicted_files(
        repo_root, environment, timeout_seconds
    )
    if not conflicted_paths or normalized_mode == "none":
        return False

    for relative_path in conflicted_paths:
        absolute_path = os.path.join(repo_root, relative_path)

        if normalized_mode in {"ours", "theirs"}:
            side_argument = "--ours" if normalized_mode == "ours" else "--theirs"
            checkout_result = commands.run_git(
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
                    file_text = open(
                        absolute_path,
                        "r",
                        encoding="utf-8",
                        errors="surrogateescape",
                    ).read()
                    resolved_text = union_resolve_text(file_text)
                    if resolved_text is None:
                        checkout_result = commands.run_git(
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
                        open(
                            absolute_path,
                            "w",
                            encoding="utf-8",
                            errors="surrogateescape",
                        ).write(resolved_text)
                else:
                    checkout_result = commands.run_git(
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
                                checkout_result.stderr
                                or checkout_result.stdout
                                or ""
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

        add_result = commands.run_git(
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

    commit_result = commands.run_git(
        repo_root, ["commit", "--no-edit"], environment, timeout_seconds
    )
    if commit_result.returncode != 0:
        logger.error(
            "auto-resolve commit failed | repo=%s | err=%s",
            repo_root,
            (commit_result.stderr or commit_result.stdout or "")[:1200],
        )
    return commit_result.returncode == 0
