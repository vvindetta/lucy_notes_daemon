from __future__ import annotations

from pathlib import Path

from watchdog.events import FileModifiedEvent

from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.sys import Sys


def test_man_lines_list_and_specific_name():
    module = Sys()
    system = System(
        event=FileModifiedEvent("/tmp/x"),
        global_template=[
            ("--mods", bool, False, "mods help"),
            ("--todo", bool, False, "todo help"),
        ],
        modules=[],
    )

    list_lines = module._man_lines(system, ["list"])
    one_lines = module._man_lines(system, ["todo"])

    assert any("--mods" in line for line in list_lines)
    assert any("--todo:" in line for line in one_lines)


def test_apply_inserts_block_for_first_line_flags(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("--mods --help\nbody\n", encoding="utf-8")

    module = Sys()
    ctx = Context(
        path=str(note),
        config={
            "mods": True,
            "help": True,
            "config": False,
            "sys_event": False,
            "man": [],
        },
        arg_lines={"mods": [1], "help": [1]},
    )
    system = System(
        event=FileModifiedEvent(str(note)),
        global_template=[("--mods", bool, False, ""), ("--help", bool, False, "")],
        modules=[module],
    )

    changed = module.on_modified(ctx, system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- mods+help ---\n" in content
    assert "* --mods: print loaded modules and their priorities\n" in content


def test_apply_non_first_line_replacement_with_man(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("head\n--man list\n", encoding="utf-8")

    module = Sys()
    ctx = Context(
        path=str(note),
        config={
            "mods": False,
            "help": False,
            "config": False,
            "sys_event": False,
            "man": ["list"],
        },
        arg_lines={"man": [2]},
    )
    system = System(
        event=FileModifiedEvent(str(note)),
        global_template=[("--man", str, None, "manual")],
        modules=[],
    )

    changed = module._apply(ctx=ctx, system=system)
    content = note.read_text(encoding="utf-8")

    assert changed == {str(note): 1}
    assert "--- man ---\n" in content
    assert "* --man type=str default=None\n" in content
