from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System


class _ModA(AbstractModule):
    name = "a"
    priority = 20

    def __init__(self):
        self.calls = 0

    def on_modified(self, ctx: Context, system: System):
        self.calls += 1
        return {ctx.path: 1}


class _ModB(AbstractModule):
    name = "b"
    priority = 30

    # no on_modified() override on purpose


class _ModC(AbstractModule):
    name = "c"
    priority = 40

    def __init__(self):
        self.calls = 0

    def on_modified(self, ctx: Context, system: System):
        self.calls += 1
        return {ctx.path: 2}


def test_parse_priority_list_rejects_bad_items():
    manager = ModuleManager(modules=[_ModA()], args=[])
    with pytest.raises(ValueError):
        manager._parse_priority_list(["broken-item"])


def test_init_sorts_modules_by_priority_override():
    a, c = _ModA(), _ModC()
    manager = ModuleManager(
        modules=[c, a],
        args=["--sys-priority", "c=1", "a=9"],
    )
    assert [m.name for m in manager.modules] == ["c", "a"]


def test_run_respects_exclude_and_force_and_event_implementation(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("hello\n", encoding="utf-8")
    event = FileModifiedEvent(str(note))

    a, b, c = _ModA(), _ModB(), _ModC()
    manager = ModuleManager(modules=[a, b, c], args=["--exclude", "a"])
    ignore_paths = manager.run(str(note), event)

    assert a.calls == 0
    assert c.calls == 1
    assert ignore_paths == {str(note.resolve()): 2}

    a2, c2 = _ModA(), _ModC()
    manager_force = ModuleManager(
        modules=[a2, c2],
        args=["--exclude", "a", "--force", "a"],
    )
    ignore_paths_force = manager_force.run(str(note), event)

    assert a2.calls == 1
    assert c2.calls == 1
    assert ignore_paths_force == {str(note.resolve()): 3}
