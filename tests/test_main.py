from __future__ import annotations

import runpy
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import lucy_notes_manager.file_handler as file_handler_mod
import lucy_notes_manager.lib.args as args_mod
import lucy_notes_manager.module_manager as module_manager_mod
import watchdog.observers as observers_mod


def _main_path() -> str:
    return str((Path(__file__).resolve().parents[1] / "main.py"))


@dataclass
class _ObserverState:
    scheduled: list[tuple[object, str, bool]] = field(default_factory=list)
    started: bool = False
    stopped: bool = False
    joined: bool = False


def test_main_schedules_observer_and_stops_cleanly(tmp_path: Path, monkeypatch):
    state = _ObserverState()

    class FakeObserver:
        def schedule(self, handler, path, recursive):
            state.scheduled.append((handler, path, recursive))

        def start(self):
            state.started = True

        def stop(self):
            state.stopped = True

        def join(self):
            state.joined = True

    class FakeFileHandler:
        def __init__(self, modules, open_cooldown_seconds):
            self.modules = modules
            self.open_cooldown_seconds = open_cooldown_seconds

    class FakeModuleManager:
        def __init__(self, modules, args):
            self.modules = modules
            self.args = args

    monkeypatch.setattr(observers_mod, "Observer", FakeObserver)
    monkeypatch.setattr(file_handler_mod, "FileHandler", FakeFileHandler)
    monkeypatch.setattr(module_manager_mod, "ModuleManager", FakeModuleManager)
    monkeypatch.setattr(
        args_mod,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_debug": False,
                "sys_logging_format": "%(message)s",
                "sys_notes_dirs": [str(tmp_path)],
                "sys_on_open_cooldown": 20,
                "sys_enable_experimental_modules": False,
            },
            [],
        ),
    )
    monkeypatch.setattr(time, "sleep", lambda _sec: (_ for _ in ()).throw(KeyboardInterrupt()))

    runpy.run_path(_main_path(), run_name="__main__")

    assert state.started is True
    assert state.stopped is True
    assert state.joined is True
    assert len(state.scheduled) == 1
    assert state.scheduled[0][1] == str(tmp_path)
    assert state.scheduled[0][2] is True
    assert [m.name for m in state.scheduled[0][0].modules.modules] == [
        "banner",
        "renamer",
        "todo_formatter",
        "today",
        "sys",
    ]


def test_main_raises_when_notes_dirs_are_missing(monkeypatch):
    monkeypatch.setattr(
        args_mod,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_debug": False,
                "sys_logging_format": "%(message)s",
                "sys_notes_dirs": None,
                "sys_on_open_cooldown": 20,
                "sys_enable_experimental_modules": False,
            },
            [],
        ),
    )

    with pytest.raises(ValueError):
        runpy.run_path(_main_path(), run_name="__main__")


def test_main_enables_experimental_modules_when_flag_is_true(
    tmp_path: Path, monkeypatch
):
    state = _ObserverState()

    class FakeObserver:
        def schedule(self, handler, path, recursive):
            state.scheduled.append((handler, path, recursive))

        def start(self):
            state.started = True

        def stop(self):
            state.stopped = True

        def join(self):
            state.joined = True

    class FakeFileHandler:
        def __init__(self, modules, open_cooldown_seconds):
            self.modules = modules
            self.open_cooldown_seconds = open_cooldown_seconds

    class FakeModuleManager:
        def __init__(self, modules, args):
            self.modules = modules
            self.args = args

    monkeypatch.setattr(observers_mod, "Observer", FakeObserver)
    monkeypatch.setattr(file_handler_mod, "FileHandler", FakeFileHandler)
    monkeypatch.setattr(module_manager_mod, "ModuleManager", FakeModuleManager)
    monkeypatch.setattr(
        args_mod,
        "setup_config_and_cli_args",
        lambda template: (
            {
                "sys_debug": False,
                "sys_logging_format": "%(message)s",
                "sys_notes_dirs": [str(tmp_path)],
                "sys_on_open_cooldown": 20,
                "sys_enable_experimental_modules": True,
            },
            [],
        ),
    )
    monkeypatch.setattr(time, "sleep", lambda _sec: (_ for _ in ()).throw(KeyboardInterrupt()))

    runpy.run_path(_main_path(), run_name="__main__")

    assert [m.name for m in state.scheduled[0][0].modules.modules] == [
        "banner",
        "renamer",
        "todo_formatter",
        "today",
        "sys",
        "git",
        "plasma_sync",
    ]
