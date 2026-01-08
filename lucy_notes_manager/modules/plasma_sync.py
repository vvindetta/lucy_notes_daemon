import hashlib
import html
import logging
import os
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System

logger = logging.getLogger(__name__)

IgnoreMap = Dict[str, int]

# ---------------- State ---------------- #

_CURRENT_TEXT: Optional[str] = None
_CURRENT_HASH: Optional[str] = None

_MAIN_BOLD_HASH: Optional[str] = None
_BOLD_NOTE_ITEMS_HASH: Optional[str] = None


# ---------------- IO ---------------- #


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.debug("File not found: %s", path)
        return ""
    except PermissionError as e:
        logger.error("Permission error reading %s: %s", path, e)
        safe_notify("read_perm:" + path, f"Permission denied reading:\n{path}\n\n{e}")
        return ""
    except OSError as e:
        logger.error("OS error reading %s: %s", path, e)
        safe_notify("read_os:" + path, f"Failed to read file:\n{path}\n\n{e}")
        return ""


def _write_if_changed(path: str, content: str) -> bool:
    path = os.path.abspath(path)
    old = _read_file(path)
    if old == content:
        return False

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except PermissionError as e:
        logger.error("Permission error writing %s: %s", path, e)
        safe_notify("write_perm:" + path, f"Permission denied writing:\n{path}\n\n{e}")
        return False
    except OSError as e:
        logger.error("OS error writing %s: %s", path, e)
        safe_notify("write_os:" + path, f"Failed to write file:\n{path}\n\n{e}")
        return False


def _inc_ignore(ignore: IgnoreMap, path: str, times: int = 1) -> None:
    ap = os.path.abspath(path)
    ignore[ap] = ignore.get(ap, 0) + int(times)


# ---------------- Text helpers ---------------- #


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_text(text: str) -> str:
    """
    Normalization goals:
    - stable newlines
    - trim leading/trailing blank lines
    - collapse multiple blank lines to at most 1
    - keep tight formatting around lists (no blank between '-' items or after ':' before list)
    """
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

        result.extend([""] * keep_blank_count)
        i = j

    while result and result[-1].strip() == "":
        result.pop()

    return "\n".join(result)


def _update_state_from_text(text: str) -> bool:
    global _CURRENT_TEXT, _CURRENT_HASH

    norm = _normalize_text(text)
    new_hash = _hash_text(norm)

    if _CURRENT_HASH == new_hash:
        return False

    _CURRENT_TEXT = norm
    _CURRENT_HASH = new_hash
    return True


# ---------------- Plain text <-> Plasma HTML ---------------- #


