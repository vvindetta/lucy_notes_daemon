from __future__ import annotations

from pathlib import Path

from watchdog.events import FileMovedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.todo_formatter import TodoFormatter


def test_apply_formats_plain_dash_items(tmp_path: Path):
    note = tmp_path / "todo.md"
    note.write_text("- task\n- [ ] already\ntext\n", encoding="utf-8")

    module = TodoFormatter()
    changed = module._apply(
        path=str(note),
        config={"todo": True},
        arg_lines={},
    )

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "- [ ] task\n- [ ] already\ntext\n"


def test_apply_returns_none_when_disabled(tmp_path: Path):
    note = tmp_path / "todo.md"
    note.write_text("- task\n", encoding="utf-8")

    module = TodoFormatter()
    changed = module._apply(path=str(note), config={"todo": False}, arg_lines={})
    assert changed is None


def test_event_methods_delegate_to_apply(tmp_path: Path, monkeypatch):
    note = tmp_path / "todo.md"
    note.write_text("- x\n", encoding="utf-8")
    module = TodoFormatter()

    called = []
    monkeypatch.setattr(
        module,
        "_apply",
        lambda **kwargs: called.append(kwargs["path"]) or {kwargs["path"]: 1},
    )

    ctx = Context(path=str(note), config={"todo": True}, arg_lines={})
    system = System(
        event=FileMovedEvent(str(note), str(note)),
        global_template=[],
        modules=[module],
    )
    result = module.on_moved(ctx, system)

    assert called == [str(note)]
    assert result == {str(note): 1}
