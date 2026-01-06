import os
import subprocess
import time
from typing import Dict, List

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


def notify(message: str, title: str = "Lucy Note Manager") -> None:
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


def slow_write_lines_from(
    path: str,
    lines: List[str],
    from_line: int,
    delay: float = 0.2,
) -> Dict[str, int]:
    abs_path = os.path.abspath(path)
    from_idx = max(0, int(from_line) - 1)

    slow_writes = 0

    with open(abs_path, "w", encoding="utf-8") as f:
        # fast part
        if from_idx > 0:
            f.writelines(lines[:from_idx])

        # slow part
        for line in lines[from_idx:]:
            f.write(line)
            f.flush()
            slow_writes += 1
            time.sleep(delay)

    # if slow part was empty, file still changed -> ignore once
    return {abs_path: slow_writes or 1}