class _PlasmaHTMLParser(HTMLParser):
    """
    IMPORTANT FIX:
    HTMLParser emits handle_data() for whitespace between tags.
    Plasma HTML has newlines between <p>...</p> blocks,
    and we must NOT treat those as user content.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._in_block = False  # inside <p> or <li>
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
            # close previous block if still open
            if self._current is not None and self._in_block:
                self._lines.append(self._current)
            self._current = ""
            self._in_block = True
            return

        if not self._in_block:
            return

        if tag == "br":
            if self._current is None:
                self._current = ""
            self._current += "\n"

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "body":
            if self._current is not None and self._in_block:
                self._lines.append(self._current)
            self._current = None
            self._in_body = False
            self._in_block = False
            return

        if not self._in_body:
            return

        if tag in ("p", "li"):
            if self._current is None:
                self._current = ""
            self._lines.append(self._current)
            self._current = None
            self._in_block = False

    def handle_data(self, data):
        if not self._in_body:
            return
        if not isinstance(data, str):
            return

        # CRITICAL: ignore whitespace-only text nodes between tags
        if not self._in_block and data.strip() == "":
            return

        if self._current is None:
            # Only start collecting if it has real content
            if data.strip() == "":
                return
            self._current = ""
        self._current += data

    def get_text(self) -> str:
        if self._current is not None and self._in_block:
            self._lines.append(self._current)
        self._current = None
        self._in_block = False
        return "\n".join(self._lines)


def _html_to_text(html_src: str) -> str:
    parser = _PlasmaHTMLParser()
    parser.feed(html_src)
    return _normalize_text(parser.get_text())


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

    return header + "".join(parts) + "</body></html>\n"


# ---------------- Bold-only overlay ---------------- #


def _style_is_bold(style: str) -> bool:
    s = style.lower().replace(" ", "")
    if "font-weight:bold" in s:
        return True
    if "font-weight:" in s:
        try:
            idx = s.rfind("font-weight:")
            val = s[idx + len("font-weight:") :]
            val = val.split(";")[0]
            return int(val) >= 600
        except Exception:
            return False
    return False


class _PlasmaBoldAwareParser(HTMLParser):
    """
    Same whitespace-between-tags problem exists here too.
    We must ignore whitespace-only data outside block tags.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._in_block = False  # inside <p>/<li>/<font> etc.
        self._bold_depth = 0
        self._span_bold_stack: List[bool] = []
        self._tag_style_bold: Dict[str, List[bool]] = {"p": [], "li": [], "font": []}
        self._lines: List[List[Tuple[str, bool]]] = [[]]

    def _newline(self) -> None:
        self._lines.append([])

    def _append(self, text: str) -> None:
        if not text:
            return
        self._lines[-1].append((text, self._bold_depth > 0))

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

        if tag in ("p", "li", "font"):
            self._in_block = True
            self._push_tag_style_bold(tag, attrs)
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

        if tag == "br":
            self._newline()
            return

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "body":
            self._in_body = False
            self._in_block = False
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

        if tag in ("p", "li", "font"):
            self._pop_tag_style_bold(tag)
            if tag in ("p", "li"):
                self._newline()
            self._in_block = False
            return

    def handle_data(self, data):
        if not self._in_body or not isinstance(data, str):
            return
        if not self._in_block and data.strip() == "":
            return
        self._append(data)

    def get_lines(self) -> List[List[Tuple[str, bool]]]:
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

        def txt(ln: List[Tuple[str, bool]]) -> str:
            return "".join(t for t, _ in ln)

        i = 0
        while i < len(merged_lines) and txt(merged_lines[i]).strip() == "":
            i += 1
        merged_lines = merged_lines[i:]

        j = len(merged_lines) - 1
        while j >= 0 and txt(merged_lines[j]).strip() == "":
            j -= 1
        merged_lines = merged_lines[: j + 1]

        out: List[List[Tuple[str, bool]]] = []
        prev_blank = False
        for ln in merged_lines:
            blank = txt(ln).strip() == ""
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
    norm = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm = [it for it in norm if it]
    return _hash_text("\n".join(norm))


def _bold_items_to_plasma_html(items: List[str]) -> str:
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

    norm = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm = [it for it in norm if it]

    parts: List[str] = []
    for line in norm:
        safe = html.escape(line, quote=False)
        parts.append(
            f'<p style="{base_style}"><span style=" font-weight:600;">{safe}</span></p>\n'
        )

    return header + "".join(parts) + "</body></html>\n"


def _plasma_html_to_boldaware_lines(html_src: str) -> List[List[Tuple[str, bool]]]:
    p = _PlasmaBoldAwareParser()
    p.feed(html_src)
    return p.get_lines()


def _replace_bold_items_in_lines(
    main_lines: List[List[Tuple[str, bool]]],
    new_items: List[str],
) -> List[List[Tuple[str, bool]]]:
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
                new_line.append((t, False))
            i += 1

        out.append(new_line)

    while k < len(items):
        out.append([(items[k], True)])
        k += 1

    return out


