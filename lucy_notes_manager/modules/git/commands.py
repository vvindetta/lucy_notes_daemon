from __future__ import annotations

import subprocess
from typing import Dict, Optional


def run_git(
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
    repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> bool:
    result = run_git(
        repo_root,
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def current_branch(
    repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = run_git(
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
    repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> Optional[str]:
    result = run_git(repo_root, ["remote"], environment, timeout_seconds)
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
    repo_root: str,
    remote_name: str,
    branch_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    result = run_git(
        repo_root,
        ["ls-remote", "--heads", remote_name, branch_name],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def try_set_upstream(
    repo_root: str,
    remote_name: str,
    branch_name: str,
    environment: Dict[str, str],
    timeout_seconds: float,
) -> bool:
    result = run_git(
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
    repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> bool:
    result = run_git(
        repo_root,
        ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
        environment,
        timeout_seconds,
    )
    return result.returncode == 0 and bool((result.stdout or "").strip())


def conflicted_files(
    repo_root: str, environment: Dict[str, str], timeout_seconds: float
) -> list[str]:
    result = run_git(
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
