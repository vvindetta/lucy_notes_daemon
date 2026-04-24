from __future__ import annotations

from pathlib import Path

from lucy_notes_manager.lib.path import (
    abs_expand_path,
    canonical_path,
    find_parent_with,
    path_has_component,
)


def test_abs_expand_path_and_canonical_path(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "note.md"
    target.parent.mkdir(parents=True)
    target.write_text("x\n", encoding="utf-8")

    odd = str(tmp_path / "a" / "b" / ".." / "b" / "note.md")
    assert abs_expand_path(odd).endswith("note.md")
    assert canonical_path(odd) == str(target.resolve())


def test_path_has_component_detects_git_dir(tmp_path: Path) -> None:
    git_cfg = tmp_path / ".git" / "config"
    git_cfg.parent.mkdir(parents=True)
    git_cfg.write_text("x\n", encoding="utf-8")

    assert path_has_component(str(git_cfg), ".git") is True
    assert path_has_component(str(tmp_path / "notes.md"), ".git") is False


def test_find_parent_with_git_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "x" / "y" / "note.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("x\n", encoding="utf-8")

    assert find_parent_with(str(nested), ".git") == str(repo.resolve())
    assert find_parent_with(str(tmp_path / "outside.txt"), ".git") is None
