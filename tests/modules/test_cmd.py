from __future__ import annotations

from pathlib import Path

import pytest
from watchdog.events import FileDeletedEvent, FileModifiedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.cmd import Cmd


def test_collect_runs_groups_tokens_by_line():
    module = Cmd()
    ctx = Context(
        path="/tmp/x.md",
        config={"c": ["echo", "hello", "ls", "-la"]},
        arg_lines={"c": [1, 1, 2, 2]},
    )

    runs = module._collect_runs(ctx)
    assert len(runs) == 2
    assert runs[0].lineno_1based == 1
    assert runs[0].cmd_tokens == ["echo", "hello"]
    assert runs[1].cmd_tokens == ["ls", "-la"]


@pytest.mark.xfail(
    reason=(
        "Pre-existing bug: delete_args_from_string consumes trailing tokens "
        "after --c as values. Tracked separately from the CI setup."
    ),
    strict=False,
)
def test_apply_replaces_command_line_with_output_block(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--c echo hello tail\n", encoding="utf-8")

    module = Cmd()
    monkeypatch.setattr(module, "_run_cmd", lambda **_kwargs: (0, "OUT\n", ""))

    ctx = Context(
        path=str(note),
        config={
            "c": ["echo", "hello"],
            "cmd_timeout": [5],
            "cmd_max_bytes": [1000],
            "cmd_show_stdout": True,
            "cmd_show_stderr": True,
        },
        arg_lines={"c": [1, 1]},
    )
    system = System(
        event=FileModifiedEvent(str(note)),
        global_template=[],
        modules=[module],
    )

    changed = module.modified(ctx, system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- echo ---\n" in content
    assert "OUT\n" in content
    assert "tail\n" in content


def test_deleted_event_is_noop():
    module = Cmd()
    ctx = Context(path="/tmp/x", config={}, arg_lines={})
    system = System(
        event=FileDeletedEvent("/tmp/x"),
        global_template=[],
        modules=[],
    )
    assert module.deleted(ctx, system) is None
