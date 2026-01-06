import logging
import time
from typing import List

from watchdog.observers import Observer

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.lib.args import Template, setup_config_and_cli_args
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.modules.abstract_module import AbstractModule
from lucy_notes_manager.modules.banner import Banner

# from lucy_notes_manager.modules.git import Git
# from lucy_notes_manager.modules.plasma_sync import PlasmaSync
from lucy_notes_manager.modules.renamer import Renamer
from lucy_notes_manager.modules.sys_info import SysInfo
from lucy_notes_manager.modules.todo_formatter import TodoFormatter

TEMPLATE_STARTUP_ARGS: Template = [
    (
        "--config_path",
        str,
        "config.txt",
    ),
    ("--debug", bool, False),
    (
        "--logging_format",
        str,
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
    ),
    ("--notes_dirs", str, None),
]


MODULES: List[AbstractModule] = [
    Banner(),
    Renamer(),
    TodoFormatter(),
    SysInfo(),
    # (PlasmaSync()),
    # (Git()),
]


config, unknown_args = setup_config_and_cli_args(
    template=TEMPLATE_STARTUP_ARGS,
)

modules = ModuleManager(modules=MODULES, args=unknown_args)


# --- logging ---

log_level = logging.DEBUG if bool(config["debug"]) else logging.INFO
log_format = config["logging_format"]
logging.basicConfig(
    level=log_level,
    format=log_format,
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

# --- running ---

if not config.get("notes_dirs"):
    raise ValueError("No --notes_dirs was setuped")

# print(system_args)
# print(unknown_args)

observer = Observer()

for path in config["notes_dirs"]:
    observer.schedule(
        FileHandler(modules=modules),
        path=path,
        recursive=True,
    )

observer.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    observer.stop()
observer.join()
