import os
import subprocess
import sys
import time
from typing import Dict, Iterable, Tuple

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


def slow_print_by_lines(
    path: str,
    lines: Iterable[Tuple[int, str]],
    delay: float = 0.2,
) -> Dict[str, int]:
    """
    Print (lineno, text) with delay.
    Returns {abs_path: N} where N is how many lines were printed.
    """
    abs_path = os.path.abspath(path)
    writes = 0

    for lineno, text in lines:
        line = text if text.endswith("\n") else text + "\n"
        sys.stdout.write(f"{lineno}: {line}")
        sys.stdout.flush()
        writes += 1
        time.sleep(delay)

    return {abs_path: writes}


def slow_print(
    path: str,
    start_line: int,
    text: str,
    delay: float = 0.2,
) -> Dict[str, int]:
    """
    Print multi-line text starting from start_line with delay.
    Returns {abs_path: N} where N is how many lines were printed.
    """
    abs_path = os.path.abspath(path)
    writes = 0
    lineno = start_line

    for line in text.splitlines(True):  # keep '\n'
        if not line.endswith("\n"):
            line += "\n"
        sys.stdout.write(f"{lineno}: {line}")
        sys.stdout.flush()
        writes += 1
        time.sleep(delay)
        lineno += 1

    return {abs_path: writes}
