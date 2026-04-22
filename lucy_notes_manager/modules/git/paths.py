from __future__ import annotations

import os
from typing import Union

PathLike = Union[str, bytes]


def to_str(path_value: PathLike) -> str:
    if isinstance(path_value, bytes):
        return path_value.decode(errors="surrogateescape")
    return path_value


def abs_path(path_value: str) -> str:
    return os.path.abspath(os.path.expanduser(path_value))


def path_is_inside_git_dir(path_value: str) -> bool:
    path_components = os.path.abspath(path_value).split(os.sep)
    return ".git" in path_components


def find_git_root(path_value: PathLike) -> str | None:
    current_path = os.path.abspath(to_str(path_value))
    if not os.path.isdir(current_path):
        current_path = os.path.dirname(current_path)

    while True:
        if os.path.isdir(os.path.join(current_path, ".git")):
            return current_path
        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            return None
        current_path = parent_path


def git_environment(config: dict) -> dict:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"

    key_path_raw = config.get("git_key")
    if not key_path_raw:
        return environment

    key_path = abs_path(str(key_path_raw))
    environment["GIT_SSH_COMMAND"] = (
        f'ssh -i "{key_path}" '
        f"-o IdentitiesOnly=yes "
        f"-o BatchMode=yes "
        f"-o StrictHostKeyChecking=accept-new"
    )
    return environment