def _boldaware_lines_to_plasma_html(lines: List[List[Tuple[str, bool]]]) -> str:
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

    def plain(ln: List[Tuple[str, bool]]) -> str:
        return "".join(t for t, _ in ln)

    parts: List[str] = []
    for ln in lines:
        if plain(ln).strip() == "":
            parts.append(
                f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
            )
            continue

        inner: List[str] = []
        for t, b in ln:
            safe = html.escape(t, quote=False)
            inner.append(
                f'<span style=" font-weight:600;">{safe}</span>' if b else safe
            )

        parts.append(f'<p style="{base_style}">{"".join(inner)}</p>\n')

    return header + "".join(parts) + "</body></html>\n"


# ---------------- Module ---------------- #


class PlasmaSync(AbstractModule):
    name: str = "plasma_notes_sync"
    priority: int = 30

    template: Template = [
        (
            "--plasma-widget-path",
            str,
            None,
            "Path to the main Plasma note HTML file (widget file). Example: --plasma-widget-path ~/.local/share/plasma_notes/1234567890.html",
        ),
        (
            "--plasma-bold-widget-path",
            str,
            None,
            "Optional: path to a separate Plasma widget HTML file used as a 'bold-only mirror' (stores only bold fragments). Example: --plasma-bold-widget-path ~/.local/share/plasma_notes/bold_123.html",
        ),
        (
            "--plasma-markdown-note-path",
            str,
            None,
            "Path to the plain-text Markdown note that is synced with the Plasma widget(s). Example: --plasma-markdown-note-path ~/notes/todo.md",
        ),
    ]

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def _cfg(self, ctx: Context) -> tuple[str, str, Optional[str]]:
        cfg = ctx.config  # trust parse_args shape

        def one_value(key: str, flag: str, required: bool) -> Optional[str]:
            val = cfg.get(key)
            if val is None:
                if required:
                    raise ValueError(f"PlasmaSync: missing required {flag}")
                return None

            # Old parse_args may still give a list; do NOT "take first" silently.
            if isinstance(val, list):
                if len(val) != 1:
                    raise ValueError(
                        f"PlasmaSync: {flag} expects exactly one value, got {len(val)}"
                    )
                val = val[0]

            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"PlasmaSync: invalid value for {flag}")

            return os.path.abspath(os.path.expanduser(val))

        widget_path = one_value("plasma_widget_path", "--plasma-widget-path", True)
        markdown_path = one_value(
            "plasma_markdown_note_path", "--plasma-markdown-note-path", True
        )
        bold_widget_path = one_value(
            "plasma_bold_widget_path", "--plasma-bold-widget-path", False
        )

        # mypy: widget_path/markdown_path are required so they are not None here
        return widget_path or "", markdown_path or "", bold_widget_path

    def _handle(self, ctx: Context) -> Optional[IgnoreMap]:
        widget_path, markdown_path, bold_widget_path = self._cfg(ctx)

        path = os.path.abspath(ctx.path)
        widget_abs = os.path.abspath(widget_path)
        md_abs = os.path.abspath(markdown_path)
        bold_abs = (
            os.path.abspath(bold_widget_path) if bold_widget_path is not None else None
        )

        if path == md_abs:
            return self._from_markdown(markdown_path, widget_path, bold_widget_path)

        if bold_abs and path == bold_abs:
            return self._from_bold_mirror(widget_path, markdown_path, bold_widget_path)

        if path == widget_abs:
            return self._from_main_plasma(
                widget_path, markdown_path, bold_widget_path, html_path=path
            )

        return None

    def _from_markdown(
        self,
        markdown_path: str,
        widget_path: str,
        bold_widget_path: Optional[str],
    ) -> Optional[IgnoreMap]:
        text_raw = _read_file(markdown_path)
        if text_raw == "" and not os.path.exists(markdown_path):
            safe_notify(
                "md_missing:" + markdown_path,
                f"Markdown note file not found:\n{markdown_path}",
            )
            return None

        if not _update_state_from_text(text_raw):
            return None
        if _CURRENT_TEXT is None:
            return None

        ignore: IgnoreMap = {}

        html_new = _text_to_plasma_html(_CURRENT_TEXT)
        if _write_if_changed(widget_path, html_new):
            _inc_ignore(ignore, widget_path, 1)

        if bold_widget_path:
            global _MAIN_BOLD_HASH
            _MAIN_BOLD_HASH = _items_hash([])
            if _write_if_changed(bold_widget_path, _bold_items_to_plasma_html([])):
                _inc_ignore(ignore, bold_widget_path, 1)

        if ignore:
            logger.info(
                "Sync Markdown -> Plasma: %s -> %s",
                os.path.abspath(markdown_path),
                os.path.abspath(widget_path),
            )
        return ignore or None

    def _from_main_plasma(
        self,
        widget_path: str,
        markdown_path: str,
        bold_widget_path: Optional[str],
        html_path: str,
    ) -> Optional[IgnoreMap]:
        if not os.path.exists(html_path):
            logger.debug("Plasma widget html not found (ignored): %s", html_path)
            return None

        html_raw = _read_file(html_path)
        text_from_html = _html_to_text(html_raw)

        text_changed = _update_state_from_text(text_from_html)

        ignore: IgnoreMap = {}

        if text_changed and _CURRENT_TEXT is not None:
            if _write_if_changed(markdown_path, _CURRENT_TEXT):
                _inc_ignore(ignore, markdown_path, 1)

            # If no bold mirror is used, normalize the main widget HTML too.
            if not bold_widget_path:
                html_new = _text_to_plasma_html(_CURRENT_TEXT)
                if _write_if_changed(html_path, html_new):
                    _inc_ignore(ignore, html_path, 1)

        if bold_widget_path:
            bold_items = _extract_bold_items_from_html(html_raw)
            new_bold_hash = _items_hash(bold_items)

            global _MAIN_BOLD_HASH
            if _MAIN_BOLD_HASH != new_bold_hash:
                _MAIN_BOLD_HASH = new_bold_hash
                if _write_if_changed(
                    bold_widget_path, _bold_items_to_plasma_html(bold_items)
                ):
                    _inc_ignore(ignore, bold_widget_path, 1)

        if ignore:
            logger.info("Sync MAIN Plasma -> Markdown")
        return ignore or None

    def _from_bold_mirror(
        self,
        widget_path: str,
        markdown_path: str,
        bold_widget_path: Optional[str],
    ) -> Optional[IgnoreMap]:
        if not bold_widget_path:
            return None

        if not os.path.exists(bold_widget_path):
            logger.debug("Bold mirror not found (ignored): %s", bold_widget_path)
            return None

        bold_html_raw = _read_file(bold_widget_path)

        bold_text = _html_to_text(bold_html_raw)
        items = [ln.strip() for ln in bold_text.splitlines() if ln.strip()]

        global _BOLD_NOTE_ITEMS_HASH, _MAIN_BOLD_HASH
        items_h = _items_hash(items)
        if _BOLD_NOTE_ITEMS_HASH == items_h:
            return None
        _BOLD_NOTE_ITEMS_HASH = items_h

        main_html_raw = _read_file(widget_path)

        main_lines = _plasma_html_to_boldaware_lines(main_html_raw)
        new_lines = _replace_bold_items_in_lines(main_lines, items)
        new_main_html = _boldaware_lines_to_plasma_html(new_lines)

        ignore: IgnoreMap = {}

        if _write_if_changed(widget_path, new_main_html):
            _inc_ignore(ignore, widget_path, 1)

        new_plain = _html_to_text(new_main_html)
        if _update_state_from_text(new_plain) and _CURRENT_TEXT is not None:
            if _write_if_changed(markdown_path, _CURRENT_TEXT):
                _inc_ignore(ignore, markdown_path, 1)

        # Keep bold mirror normalized too
        if _write_if_changed(bold_widget_path, _bold_items_to_plasma_html(items)):
            _inc_ignore(ignore, bold_widget_path, 1)

        _MAIN_BOLD_HASH = _items_hash(items)

        if ignore:
            logger.info("Sync BOLD mirror -> MAIN -> Markdown")

        return ignore or None
