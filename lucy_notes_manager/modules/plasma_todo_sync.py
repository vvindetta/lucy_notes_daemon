import hashlib
import html
import logging
import os
import time
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple, cast

from watchdog.events import FileSystemEvent

from lucy_notes_manager.lib import notify
from lucy_notes_manager.lib.args import parse_args
from lucy_notes_manager.modules.abstract_module import AbstractModule

logger = logging.getLogger(__name__)

# ---------------- Defaults (can be overridden by args) ---------------- #

DEFAULT_PLASMA_NOTES_DIR = "/home/user/.local/share/plasma_notes"
DEFAULT_TODO_FILE = "/home/user/notes/todo.md"
DEFAULT_PLASMA_NOTE_ID = "bfe86b19-c35c-489b-bed7-3d561471f8"


# ---------------- Simple anti-spam notify ---------------- #

_NOTIFY_LAST: Dict[str, float] = {}
_NOTIFY_MIN_INTERVAL_SEC = 10.0


def _notify_throttled(key: str, message: str) -> None:
    now = time.time()
    last = _NOTIFY_LAST.get(key, 0.0)
    if now - last < _NOTIFY_MIN_INTERVAL_SEC:
        return
    _NOTIFY_LAST[key] = now
    notify(message=message)


# ---------------- Local ignore + state ---------------- #

_IGNORE_NEXT_EVENT: Dict[str, int] = {}

_CURRENT_TEXT: Optional[str] = None
_CURRENT_HASH: Optional[str] = None


def _mark_ignore(path: str) -> None:
    path = os.path.abspath(path)
    _IGNORE_NEXT_EVENT[path] = _IGNORE_NEXT_EVENT.get(path, 0) + 1


def _should_ignore(path: str) -> bool:
    path = os.path.abspath(path)
    count = _IGNORE_NEXT_EVENT.get(path, 0)
    if not count:
        return False
    if count == 1:
        _IGNORE_NEXT_EVENT.pop(path, None)
    else:
        _IGNORE_NEXT_EVENT[path] = count - 1
    return True


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # Often normal (first run). Don’t spam notifications.
        logger.debug("File not found: %s", path)
        return ""
    except PermissionError as e:
        logger.error("Permission error reading %s: %s", path, e)
        _notify_throttled(
            "read_perm:" + path, f"Permission denied reading:\n{path}\n\n{e}"
        )
        return ""
    except OSError as e:
        logger.error("OS error reading %s: %s", path, e)
        _notify_throttled("read_os:" + path, f"Failed to read file:\n{path}\n\n{e}")
        return ""


