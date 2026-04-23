from __future__ import annotations

import os
from typing import Optional

from lucy_notes_manager.modules.git.types import PathLike


def to_str(path_value: PathLike) -> str:
    if isinstance(path_value, bytes):
        return path_value.decode(errors="surrogateescape")
    return path_value


def abs_path(path_value: str) -> str:
    return os.path.abspath(os.path.expanduser(path_value))


def path_is_inside_git_dir(path_value: str) -> bool:
    path_components = os.path.abspath(path_value).split(os.sep)
    return ".git" in path_components


def find_git_root(path_value: PathLike) -> Optional[str]:
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


def parse_porcelain_paths(porcelain_text: str) -> list[str]:
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


def push_rejected_needs_pull(output_text: str) -> bool:
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


def union_resolve_text(file_content: str) -> Optional[str]:
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
            if line_index >= len(lines) or not lines[line_index].startswith("======="):
                return None
            line_index += 1

            theirs_lines: list[str] = []
            while line_index < len(lines) and not lines[line_index].startswith(
                ">>>>>>> "
            ):
                theirs_lines.append(lines[line_index])
                line_index += 1
            if line_index >= len(lines) or not lines[line_index].startswith(">>>>>>> "):
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
