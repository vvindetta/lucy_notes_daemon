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

# Optional bold-only mirror note (your "third widget", but bold-only)
DEFAULT_PLASMA_BOLD_NOTE_ID: Optional[str] = None


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

# Bold-specific state (so MAIN->BOLD updates happen even if plain text didn't change)
_MAIN_BOLD_HASH: Optional[str] = None
_BOLD_NOTE_ITEMS_HASH: Optional[str] = None


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


# ---------------- Plain text <-> Plasma HTML (UNCHANGED) ---------------- #


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


# ---------------- Bold-only overlay (ADDED, optional) ---------------- #


def _style_is_bold(style: str) -> bool:
    s = style.lower().replace(" ", "")
    if "font-weight:bold" in s:
        return True
    if "font-weight:" in s:
        try:
            idx = s.rfind("font-weight:")
            val = s[idx + len("font-weight:") :]
            val = val.split(";")[0]
            num = int(val)
            return num >= 600
        except Exception:
            return False
    return False


class _PlasmaBoldAwareParser(HTMLParser):
    """
    Extract text while tracking bold spans.
    Bold is considered if:
    - <b>, <strong>
    - <span style="...font-weight:600/700/bold...">
    - <p>/<li>/<font> style has font-weight (Qt often puts bold on block tags)
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._bold_depth = 0

        self._span_bold_stack: List[bool] = []

        # NEW: bold may be applied to block tags (<p>, <li>) or <font>
        self._tag_style_bold: Dict[str, List[bool]] = {"p": [], "li": [], "font": []}

        self._lines: List[List[Tuple[str, bool]]] = [[]]

    def _newline(self) -> None:
        self._lines.append([])

    def _append(self, text: str) -> None:
        if not text:
            return
        b = self._bold_depth > 0
        self._lines[-1].append((text, b))

    def _push_tag_style_bold(self, tag: str, attrs) -> None:
        style = ""
        for k, v in attrs:
            if k.lower() == "style" and isinstance(v, str):
                style = v
                break
        is_bold = _style_is_bold(style)
        self._tag_style_bold[tag].append(is_bold)
        if is_bold:
            self._bold_depth += 1

    def _pop_tag_style_bold(self, tag: str) -> None:
        st = self._tag_style_bold.get(tag)
        if not st:
            return
        was_bold = st.pop() if st else False
        if was_bold:
            self._bold_depth = max(0, self._bold_depth - 1)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "body":
            self._in_body = True
            return
        if not self._in_body:
            return

        if tag in ("b", "strong"):
            self._bold_depth += 1
            return

        if tag == "span":
            style = ""
            for k, v in attrs:
                if k.lower() == "style" and isinstance(v, str):
                    style = v
                    break
            is_bold = _style_is_bold(style)
            self._span_bold_stack.append(is_bold)
            if is_bold:
                self._bold_depth += 1
            return

        # NEW: detect bold set on <p>/<li>/<font> style
        if tag in ("p", "li", "font"):
            self._push_tag_style_bold(tag, attrs)
            return

        if tag == "br":
            self._newline()
            return

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "body":
            self._in_body = False
            return
        if not self._in_body:
            return

        if tag in ("b", "strong"):
            self._bold_depth = max(0, self._bold_depth - 1)
            return

        if tag == "span":
            if self._span_bold_stack:
                was_bold = self._span_bold_stack.pop()
                if was_bold:
                    self._bold_depth = max(0, self._bold_depth - 1)
            return

        # NEW: close tag-style bold
        if tag in ("p", "li", "font"):
            self._pop_tag_style_bold(tag)
            if tag in ("p", "li"):
                self._newline()
            return

        if tag in ("p", "li"):
            self._newline()
            return

    def handle_data(self, data):
        if not self._in_body:
            return
        if not isinstance(data, str):
            return
        self._append(data)

    def get_lines(self) -> List[List[Tuple[str, bool]]]:
        # merge adjacent segments with same bold flag
        merged_lines: List[List[Tuple[str, bool]]] = []
        for line in self._lines:
            merged: List[Tuple[str, bool]] = []
            for t, b in line:
                if not t:
                    continue
                if merged and merged[-1][1] == b:
                    merged[-1] = (merged[-1][0] + t, b)
                else:
                    merged.append((t, b))
            merged_lines.append(merged)

        # trim leading/trailing empty lines
        def line_text(ln: List[Tuple[str, bool]]) -> str:
            return "".join(t for t, _b in ln)

        i = 0
        while i < len(merged_lines) and line_text(merged_lines[i]).strip() == "":
            i += 1
        merged_lines = merged_lines[i:]

        j = len(merged_lines) - 1
        while j >= 0 and line_text(merged_lines[j]).strip() == "":
            j -= 1
        merged_lines = merged_lines[: j + 1]

        # collapse multiple blank lines
        out: List[List[Tuple[str, bool]]] = []
        prev_blank = False
        for ln in merged_lines:
            blank = line_text(ln).strip() == ""
            if blank and prev_blank:
                continue
            out.append(ln)
            prev_blank = blank

        return out


def _extract_bold_items_from_html(html_src: str) -> List[str]:
    p = _PlasmaBoldAwareParser()
    p.feed(html_src)
    lines = p.get_lines()

    items: List[str] = []
    for line in lines:
        buf: List[str] = []
        in_bold = False
        for t, b in line:
            if b:
                if not in_bold:
                    buf = []
                    in_bold = True
                buf.append(t)
            else:
                if in_bold:
                    s = "".join(buf).strip()
                    if s:
                        items.append(s)
                    buf = []
                    in_bold = False
        if in_bold:
            s = "".join(buf).strip()
            if s:
                items.append(s)

    return items


def _items_hash(items: List[str]) -> str:
    norm_items = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm_items = [it for it in norm_items if it]
    return _hash_text("\n".join(norm_items))


def _bold_items_to_plasma_html(items: List[str]) -> str:
    """
    Render items as bold-only paragraphs in Plasma HTML.
    """
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

    norm = []
    for it in items:
        it2 = it.replace("\r\n", "\n").replace("\r", "\n").strip()
        if it2:
            norm.append(it2)

    parts: List[str] = []
    for line in norm:
        safe = html.escape(line, quote=False)
        parts.append(
            f'<p style="{base_style}"><span style=" font-weight:600;">{safe}</span></p>\n'
        )

    footer = "</body></html>\n"
    return header + "".join(parts) + footer


def _plasma_html_to_boldaware_lines(html_src: str) -> List[List[Tuple[str, bool]]]:
    p = _PlasmaBoldAwareParser()
    p.feed(html_src)
    return p.get_lines()


def _replace_bold_items_in_lines(
    main_lines: List[List[Tuple[str, bool]]],
    new_items: List[str],
) -> List[List[Tuple[str, bool]]]:
    """
    Mirror (items) -> MAIN:
    - Replace bold runs in MAIN in order with items
    - If MAIN has more bold runs than items -> those extra runs become non-bold (unbold)
    - If items has more -> append new bold lines at end
    """
    items = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in new_items]
    items = [it for it in items if it]

    out: List[List[Tuple[str, bool]]] = []
    k = 0

    for line in main_lines:
        new_line: List[Tuple[str, bool]] = []
        i = 0
        while i < len(line):
            t, b = line[i]
            if not b:
                new_line.append((t, False))
                i += 1
                continue

            if k < len(items):
                new_line.append((items[k], True))
                k += 1
            else:
                new_line.append((t, False))  # unbold leftover
            i += 1

        out.append(new_line)

    while k < len(items):
        out.append([(items[k], True)])
        k += 1

    return out


def _boldaware_lines_to_plasma_html(lines: List[List[Tuple[str, bool]]]) -> str:
    """
    Flatten to paragraph HTML but preserve bold runs via <span font-weight:600>.
    """
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

    def line_plain_text(ln: List[Tuple[str, bool]]) -> str:
        return "".join(t for t, _b in ln)

    parts: List[str] = []
    for ln in lines:
        if line_plain_text(ln).strip() == "":
            parts.append(
                f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
            )
            continue

        inner: List[str] = []
        for t, b in ln:
            safe = html.escape(t, quote=False)
            if b:
                inner.append(f'<span style=" font-weight:600;">{safe}</span>')
            else:
                inner.append(safe)

        parts.append(f'<p style="{base_style}">{"".join(inner)}</p>\n')

    footer = "</body></html>\n"
    return header + "".join(parts) + footer


# ---------------- Module ---------------- #


class PlasmaSync(AbstractModule):
    name: str = "plasma_notes_sync"
    priority: int = 30

    template = (
        ("--plasma-notes-dir", str),
        ("--plasma-note-id", str),
        ("--todo-file", str),
        # Optional bold mirror note
        ("--plasma-bold-note-id", str),
    )

    def _cfg(self, args: List[str]) -> Tuple[str, str, str, Optional[str]]:
        known_raw, _ = parse_args(self.template, args)
        known = cast(Dict[str, List[object]], known_raw)

        plasma_dir = DEFAULT_PLASMA_NOTES_DIR
        todo_file = DEFAULT_TODO_FILE
        note_id = DEFAULT_PLASMA_NOTE_ID
        bold_note_id: Optional[str] = DEFAULT_PLASMA_BOLD_NOTE_ID

        v = known.get("plasma_notes_dir")
        if v and isinstance(v[0], str) and v[0]:
            plasma_dir = v[0]

        v = known.get("todo_file")
        if v and isinstance(v[0], str) and v[0]:
            todo_file = v[0]

        v = known.get("plasma_note_id")
        if v and isinstance(v[0], str) and v[0]:
            note_id = v[0]

        v = known.get("plasma_bold_note_id")
        if v and isinstance(v[0], str) and v[0].strip():
            bold_note_id = v[0].strip()

        return (
            os.path.abspath(os.path.expanduser(plasma_dir)),
            os.path.abspath(os.path.expanduser(todo_file)),
            note_id,
            bold_note_id,
        )

    def created(self, args: List[str], event: FileSystemEvent) -> bool:
        return self._handle_event(args, event)

    def modified(self, args: List[str], event: FileSystemEvent) -> bool:
        return self._handle_event(args, event)

    def moved(self, args: List[str], event: FileSystemEvent) -> bool:
        path = getattr(event, "dest_path", None) or getattr(event, "src_path", None)
        return self._handle_event(args, event, override_path=path)

    def deleted(self, args: List[str], event: FileSystemEvent) -> bool:
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

    def _bold_note_path(
        self, plasma_dir: str, bold_note_id: Optional[str]
    ) -> Optional[str]:
        if not bold_note_id:
            return None
        try:
            os.makedirs(plasma_dir, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create plasma dir %s: %s", plasma_dir, e)
            _notify_throttled(
                "mk_plasma:" + plasma_dir,
                f"Failed to create directory:\n{plasma_dir}\n\n{e}",
            )
        return os.path.join(plasma_dir, bold_note_id)

    def _handle_event(
        self,
        args: List[str],
        event: FileSystemEvent,
        override_path: Optional[str] = None,
    ) -> bool:
        plasma_dir, todo_file, note_id, bold_note_id = self._cfg(args)

        path = override_path or getattr(event, "src_path", None)
        if not path:
            return False
        path = os.path.abspath(path)

        if _should_ignore(path):
            return False

        todo_abs = os.path.abspath(todo_file)
        main_path = os.path.abspath(self._ensure_primary_note(plasma_dir, note_id))

        bp = self._bold_note_path(plasma_dir, bold_note_id)
        bold_path = os.path.abspath(bp) if bp is not None else None

        if path == todo_abs:
            return self._from_todo(plasma_dir, todo_file, note_id, bold_note_id)

        # Check if it's inside plasma dir
        try:
            in_plasma = os.path.commonpath([path, plasma_dir]) == plasma_dir
        except ValueError:
            in_plasma = False

        if not in_plasma:
            return False

        # Bold mirror edited
        if bold_path and path == bold_path:
            return self._from_bold_mirror(plasma_dir, todo_file, note_id, bold_note_id)

        # Main note edited
        if path == main_path:
            return self._from_main_plasma(
                plasma_dir, todo_file, note_id, bold_note_id, html_path=path
            )

        # Any other plasma note -> keep old behavior exactly
        return self._from_other_plasma(plasma_dir, todo_file, note_id, html_path=path)

    # -------- TODO -> Plasma (same logic), plus optional update of bold mirror -------- #

    def _from_todo(
        self,
        plasma_dir: str,
        todo_file: str,
        note_id: str,
        bold_note_id: Optional[str],
    ) -> bool:
        text_raw = _read_file(todo_file)
        if text_raw == "" and not os.path.exists(todo_file):
            _notify_throttled(
                "todo_missing:" + todo_file, f"TODO file not found:\n{todo_file}"
            )
            return False

        if not _update_state_from_text(text_raw):
            return False

        if _CURRENT_TEXT is None:
            return False

        main_path = self._ensure_primary_note(plasma_dir, note_id)
        html_new = _text_to_plasma_html(_CURRENT_TEXT)

        any_changed = _write_if_changed(main_path, html_new)

        # If bold mirror enabled: TODO overwrites MAIN HTML (plain), so mirror should reflect that (usually empty)
        bold_path = self._bold_note_path(plasma_dir, bold_note_id)
        if bold_path:
            global _MAIN_BOLD_HASH
            _MAIN_BOLD_HASH = _items_hash(
                []
            )  # MAIN now has no bold produced by this generator
            if _write_if_changed(bold_path, _bold_items_to_plasma_html([])):
                any_changed = True

        if any_changed:
            logger.info("Sync TODO -> Plasma: %s -> %s", todo_file, main_path)
        return any_changed

    # -------- MAIN Plasma -> TODO (same text logic), plus MAIN -> BOLD mirror -------- #

    def _from_main_plasma(
        self,
        plasma_dir: str,
        todo_file: str,
        note_id: str,
        bold_note_id: Optional[str],
        html_path: str,
    ) -> bool:
        if not os.path.exists(html_path):
            logger.debug("Plasma html not found (ignored): %s", html_path)
            return False

        html_raw = _read_file(html_path)
        text_from_html = _html_to_text(html_raw)

        text_changed = _update_state_from_text(text_from_html)

        any_changed = False

        # Update TODO only if plain text changed (original behavior)
        if text_changed and _CURRENT_TEXT is not None:
            if _write_if_changed(todo_file, _CURRENT_TEXT):
                any_changed = True

            # IMPORTANT:
            # If bold mirror is NOT enabled -> keep original "normalize and re-write HTML" behavior.
            # If bold mirror IS enabled -> do NOT rewrite MAIN via _text_to_plasma_html(),
            # otherwise it would wipe bold formatting, breaking MAIN<->BOLD sync.
            if not bold_note_id:
                html_new = _text_to_plasma_html(_CURRENT_TEXT)
                if _write_if_changed(html_path, html_new):
                    any_changed = True

        # MAIN -> BOLD mirror update (even if plain text unchanged)
        if bold_note_id:
            bold_items = _extract_bold_items_from_html(html_raw)
            new_bold_hash = _items_hash(bold_items)

            global _MAIN_BOLD_HASH
            if _MAIN_BOLD_HASH != new_bold_hash:
                _MAIN_BOLD_HASH = new_bold_hash
                bold_path = self._bold_note_path(plasma_dir, bold_note_id)
                if bold_path:
                    if _write_if_changed(
                        bold_path, _bold_items_to_plasma_html(bold_items)
                    ):
                        any_changed = True

        if any_changed:
            logger.info("Sync MAIN Plasma -> TODO: %s -> %s", html_path, todo_file)

        return any_changed

    # -------- BOLD mirror -> MAIN (two-way sync), and update TODO accordingly -------- #

    def _from_bold_mirror(
        self,
        plasma_dir: str,
        todo_file: str,
        note_id: str,
        bold_note_id: Optional[str],
    ) -> bool:
        if not bold_note_id:
            return False

        bold_path = self._bold_note_path(plasma_dir, bold_note_id)
        if not bold_path or not os.path.exists(bold_path):
            logger.debug("Bold mirror not found (ignored): %s", bold_path)
            return False

        bold_html_raw = _read_file(bold_path)

        # We treat each non-empty line of the bold note's *plain text* as an item.
        # This allows the user to type normally; we will re-render it as bold-only.
        bold_text = _html_to_text(bold_html_raw)
        items = [ln.strip() for ln in bold_text.splitlines() if ln.strip()]

        global _BOLD_NOTE_ITEMS_HASH, _MAIN_BOLD_HASH
        items_h = _items_hash(items)
        if _BOLD_NOTE_ITEMS_HASH == items_h:
            return False
        _BOLD_NOTE_ITEMS_HASH = items_h

        # Read MAIN and apply replacement into bold runs
        main_path = self._ensure_primary_note(plasma_dir, note_id)
        main_html_raw = _read_file(main_path)

        main_lines = _plasma_html_to_boldaware_lines(main_html_raw)
        new_lines = _replace_bold_items_in_lines(main_lines, items)
        new_main_html = _boldaware_lines_to_plasma_html(new_lines)

        any_changed = False

        # Write MAIN (this is the core of reverse sync)
        if _write_if_changed(main_path, new_main_html):
            any_changed = True

        # Update TODO based on updated MAIN (same text extraction logic)
        new_plain = _html_to_text(new_main_html)
        if _update_state_from_text(new_plain) and _CURRENT_TEXT is not None:
            if _write_if_changed(todo_file, _CURRENT_TEXT):
                any_changed = True

        # Normalize/rewrite the bold note itself to enforce bold-only display
        if _write_if_changed(bold_path, _bold_items_to_plasma_html(items)):
            any_changed = True

        # Keep MAIN bold hash in sync with what we just enforced
        _MAIN_BOLD_HASH = _items_hash(items)

        if any_changed:
            logger.info(
                "Sync BOLD mirror -> MAIN (+TODO): %s -> %s -> %s",
                bold_path,
                main_path,
                todo_file,
            )

        return any_changed

    # -------- Original behavior for any other Plasma note -------- #

    def _from_other_plasma(
        self,
        plasma_dir: str,
        todo_file: str,
        note_id: str,
        html_path: str,
    ) -> bool:
        if not os.path.exists(html_path):
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

        # Normalize and re-write THIS HTML (original behavior)
        html_new = _text_to_plasma_html(_CURRENT_TEXT)
        if _write_if_changed(html_path, html_new):
            any_changed = True

        if any_changed:
            logger.info("Sync Plasma -> TODO: %s -> %s", html_path, todo_file)

        return any_changed
