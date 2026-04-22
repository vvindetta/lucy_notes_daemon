from __future__ import annotations

from datetime import datetime

import pytest
from watchdog.events import FileMovedEvent, FileOpenedEvent

import lucy_notes_manager.modules.git as git_pkg
import lucy_notes_manager.modules.git.module as git_module_mod
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.git import Git, _RepoBatch


@pytest.fixture
def git_module(monkeypatch):
    class _DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(git_module_mod.threading, "Thread", _DummyThread)
    return Git()


def test_parse_porcelain_paths_handles_regular_and_renamed(git_module):
    text = " M a.txt\nR  old.md -> new.md\n?? x.py\n"
    assert git_module._parse_porcelain_paths(text) == ["a.txt", "new.md", "x.py"]


def test_push_rejected_needs_pull_detects_common_messages(git_module):
    assert git_module._push_rejected_needs_pull("non-fast-forward update rejected")
    assert not git_module._push_rejected_needs_pull("everything up-to-date")


def test_union_resolve_text_merges_conflict_content(git_module):
    merged = git_module._union_resolve_text(
        "A\n<<<<<<< ours\none\n=======\ntwo\n>>>>>>> theirs\nB\n"
    )
    assert merged == "A\none\ntwo\nB\n"


def test_build_commit_message_includes_event_summary_and_names(git_module, monkeypatch):
    class _FakeDateTime:
        @classmethod
        def now(cls):
            return datetime(2026, 4, 21, 12, 0, 0)

    monkeypatch.setattr(git_module_mod, "datetime", _FakeDateTime)

    batch = _RepoBatch(
        repo_root="/repo",
        base_message="Auto",
        add_timestamp_to_message=True,
        timestamp_format="%Y",
        environment={},
        debounce_seconds=0.5,
        git_timeout_seconds=5.0,
        pull_timeout_seconds=5.0,
        push_timeout_seconds=5.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
        pull_cooldown_min_seconds=1.0,
        pull_cooldown_max_seconds=4.0,
        event_types={"modified", "created"},
        hinted_paths={"/repo/hinted.md"},
    )

    msg = git_module._build_commit_message(batch, ["/repo/a.md", "/repo/b.md"])
    assert msg.startswith("Auto: ")
    assert "a.md, b.md" in msg
    assert msg.endswith("[2026]")


def test_pull_allowed_with_progression(git_module, monkeypatch):
    times = iter([0.0, 1.0, 30.0])
    monkeypatch.setattr(git_module_mod.time, "time", lambda: next(times))

    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is True
    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is False
    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is True


def test_register_push_failure_updates_backoff(git_module, monkeypatch):
    monkeypatch.setattr(git_module_mod.time, "time", lambda: 100.0)
    git_module._register_push_failure(
        "/repo", backoff_start_seconds=5.0, backoff_max_seconds=20.0
    )

    assert git_module._push_backoff_seconds["/repo"] == 10.0
    assert git_module._push_next_allowed_at["/repo"] == 110.0


def test_opened_enqueues_when_repo_exists(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        git_module_mod.paths, "find_git_root", lambda _p: "/repo"
    )
    monkeypatch.setattr(
        git_module,
        "_enqueue",
        lambda **kwargs: recorded.update(kwargs),
    )

    ctx = Context(path="/repo/note.md", config={"git_auto_pull": True}, arg_lines={})
    system = System(
        event=FileOpenedEvent("/repo/note.md"),
        global_template=[],
        modules=[git_module],
    )
    git_module.opened(ctx, system)

    assert recorded["repo_root"] == "/repo"
    assert recorded["event_type"] == "opened"
    assert recorded["wants_pull"] is True


def test_opened_skipped_when_update_source_not_poll(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        git_module_mod.paths, "find_git_root", lambda _p: "/repo"
    )
    monkeypatch.setattr(
        git_module,
        "_enqueue",
        lambda **kwargs: recorded.update(kwargs),
    )

    ctx = Context(
        path="/repo/note.md",
        config={"git_auto_pull": True, "git_update_source": "webhook"},
        arg_lines={},
    )
    system = System(
        event=FileOpenedEvent("/repo/note.md"),
        global_template=[],
        modules=[git_module],
    )
    git_module.opened(ctx, system)

    assert recorded == {}


def test_handle_moved_uses_src_and_dest_paths_for_hints(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        git_module_mod.paths, "find_git_root", lambda _p: "/repo"
    )
    monkeypatch.setattr(
        git_module,
        "_enqueue",
        lambda **kwargs: recorded.update(kwargs),
    )

    event = FileMovedEvent("/repo/old.md", "/repo/new.md")
    ctx = Context(path="/repo/new.md", config={}, arg_lines={})
    system = System(event=event, global_template=[], modules=[git_module])

    git_module._handle(ctx, system, "moved")
    assert recorded["paths"] == ["/repo/old.md", "/repo/new.md"]
    assert recorded["event_type"] == "moved"


def test_public_api_reexports_from_package():
    """Backwards compatibility: Git and _RepoBatch importable from the package root."""
    assert git_pkg.Git is Git
    assert git_pkg._RepoBatch is _RepoBatch


def test_trigger_pull_enqueues_event(git_module):
    recorded = []
    original_put = git_module._event_queue.put
    git_module._event_queue.put = lambda item: recorded.append(item)
    try:
        git_module.trigger_pull("/repo", {"git_auto_pull": True})
    finally:
        git_module._event_queue.put = original_put

    assert len(recorded) == 1
    repo_root, event_type, path_items, config_snapshot, wants_pull = recorded[0]
    assert repo_root == "/repo"
    assert event_type == "opened"
    assert path_items == []
    assert wants_pull is True
