from __future__ import annotations

from datetime import datetime
from pathlib import Path

import lucy_notes_manager.modules.renamer as renamer_mod
from lucy_notes_manager.modules.renamer import Renamer


def test_apply_manual_renames_file(tmp_path: Path):
    old_path = tmp_path / "old.md"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_manual(path=str(old_path), config={"r": "new.md"})

    assert changed is not None
    assert (tmp_path / "new.md").exists()
    assert not old_path.exists()


def test_apply_manual_skips_when_target_exists(tmp_path: Path):
    old_path = tmp_path / "old.md"
    new_path = tmp_path / "new.md"
    old_path.write_text("x\n", encoding="utf-8")
    new_path.write_text("y\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_manual(path=str(old_path), config={"r": "new.md"})
    assert changed is None
    assert old_path.exists()
    assert new_path.exists()


def test_apply_auto_on_create_uses_date_name(tmp_path: Path, monkeypatch):
    class _FakeDatetime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 10, 30, 0)

    monkeypatch.setattr(renamer_mod, "datetime", _FakeDatetime)

    old_path = tmp_path / "t"
    old_path.write_text("x\n", encoding="utf-8")

    module = Renamer()
    changed = module._apply_auto_on_create(
        path=str(old_path),
        config={"auto_rename": True},
    )

    assert changed is not None
    assert (tmp_path / "21-04.txt").exists()
    assert not old_path.exists()
