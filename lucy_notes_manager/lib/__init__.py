import subprocess
import time
from typing import Dict

_NOTIFY_LAST: Dict[str, float] = {}
_NOTIFY_MIN_INTERVAL_SEC = 10.0


def safe_notify(name: str, message: str) -> None:
    """
    Throttle notifications by `key`.

    - If called again within _NOTIFY_MIN_INTERVAL_SEC, it does nothing.
    - Otherwise calls lucy_notes_manager.lib.notify(message=...).
    """
    now = time.time()
    last = _NOTIFY_LAST.get(name, 0.0)
    if now - last < _NOTIFY_MIN_INTERVAL_SEC:
        return
    _NOTIFY_LAST[name] = now
    notify(message=message)


def notify(message: str, title: str = "Lucy Note Manager"):
    """
    Send desktop notification via notify-send.
    Fails silently if notify-send is unavailable.
    """
    try:
        subprocess.run(
            ["notify-send", title, message],
            check=False,
        )
    except Exception:
        pass