def _write_if_changed(path: str, content: str) -> bool:
    """
    Write to path only if content changed.
    Returns True if the file was actually written, False otherwise.
    """
    path = os.path.abspath(path)
    old = _read_file(path)
    if old == content:
        return False

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _mark_ignore(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except PermissionError as e:
        logger.error("Permission error writing %s: %s", path, e)
        _notify_throttled(
            "write_perm:" + path, f"Permission denied writing:\n{path}\n\n{e}"
        )
        return False
    except OSError as e:
        logger.error("OS error writing %s: %s", path, e)
        _notify_throttled("write_os:" + path, f"Failed to write file:\n{path}\n\n{e}")
        return False


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    n = len(lines)

    i = 0
    while i < n and lines[i].strip() == "":
        i += 1

    result: List[str] = []
    last_nonblank: Optional[str] = None

    while i < n:
        line = lines[i]

        if line.strip() != "":
            result.append(line)
            last_nonblank = line
            i += 1
            continue

        j = i
        while j < n and lines[j].strip() == "":
            j += 1

        if j == n:
            break

        next_line = lines[j]
        prev = (last_nonblank or "").rstrip()
        next_stripped = next_line.lstrip()

        keep_blank_count = 1

        if prev.endswith(":") and next_stripped.startswith("- "):
            keep_blank_count = 0
        elif prev.lstrip().startswith("- ") and next_stripped.startswith("- "):
            keep_blank_count = 0

        for _ in range(keep_blank_count):
            result.append("")

        i = j

    while result and result[-1].strip() == "":
        result.pop()

    return "\n".join(result)


class _PlasmaHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._current: Optional[str] = None
        self._lines: List[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "body":
            self._in_body = True
            return

        if not self._in_body:
            return

        if tag in ("p", "li"):
            if self._current is not None:
                self._lines.append(self._current)
            self._current = ""
        elif tag == "br":
            if self._current is None:
                self._current = ""
            self._current += "\n"

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "body":
            if self._current is not None:
                self._lines.append(self._current)
                self._current = None
            self._in_body = False
            return

        if not self._in_body:
            return

        if tag in ("p", "li"):
            if self._current is None:
                self._current = ""
            self._lines.append(self._current)
            self._current = None

    def handle_data(self, data):
        if not self._in_body:
            return
        if self._current is None:
            self._current = ""
        self._current += data

    def get_text(self) -> str:
        if self._current is not None:
            self._lines.append(self._current)
            self._current = None
        return "\n".join(self._lines)


def _html_to_text(html_src: str) -> str:
    parser = _PlasmaHTMLParser()
    parser.feed(html_src)
    raw_text = parser.get_text()
    return _normalize_text(raw_text)


def _text_to_plasma_html(text: str) -> str:
    norm = _normalize_text(text)

    header = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        '<html><head><meta name="qrichtext" content="1" />'
        '<meta charset="utf-8" />'
        '<style type="text/css">\n'
        "p, li { white-space: pre-wrap; }\n"
        "hr { height: 1px; border-width: 0; }\n"
        'li.unchecked::marker { content: "\\2610"; }\n'
        'li.checked::marker { content: "\\2612"; }\n'
        "</style></head>"
        "<body style=\" font-family:'Noto Sans'; font-size:10pt; "
        'font-weight:400; font-style:normal;">\n'
    )

    base_style = (
        " margin-top:0px; margin-bottom:0px; margin-left:0px; "
        "margin-right:0px; -qt-block-indent:0; text-indent:0px;"
    )

    parts: List[str] = []
    for line in norm.splitlines():
        if line != "":
            safe = html.escape(line, quote=False)
            parts.append(f'<p style="{base_style}">{safe}</p>\n')
        else:
            parts.append(
                f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
            )

    footer = "</body></html>\n"
    return header + "".join(parts) + footer


def _update_state_from_text(text: str) -> bool:
    global _CURRENT_TEXT, _CURRENT_HASH

    norm = _normalize_text(text)
    new_hash = _hash_text(norm)

    if _CURRENT_HASH == new_hash:
        return False

    _CURRENT_TEXT = norm
    _CURRENT_HASH = new_hash
    return True


# ---------------- Module ---------------- #


class PlasmaNotesSync(AbstractModule):
    name: str = "plasma_notes_sync"
    priority: int = 30

    # args like in git module
    template = (
        ("--plasma-notes-dir", str),
        ("--plasma-note-id", str),
        ("--todo-file", str),
    )

    def _cfg(self, args: List[str]) -> Tuple[str, str, str]:
        known_raw, _ = parse_args(self.template, args)
        known = cast(Dict[str, List[object]], known_raw)

        plasma_dir = DEFAULT_PLASMA_NOTES_DIR
        todo_file = DEFAULT_TODO_FILE
        note_id = DEFAULT_PLASMA_NOTE_ID

        v = known.get("plasma_notes_dir")
        if v and isinstance(v[0], str) and v[0]:
            plasma_dir = v[0]

        v = known.get("todo_file")
        if v and isinstance(v[0], str) and v[0]:
            todo_file = v[0]

        v = known.get("plasma_note_id")
        if v and isinstance(v[0], str) and v[0]:
            note_id = v[0]

        return (
            os.path.abspath(os.path.expanduser(plasma_dir)),
            os.path.abspath(os.path.expanduser(todo_file)),
            note_id,
        )

    def created(self, args: List[str], event: FileSystemEvent) -> bool:
        return self._handle_event(args, event)

    def modified(self, args: List[str], event: FileSystemEvent) -> bool:
        return self._handle_event(args, event)

    def moved(self, args: List[str], event: FileSystemEvent) -> bool:
        path = getattr(event, "dest_path", None) or getattr(event, "src_path", None)
        return self._handle_event(args, event, override_path=path)

    def deleted(self, args: List[str], event: FileSystemEvent) -> bool:
        # Safe choice: do nothing on delete to avoid wiping TODO on missing source.
        return False

    def _find_primary_note(self, plasma_dir: str, note_id: str) -> Optional[str]:
        try:
            entries = sorted(os.listdir(plasma_dir))
        except FileNotFoundError:
            return None
        except PermissionError as e:
            logger.error("Permission error listing %s: %s", plasma_dir, e)
            _notify_throttled(
                "ls_perm:" + plasma_dir, f"Permission denied:\n{plasma_dir}\n\n{e}"
            )
            return None
        except OSError as e:
            logger.error("OS error listing %s: %s", plasma_dir, e)
            _notify_throttled(
                "ls_os:" + plasma_dir, f"Failed to read directory:\n{plasma_dir}\n\n{e}"
            )
            return None

        if note_id in entries:
            return os.path.join(plasma_dir, note_id)

        for name in entries:
            if name.startswith("."):
                continue
            return os.path.join(plasma_dir, name)

        return None

    def _ensure_primary_note(self, plasma_dir: str, note_id: str) -> str:
        path = self._find_primary_note(plasma_dir, note_id)
        if path is not None:
            return path

        try:
            os.makedirs(plasma_dir, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create plasma dir %s: %s", plasma_dir, e)
            _notify_throttled(
                "mk_plasma:" + plasma_dir,
                f"Failed to create directory:\n{plasma_dir}\n\n{e}",
            )

        return os.path.join(plasma_dir, note_id)

    def _handle_event(
        self,
        args: List[str],
        event: FileSystemEvent,
        override_path: Optional[str] = None,
    ) -> bool:
        plasma_dir, todo_file, note_id = self._cfg(args)

        path = override_path or getattr(event, "src_path", None)
        if not path:
            return False

        path = os.path.abspath(path)

        if _should_ignore(path):
            return False

        # Decide direction
        todo_abs = os.path.abspath(todo_file)

        if path == todo_abs:
            return self._from_todo(plasma_dir, todo_file, note_id)

        # Check if it's inside plasma dir
        try:
            in_plasma = os.path.commonpath([path, plasma_dir]) == plasma_dir
        except ValueError:
            in_plasma = False

        if in_plasma:
            return self._from_plasma(plasma_dir, todo_file, note_id, html_path=path)

        return False

    def _from_todo(self, plasma_dir: str, todo_file: str, note_id: str) -> bool:
        text_raw = _read_file(todo_file)
        if text_raw == "" and not os.path.exists(todo_file):
            # Only notify if TODO is truly missing (not just empty file)
            _notify_throttled(
                "todo_missing:" + todo_file, f"TODO file not found:\n{todo_file}"
            )
            return False

        if not _update_state_from_text(text_raw):
            return False

        if _CURRENT_TEXT is None:
            return False

        html_path = self._ensure_primary_note(plasma_dir, note_id)
        html_new = _text_to_plasma_html(_CURRENT_TEXT)

        changed = _write_if_changed(html_path, html_new)
        if changed:
            logger.info("Sync TODO -> Plasma: %s -> %s", todo_file, html_path)
        return changed

    def _from_plasma(
        self,
        plasma_dir: str,
        todo_file: str,
        note_id: str,
        html_path: str,
    ) -> bool:
        if not os.path.exists(html_path):
            # if Plasma note disappeared, ignore
            logger.debug("Plasma html not found (ignored): %s", html_path)
            return False

        html_raw = _read_file(html_path)
        text_from_html = _html_to_text(html_raw)

        if not _update_state_from_text(text_from_html):
            return False

        if _CURRENT_TEXT is None:
            return False

        any_changed = False

        # Update TODO file
        if _write_if_changed(todo_file, _CURRENT_TEXT):
            any_changed = True

        # Normalize and re-write HTML if needed
        html_new = _text_to_plasma_html(_CURRENT_TEXT)
        if _write_if_changed(html_path, html_new):
            any_changed = True

        if any_changed:
            logger.info("Sync Plasma -> TODO: %s -> %s", html_path, todo_file)

        return any_changed
