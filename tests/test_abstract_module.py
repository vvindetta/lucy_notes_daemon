from __future__ import annotations

from watchdog.events import FileModifiedEvent

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System


class DemoModule(AbstractModule):
    name: str = "demo"


def test_default_module_hooks_are_noops():
    module = DemoModule()
    ctx = Context(path="/tmp/x", config={}, arg_lines={})
    system = System(event=FileModifiedEvent("/tmp/x"), global_template=[], modules=[module])

    assert module.created(ctx, system) is None
    assert module.modified(ctx, system) is None
    assert module.moved(ctx, system) is None
    assert module.deleted(ctx, system) is None
    assert module.opened(ctx, system) is None


def test_context_and_system_dataclasses_keep_values():
    module = DemoModule()
    event = FileModifiedEvent("/tmp/file")
    template: Template = [("--x", str, None, "")]
    ctx = Context(path="/tmp/file", config={"x": ["1"]}, arg_lines={"x": [1]})
    system = System(event=event, global_template=template, modules=[module])

    assert ctx.path == "/tmp/file"
    assert ctx.config["x"] == ["1"]
    assert system.event is event
    assert system.global_template == template
    assert system.modules == [module]
