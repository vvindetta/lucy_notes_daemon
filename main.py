import logging
import time

from watchdog.observers import Observer

from lucy_notes_manager.file_handler import FileHandler
from lucy_notes_manager.lib.args import setup_args
from lucy_notes_manager.module_manager import ModuleManager
from lucy_notes_manager.modules.banner_inserter import BannerInserter
from lucy_notes_manager.modules.git import Git
from lucy_notes_manager.modules.plasma_sync import PlasmaSync
from lucy_notes_manager.modules.renamer import Renamer
from lucy_notes_manager.modules.todo_formatter import TodoFormatter

TEMPLATE_STARTUP_ARGS = [
    ("--config_path", str),
    ("--debug", bool),
    ("--logging_format", str),
    ("--notes_dirs", str),
]
DEFAULT_CONFIG_PATH = "config.txt"
DEFAULT_LOGGING_FORMAT = (
    "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s"
)


MODULES = [
    (BannerInserter()),
    (Renamer()),
    (PlasmaSync()),
    (TodoFormatter()),
    # (Git()),
]


config, unknown_args = setup_args(
    template=TEMPLATE_STARTUP_ARGS,
    default_config_path=DEFAULT_CONFIG_PATH,
)

modules = ModuleManager(modules=MODULES, args=unknown_args)


# --- logging ---

log_level = logging.DEBUG if bool(config.get("debug")) else logging.INFO
log_format = config.get("logging_format") or DEFAULT_LOGGING_FORMAT
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
