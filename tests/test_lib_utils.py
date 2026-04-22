from __future__ import annotations

from pathlib import Path

import lucy_notes_manager.lib as lib_mod


def test_safe_notify_throttles_by_name(monkeypatch):
    calls: list[str] = []
    times = iter([0.0, 1.0, 15.0])

    monkeypatch.setattr(
        lib_mod, "notify", lambda message, title="Lucy Note Manager": calls.append(message)
    )
    monkeypatch.setattr(lib_mod.time, "time", lambda: next(times))
    lib_mod._NOTIFY_LAST.clear()

    lib_mod.safe_notify("k1", "first")
    lib_mod.safe_notify("k1", "second")
    lib_mod.safe_notify("k1", "third")

    assert calls == ["first", "third"]


def test_notify_uses_global_notifypy_object(monkeypatch):
    class DummyNotify:
        def __init__(self):
            self.title = ""
            self.message = ""
            self.sent = False

        def send(self):
            self.sent = True

    dummy = DummyNotify()
    monkeypatch.setattr(lib_mod, "notifypy", dummy)

    lib_mod.notify("hello", title="T")

    assert dummy.title == "T"
    assert dummy.message == "hello"
    assert dummy.sent is True


def test_slow_write_lines_from_writes_and_counts(tmp_path: Path, monkeypatch):
    path = tmp_path / "note.txt"
    monkeypatch.setattr(lib_mod.time, "sleep", lambda _d: None)

    result = lib_mod.slow_write_lines_from(
        str(path),
        lines=["a\n", "b\n", "c\n"],
        from_line=2,
        delay=0.01,
    )

    assert path.read_text(encoding="utf-8") == "a\nb\nc\n"
    assert result == {str(path.resolve()): 2}
