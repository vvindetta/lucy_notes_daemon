from __future__ import annotations

from datetime import datetime

import pytest
from watchdog.events import FileMovedEvent, FileOpenedEvent

import lucy_notes_manager.modules.git as git_mod
from lucy_notes_manager.modules.abstract_module import Context, System
from lucy_notes_manager.modules.git import Git, _RepoBatch
from lucy_notes_manager.modules.git.worker import should_force_flush_batch


@pytest.fixture
def git_module(monkeypatch):
    class _DummyThread:
        def __init__(self, target=None, daemon=None):
            self.target = target
            self.daemon = daemon
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(git_mod.threading, "Thread", _DummyThread)
    return Git()


def test_git_module_is_marked_experimental(git_module):
    assert git_module.experimental is True


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

    monkeypatch.setattr(git_mod, "datetime", _FakeDateTime)

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
        max_batch_seconds=8.0,
        event_types={"modified", "created"},
        hinted_paths={"/repo/hinted.md"},
    )

    msg = git_module._build_commit_message(batch, ["/repo/a.md", "/repo/b.md"])
    assert msg.startswith("Auto: ")
    assert "a.md, b.md" in msg
    assert msg.endswith("[2026]")


def test_pull_allowed_with_progression(git_module, monkeypatch):
    times = iter([0.0, 1.0, 30.0])
    monkeypatch.setattr(git_mod.time, "time", lambda: next(times))

    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is True
    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is False
    assert git_module._pull_allowed_with_progression("/r", 10.0, 40.0) is True


def test_register_push_failure_updates_backoff(git_module, monkeypatch):
    monkeypatch.setattr(git_mod.time, "time", lambda: 100.0)
    git_module._register_push_failure("/repo", backoff_start_seconds=5.0, backoff_max_seconds=20.0)

    assert git_module._push_backoff_seconds["/repo"] == 10.0
    assert git_module._push_next_allowed_at["/repo"] == 110.0


def test_update_periodic_pull_state_default_disabled(git_module):
    git_module._update_periodic_pull_state(
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 0.0},
        now_timestamp=100.0,
    )
    assert "/repo" not in git_module._periodic_pull_next_at
    assert "/repo" not in git_module._periodic_pull_intervals_seconds
    assert "/repo" not in git_module._periodic_pull_configs


def test_update_periodic_pull_state_enables_and_emits_due_event(git_module):
    git_module._update_periodic_pull_state(
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 2.0},
        now_timestamp=100.0,
    )

    assert git_module._periodic_pull_intervals_seconds["/repo"] == 7200.0
    assert git_module._periodic_pull_next_at["/repo"] == 7300.0

    assert git_module._collect_due_periodic_pull_events(now_timestamp=7299.0) == []

    events = git_module._collect_due_periodic_pull_events(now_timestamp=7300.0)
    assert events == [
        ("/repo", "scheduled_pull", [], {"git_auto_pull_every_hours": 2.0}, True)
    ]
    assert git_module._periodic_pull_next_at["/repo"] == 14500.0


def test_update_periodic_pull_state_turns_off_existing_schedule(git_module):
    git_module._update_periodic_pull_state(
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 1.0},
        now_timestamp=100.0,
    )
    git_module._update_periodic_pull_state(
        repo_root="/repo",
        config_snapshot={"git_auto_pull_every_hours": 0.0},
        now_timestamp=200.0,
    )

    assert "/repo" not in git_module._periodic_pull_next_at
    assert "/repo" not in git_module._periodic_pull_intervals_seconds
    assert "/repo" not in git_module._periodic_pull_configs


def test_scheduled_pull_batch_only_runs_pull(git_module, monkeypatch):
    pull_calls: list[tuple[str, float, float]] = []

    monkeypatch.setattr(git_module, "_merge_in_progress", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        git_module,
        "_pull_allowed_with_progression",
        lambda **_kwargs: True,
    )

    def _safe_pull_merge(repo_root, _environment, pull_timeout_seconds, operation_timeout_seconds, **_kwargs):
        pull_calls.append((repo_root, pull_timeout_seconds, operation_timeout_seconds))
        return True

    monkeypatch.setattr(git_module, "_safe_pull_merge", _safe_pull_merge)
    monkeypatch.setattr(
        git_module,
        "_run_git",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("scheduled pull-only batches must not run add/commit/push")
        ),
    )

    batch = _RepoBatch(
        repo_root="/repo",
        base_message="Auto",
        add_timestamp_to_message=False,
        timestamp_format="%Y",
        environment={},
        debounce_seconds=0.5,
        git_timeout_seconds=5.0,
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
        pull_cooldown_min_seconds=1.0,
        pull_cooldown_max_seconds=4.0,
        max_batch_seconds=8.0,
        wants_pull=True,
        event_types={"scheduled_pull"},
    )

    git_module._process_batch(batch)
    assert pull_calls == [("/repo", 6.0, 5.0)]


def test_should_force_flush_batch_for_non_pull_batches():
    batch = _RepoBatch(
        repo_root="/repo",
        base_message="Auto",
        add_timestamp_to_message=False,
        timestamp_format="%Y",
        environment={},
        debounce_seconds=0.5,
        git_timeout_seconds=5.0,
        pull_timeout_seconds=6.0,
        push_timeout_seconds=7.0,
        backoff_start_seconds=2.0,
        backoff_max_seconds=8.0,
        pull_cooldown_min_seconds=1.0,
        pull_cooldown_max_seconds=4.0,
        max_batch_seconds=5.0,
        first_event_at=10.0,
        event_types={"opened"},
    )

    assert should_force_flush_batch(batch, now_timestamp=20.0) is False

    batch.event_types = {"opened", "modified"}
    assert should_force_flush_batch(batch, now_timestamp=20.0) is True


def test_opened_enqueues_when_repo_exists(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
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


def test_handle_moved_uses_src_and_dest_paths_for_hints(git_module, monkeypatch):
    recorded = {}
    monkeypatch.setattr(git_mod, "find_parent_with", lambda _p, _m: "/repo")
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
