from __future__ import annotations

import sys
from pathlib import Path

from lucy_notes_manager.lib.args import (
    delete_args_from_string,
    get_args_from_file,
    get_config_args,
    merge_known_args,
    parse_args,
    setup_config_and_cli_args,
)


def test_parse_args_handles_bool_and_nargs():
    template = [
        ("--todo", bool, False, ""),
        ("--name", str, None, ""),
        ("--tags", str, [], ""),
    ]

    known, unknown = parse_args(
        args=["--todo", "--name", "alice", "--tags", "x", "y", "--unknown"],
        template=template,
    )

    assert known["todo"] is True
    assert known["name"] == "alice"
    assert known["tags"] == ["x", "y"]
    assert unknown == ["--unknown"]


def test_get_config_args_reads_lines_and_ignores_comments(tmp_path: Path):
    cfg = tmp_path / "config.txt"
    cfg.write_text(
        "# comment\n--name jane\n\n--count 7\n",
        encoding="utf-8",
    )
    template = [
        ("--name", str, None, ""),
        ("--count", int, 0, ""),
    ]

    known, unknown = get_config_args(str(cfg), template)
    assert known["name"] == "jane"
    assert known["count"] == 7
    assert unknown == []


def test_merge_known_args_overwrites_only_when_value_is_meaningful():
    base = {"a": 1, "b": "x", "c": ["old"]}
    overwrite = {"a": None, "b": "", "c": ["new"], "d": 5}

    merged = merge_known_args(base, overwrite)

    assert merged == {"a": 1, "b": "x", "c": ["new"], "d": 5}


def test_delete_args_from_string_removes_flag_segments():
    line = '--banner "Hello world" body --todo --x=1 tail\n'
    cleaned = delete_args_from_string(line, ["--banner", "--todo", "--x"])
    assert cleaned == "body tail\n"


def test_get_args_from_file_skips_non_utf8_files(tmp_path: Path):
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    template = [
        ("--help", bool, False, ""),
    ]

    known, unknown, arg_lines = get_args_from_file(str(path), template)
    assert known == {}
    assert unknown == []
    assert arg_lines == {}


def test_setup_config_and_cli_args_keeps_config_values_when_cli_uses_defaults(
    tmp_path: Path, monkeypatch
):
    cfg = tmp_path / "daemon.cfg"
    cfg.write_text(
        '--sys-notes-dirs "/notes/a" "/notes/b"\n--sys-debug\n',
        encoding="utf-8",
    )

    template = [
        ("--sys-config-path", str, "config.txt", ""),
        ("--sys-notes-dirs", str, [], ""),
        ("--sys-debug", bool, False, ""),
    ]

    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--sys-config-path", str(cfg)],
    )

    known, unknown = setup_config_and_cli_args(template=template)

    assert unknown == []
    assert known["sys_notes_dirs"] == ["/notes/a", "/notes/b"]
    assert known["sys_debug"] is True
