from __future__ import annotations

import os
from typing import Optional


def abs_expand_path(path_value: str) -> str:
    return os.path.abspath(os.path.expanduser(path_value))


def canonical_path(path_value: str) -> str:
    return os.path.realpath(os.path.normpath(abs_expand_path(path_value)))


def path_has_component(path_value: str, component: str) -> bool:
    path_components = abs_expand_path(path_value).split(os.sep)
    return component in path_components


def find_parent_with(path_value: str, marker_name: str) -> Optional[str]:
    """
    Walk up from a file or directory path and return the first parent directory
    that contains `marker_name` as a directory.

    Example:
        find_parent_with("/notes/repo/docs/todo.md", ".git") -> "/notes/repo"

    Returns None when no such parent exists.
    """
    current_path = abs_expand_path(path_value)
    if not os.path.isdir(current_path):
        current_path = os.path.dirname(current_path)

    while True:
        if os.path.isdir(os.path.join(current_path, marker_name)):
            return current_path
        parent_path = os.path.dirname(current_path)
        if parent_path == current_path:
            return None
        current_path = parent_path
