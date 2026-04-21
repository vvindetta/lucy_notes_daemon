from __future__ import annotations

from pathlib import Path
from typing import cast

from watchdog.events import FileModifiedEvent, FileMovedEvent, FileOpenedEvent, FileSystemEvent

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.module_manager import ModuleManager


class _DummyModules:
    def __init__(self, ignore_map: dict[str, int] | None = None) -> None:
        self.calls = 0
        self.paths: list[str] = []
        self._ignore_map = ignore_map

    def run(self, path: str, event: FileSystemEvent) -> dict[str, int] | None:
        _ = event
        self.calls += 1
        self.paths.append(path)
        return self._ignore_map


class _SequenceModules:
    def __init__(self, ignore_maps: list[dict[str, int] | None]) -> None:
        self.calls = 0
        self.paths: list[str] = []
        self._ignore_maps = list(ignore_maps)

    def run(self, path: str, event: FileSystemEvent) -> dict[str, int] | None:
        _ = event
        self.calls += 1
        self.paths.append(path)
        if self._ignore_maps:
            return self._ignore_maps.pop(0)
        return None


def _modified_event(src: str) -> FileModifiedEvent:
    return FileModifiedEvent(src)


def _opened_event(src: str) -> FileOpenedEvent:
    return FileOpenedEvent(src)


def _moved_event(src: str, dest: str) -> FileMovedEvent:
    return FileMovedEvent(src, dest)


def _mk_handler(modules: object, cooldown: int = 20) -> FileHandler:
    return FileHandler(
        modules=cast(ModuleManager, modules),
        open_cooldown_seconds=cooldown,
    )


def test_process_file_marks_and_consumes_ignore_map(tmp_path: Path) -> None:
    file_path = tmp_path / "a.md"
    file_path.write_text("x\n", encoding="utf-8")

    modules = _DummyModules(ignore_map={str(file_path): 1})
    handler = _mk_handler(modules)
    ev = _modified_event(str(file_path))

    handler.on_modified(ev)
    handler.on_modified(ev)  # ignored once
    handler.on_modified(ev)  # processed again

    assert modules.calls == 2


def test_process_file_skips_hidden_and_git_paths(tmp_path: Path) -> None:
    hidden = tmp_path / ".hidden"
    hidden.write_text("x\n", encoding="utf-8")

    git_file = tmp_path / ".git" / "config"
    git_file.parent.mkdir(parents=True)
    git_file.write_text("x\n", encoding="utf-8")

    modules = _DummyModules(ignore_map=None)
    handler = _mk_handler(modules)

    handler.on_modified(_modified_event(str(hidden)))
    handler.on_modified(_modified_event(str(git_file)))

    assert modules.calls == 0


def test_opened_event_respects_cooldown(tmp_path: Path, monkeypatch) -> None:
    file_path = tmp_path / "b.md"
    file_path.write_text("x\n", encoding="utf-8")

    times = iter([0.0, 1.0, 11.0])
    monkeypatch.setattr(
        "lucy_notes_manager.file_handler.time.monotonic",
        lambda: next(times),
    )

    modules = _DummyModules(ignore_map=None)
    handler = _mk_handler(modules, cooldown=10)
    ev = _opened_event(str(file_path))

    handler.on_opened(ev)
    handler.on_opened(ev)
    handler.on_opened(ev)

    assert modules.calls == 2


def test_moved_event_uses_destination_path(tmp_path: Path) -> None:
    src = tmp_path / "old.md"
    dst = tmp_path / "new.md"
    src.write_text("x\n", encoding="utf-8")

    modules = _DummyModules(ignore_map=None)
    handler = _mk_handler(modules)

    handler.on_moved(_moved_event(str(src), str(dst)))

    assert modules.calls == 1
    assert modules.paths[0] == str(dst.resolve())


def test_modified_event_ignores_exact_number_of_future_events(tmp_path: Path) -> None:
    file_path = tmp_path / "counter.md"
    file_path.write_text("x\n", encoding="utf-8")

    modules = _SequenceModules(ignore_maps=[{str(file_path): 2}, None])
    handler = _mk_handler(modules)
    ev = _modified_event(str(file_path))

    handler.on_modified(ev)  # processed, sets ignore=2
    handler.on_modified(ev)  # ignored (remaining=1)
    handler.on_modified(ev)  # ignored (remaining=0)
    handler.on_modified(ev)  # processed again

    assert modules.calls == 2


def test_moved_event_is_ignored_when_src_or_dest_is_marked(tmp_path: Path) -> None:
    src = tmp_path / "old.md"
    dst = tmp_path / "new.md"
    src.write_text("x\n", encoding="utf-8")

    src_modules = _SequenceModules(ignore_maps=[{str(src): 1}, None])
    src_handler = _mk_handler(src_modules)
    src_handler.on_modified(_modified_event(str(src)))  # marks src to ignore
    src_handler.on_moved(_moved_event(str(src), str(dst)))
    assert src_modules.calls == 1

    dst_modules = _SequenceModules(ignore_maps=[{str(dst): 1}, None])
    dst_handler = _mk_handler(dst_modules)
    dst_handler.on_modified(_modified_event(str(src)))  # marks dst to ignore
    dst_handler.on_moved(_moved_event(str(src), str(dst)))
    assert dst_modules.calls == 1


def test_ignore_path_is_normalized_before_matching_event_path(tmp_path: Path) -> None:
    file_path = tmp_path / "norm.md"
    file_path.write_text("x\n", encoding="utf-8")
    odd_form = str(tmp_path / "." / "sub" / ".." / "norm.md")

    modules = _SequenceModules(ignore_maps=[{odd_form: 1}, None])
    handler = _mk_handler(modules)

    handler.on_modified(_modified_event(str(file_path)))
    handler.on_modified(_modified_event(str(file_path.resolve())))

    assert modules.calls == 1
