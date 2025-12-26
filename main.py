import logging
import time

from watchdog.observers import Observer

from lucy_notes_manager.change_handler import ChangeHandler
from lucy_notes_manager.lib.args import setup_args
from lucy_notes_manager.modules.banner_inserter import BannerInserter
from lucy_notes_manager.modules.git import Git
from lucy_notes_manager.modules.plasma_todo_sync import PlasmaNotesSync
from lucy_notes_manager.modules.renamer import Renamer
from lucy_notes_manager.modules.todo_formatter import TodoFormatter

TEMPLATE_STARTUP_ARGS = (
    ("--config_path", str),
    ("--debug", bool),
    ("--logging_format", str),
    ("--notes_dirs", str),
)
DEFAULT_CONFIG_PATH = "config.txt"
DEFAULT_LOGGING_FORMAT = (
    "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s"
)


MODULES = [
    (BannerInserter.priority, BannerInserter()),
    (Renamer.priority, Renamer()),
    (PlasmaNotesSync.priority, PlasmaNotesSync()),
    (TodoFormatter.priority, TodoFormatter()),
    (Git.priority, Git()),
]


system_args, unknown_args = setup_args(
    template=TEMPLATE_STARTUP_ARGS,
    default_config_path=DEFAULT_CONFIG_PATH,
)

# --- logging ---

log_level = logging.DEBUG if bool(system_args.get("debug")) else logging.INFO
log_format = system_args.get("logging_format") or DEFAULT_LOGGING_FORMAT
logging.basicConfig(
    level=log_level,
    format=log_format,
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

# --- running ---

if not system_args.get("notes_dirs"):
    raise ValueError("No --notes_dirs was setuped")

# print(system_args)
# print(unknown_args)

observer = Observer()

for path in system_args["notes_dirs"]:
    observer.schedule(
        ChangeHandler(modules=MODULES, args=unknown_args),
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
