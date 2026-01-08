import logging
import time
from typing import List

from watchdog.observers import Observer

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.lib.args import Template, setup_config_and_cli_args
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.modules.abstract_module import AbstractModule
from lucy_notes_manager.modules.banner import Banner
from lucy_notes_manager.modules.cmd import Cmd
from lucy_notes_manager.modules.plasma_sync import PlasmaSync
from lucy_notes_manager.modules.renamer import Renamer
from lucy_notes_manager.modules.sys_info import SysInfo
from lucy_notes_manager.modules.todo_formatter import TodoFormatter

TEMPLATE_STARTUP_ARGS: Template = [
    (
        "--sys-config-path",
        str,
        "config.txt",
        "Path to the config file. Default: config.txt",
    ),
    (
        "--sys-debug",
        bool,
        False,
        "Enable debug logging (DEBUG level). Default: false",
    ),
    (
        "--sys-logging-format",
        str,
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
        "Python logging format string. Default includes time, level, file, line, message.",
    ),
    (
        "--sys-notes-dirs",
        str,
        None,
        "One or more directories to watch recursively. Example: --sys-notes_dirs ~/notes ~/work/notes",
    ),
    (
        "--sys-on-open-cooldown",
        int,
        20,
        "Cooldown for 'on_opened' events per file, in seconds. Prevents editor spam. Default: 30 seconds).",
    ),
]

MODULES: List[AbstractModule] = [
    Banner(),
    Renamer(),
    TodoFormatter(),
    SysInfo(),
    PlasmaSync(),
    Cmd(),
]

config, unknown_args = setup_config_and_cli_args(template=TEMPLATE_STARTUP_ARGS)

modules = ModuleManager(modules=MODULES, args=unknown_args)

log_level = logging.DEBUG if bool(config["sys_debug"]) else logging.INFO
log_format = config["sys_logging_format"]
logging.basicConfig(
    level=log_level,
    format=log_format,
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

if not config.get("sys_notes_dirs"):
    raise ValueError("No --sys-notes-dirs was setuped")

observer = Observer()

for path in config["sys_notes_dirs"]:
    observer.schedule(
        FileHandler(
            modules=modules,
            open_cooldown_seconds=config["sys_on_open_cooldown"],
        ),
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
