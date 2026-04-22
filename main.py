import json
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
from lucy_notes_manager.modules.git import Git
from lucy_notes_manager.modules.plasma_sync import PlasmaSync
from lucy_notes_manager.modules.renamer import Renamer
from lucy_notes_manager.modules.sys import Sys
from lucy_notes_manager.modules.today import Today
from lucy_notes_manager.modules.todo_formatter import TodoFormatter
from lucy_notes_manager.notifications import (
    NotificationConfig,
    NotificationEvent,
    build_providers,
)

TEMPLATE_STARTUP_ARGS: Template = [
    (
        "--sys-config-path",
        str,
        ["config.txt"],
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
    (
        "--sys-notifications-config",
        str,
        None,
        "Path to a JSON file listing repository-update notification sources "
        "(webhook/RSS). If not set, no notification providers are started.",
    ),
    (
        "--sys-webhook-host",
        str,
        "127.0.0.1",
        "Host to bind the webhook HTTP server to. Default: 127.0.0.1.",
    ),
    (
        "--sys-webhook-port",
        int,
        8765,
        "Port for the webhook HTTP server. Default: 8765.",
    ),
    (
        "--sys-webhook-path",
        str,
        "/webhook",
        "URL path for the webhook HTTP server. Default: /webhook.",
    ),
]

MODULES: List[AbstractModule] = [
    Banner(),
    Renamer(),
    TodoFormatter(),
    Today(),
    Sys(),
    # Git(),
    # PlasmaSync(),
    # Cmd(),
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
if "/path/to/note/dir" in config["sys_notes_dirs"]:
    raise Exception(
        "--sys-notes-dirs: '/path/to/note/dir' is not a valid path. Please edit your config."
    )


def _load_notification_configs(path_value) -> List[NotificationConfig]:
    if not path_value:
        return []
    if isinstance(path_value, list):
        path_value = path_value[0] if path_value else None
    if not path_value:
        return []
    with open(path_value, "r", encoding="utf-8") as file_handle:
        data = json.load(file_handle)
    if not isinstance(data, list):
        raise ValueError(
            f"{path_value}: expected a JSON list of notification config objects"
        )
    configs: List[NotificationConfig] = []
    for item in data:
        configs.append(
            NotificationConfig(
                repo_root=item["repo_root"],
                platform=item["platform"],
                transport=item["transport"],
                feed_url=item.get("feed_url"),
                poll_interval_sec=float(item.get("poll_interval_sec", 300.0)),
                secret=item.get("secret"),
                branch=item.get("branch"),
                extra=dict(item.get("extra", {})),
            )
        )
    return configs


def _find_git_module():
    for module in modules.modules:
        if isinstance(module, Git):
            return module
    return None


notification_configs = _load_notification_configs(config.get("sys_notifications_config"))

notification_providers = []
if notification_configs:
    git_module = _find_git_module()
    if git_module is None:
        logging.warning(
            "notification configs provided but Git module is not enabled; "
            "providers will not be started"
        )
    else:
        def _on_push(event: NotificationEvent) -> None:
            logging.info(
                "notification: %s via %s (ref=%s) -> pull %s",
                event.platform,
                event.source,
                event.ref,
                event.repo_root,
            )
            git_module.trigger_pull(event.repo_root, dict(config))

        webhook_host = config.get("sys_webhook_host") or "127.0.0.1"
        webhook_port = int(config.get("sys_webhook_port") or 8765)
        webhook_path = config.get("sys_webhook_path") or "/webhook"

        notification_providers = build_providers(
            notification_configs,
            _on_push,
            webhook_host=webhook_host[0] if isinstance(webhook_host, list) else webhook_host,
            webhook_port=webhook_port,
            webhook_path=webhook_path[0] if isinstance(webhook_path, list) else webhook_path,
        )
        for provider in notification_providers:
            provider.start()


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
    for provider in notification_providers:
        provider.stop()

observer.join()
for provider in notification_providers:
    provider.join(timeout=5.0)
