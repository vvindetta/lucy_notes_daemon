from __future__ import annotations

from pathlib import Path

import lucy_notes_manager.modules.banner as banner_mod
from lucy_notes_manager.modules.banner import Banner


def test_apply_inserts_banner_from_first_line(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _txt: "ASCII\n")

    path = tmp_path / "note.md"
    path.write_text("--banner Hello\nbody\n", encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        config={"banner": "Hello", "banner_separator": "---"},
        arg_lines={"banner": [1]},
    )

    content = path.read_text(encoding="utf-8")
    assert changed == {str(path): 1}
    assert "---\nASCII\n" in content
    assert "body\n" in content


def test_apply_replaces_non_first_line_and_keeps_remaining_text(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(banner_mod.pyfiglet, "figlet_format", lambda _txt: "B\n")

    path = tmp_path / "note.md"
    path.write_text("head\n--banner X tail\n", encoding="utf-8")

    module = Banner()
    module._apply(
        path=str(path),
        config={"banner": "X", "banner_separator": "---"},
        arg_lines={"banner": [2]},
    )

    content = path.read_text(encoding="utf-8")
    assert "head\n" in content
    assert "B\n" in content
    assert "tail\n" in content


def test_apply_returns_none_when_banner_is_not_configured(tmp_path: Path):
    path = tmp_path / "note.md"
    path.write_text("text\n", encoding="utf-8")

    module = Banner()
    changed = module._apply(
        path=str(path),
        config={"banner": None, "banner_separator": "---"},
        arg_lines={},
    )
    assert changed is None
