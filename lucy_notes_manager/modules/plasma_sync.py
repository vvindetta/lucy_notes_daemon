import hashlib
import html
import logging
import os
import re
import time
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System

logger = logging.getLogger(__name__)

IgnoreMap = Dict[str, int]

# ---------------- State ---------------- #

# Markdown state (WITH **bold** markers)
_CURRENT_MD_TEXT: Optional[str] = None
_CURRENT_MD_HASH: Optional[str] = None

# Plain state (NO markdown markers) – used for convenience/logging
_CURRENT_PLAIN_TEXT: Optional[str] = None
_CURRENT_PLAIN_HASH: Optional[str] = None

# Bold items hash (canonical list of bold items, one per bold line)
_MAIN_BOLD_HASH: Optional[str] = None
_BOLD_NOTE_ITEMS_HASH: Optional[str] = None

# one-time init guard
_INIT_DONE: bool = False


# ---------------- IO ---------------- #


def _rpath(p: str) -> str:
    """Absolute + realpath to avoid symlink differences across reboot / watchdog."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(p)))


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


def _read_file_stable(path: str, tries: int = 3, delay: float = 0.03) -> str:
    """
    Some editors / Qt richtext saves can generate multiple writes.
    This helper tries to read a stable snapshot (same content twice).
    """
    last = None
    out = ""
    for _ in range(max(1, tries)):
        out = _read_file(path)
        if last is not None and out == last:
            return out
        last = out
        if delay > 0:
            time.sleep(delay)
    return out


def _write_if_changed(path: str, content: str) -> bool:
    path = _rpath(path)
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
    ap = _rpath(path)
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
    - keep tight formatting around lists:
      no blank between '-' items
      no blank after ':' before list
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


def _update_md_state(md_text: str) -> bool:
    global _CURRENT_MD_TEXT, _CURRENT_MD_HASH
    md_norm = _normalize_text(md_text)
    h = _hash_text(md_norm)
    if _CURRENT_MD_HASH == h:
        return False
    _CURRENT_MD_TEXT = md_norm
    _CURRENT_MD_HASH = h
    return True


def _set_plain_state(plain_text: str) -> None:
    global _CURRENT_PLAIN_TEXT, _CURRENT_PLAIN_HASH
    plain_norm = _normalize_text(plain_text)
    _CURRENT_PLAIN_TEXT = plain_norm
    _CURRENT_PLAIN_HASH = _hash_text(plain_norm)


# ---------------- Plain text <-> Plasma HTML ---------------- #


class _PlasmaHTMLParser(HTMLParser):
    """
    IMPORTANT:
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

        # ignore whitespace-only text nodes BETWEEN blocks
        if not self._in_block and data.strip() == "":
            return

        if self._current is None:
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


# ---------------- Bold-aware Plasma HTML parsing ---------------- #


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
    Produces lines as list of (text, is_bold) tuples.
    Ignores whitespace-only data outside block tags.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._block_depth = 0  # counts <p>/<li>
        self._bold_depth = 0
        self._span_bold_stack: List[bool] = []
        self._tag_style_bold: Dict[str, List[bool]] = {"p": [], "li": [], "font": []}
        self._lines: List[List[Tuple[str, bool]]] = [[]]

    def _in_block(self) -> bool:
        return self._block_depth > 0

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

        if tag in ("p", "li"):
            self._block_depth += 1
            self._push_tag_style_bold(tag, attrs)
            return

        if tag == "font":
            self._push_tag_style_bold("font", attrs)
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
            self._block_depth = 0
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

        if tag == "font":
            self._pop_tag_style_bold("font")
            return

        if tag in ("p", "li"):
            self._pop_tag_style_bold(tag)
            self._newline()
            self._block_depth = max(0, self._block_depth - 1)
            return

    def handle_data(self, data):
        if not self._in_body or not isinstance(data, str):
            return
        if not self._in_block() and data.strip() == "":
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


def _plasma_html_to_boldaware_lines(html_src: str) -> List[List[Tuple[str, bool]]]:
    p = _PlasmaBoldAwareParser()
    p.feed(html_src)
    return p.get_lines()


# ---------------- Markdown bold (line-level) ---------------- #

_LIST_PREFIX_RE = re.compile(r"^(\s*[-*+]\s+)(.*)$")


def _unwrap_md_bold(s: str) -> Optional[str]:
    """
    Accept:
      **text**
      ** text **
    Only if the whole string is wrapped.
    """
    s2 = s.strip()
    if len(s2) >= 4 and s2.startswith("**") and s2.endswith("**"):
        inner = s2[2:-2].strip()
        return inner
    return None


def _wrap_md_bold_line(plain_line: str) -> str:
    """
    Canonical output:
      - **Task**
      **Header**
    """
    line = plain_line.rstrip("\n")
    m = _LIST_PREFIX_RE.match(line)
    if m:
        prefix, rest = m.groups()
        rest2 = rest.strip()
        if not rest2:
            return prefix.rstrip()
        return f"{prefix}**{rest2}**"
    inner = line.strip()
    if not inner:
        return ""
    return f"**{inner}**"


def _normalize_markdown_bold(md_text: str) -> Tuple[str, str, List[str]]:
    """
    Returns:
      md_norm   - canonical markdown with **...** for bold lines
      plain_norm - markdown converted to plain text (markers removed)
      items     - bold items (ONE per bold line), without list prefix
    """
    md_text = md_text.replace("\r\n", "\n").replace("\r", "\n")
    out_md_lines: List[str] = []
    out_plain_lines: List[str] = []
    items: List[str] = []

    for raw_line in md_text.splitlines():
        if raw_line.strip() == "":
            out_md_lines.append("")
            out_plain_lines.append("")
            continue

        m = _LIST_PREFIX_RE.match(raw_line)
        if m:
            prefix, rest = m.groups()
            inner = _unwrap_md_bold(rest)
            if inner is not None:
                out_md_lines.append(f"{prefix}**{inner}**")
                out_plain_lines.append(f"{prefix}{inner}")
                if inner.strip():
                    items.append(inner.strip())
            else:
                out_md_lines.append(raw_line)
                out_plain_lines.append(raw_line)
            continue

        inner2 = _unwrap_md_bold(raw_line)
        if inner2 is not None:
            out_md_lines.append(f"**{inner2}**")
            out_plain_lines.append(inner2)
            if inner2.strip():
                items.append(inner2.strip())
        else:
            out_md_lines.append(raw_line)
            out_plain_lines.append(raw_line)

    md_norm = _normalize_text("\n".join(out_md_lines))
    plain_norm = _normalize_text("\n".join(out_plain_lines))
    items = [
        it.replace("\r\n", "\n").replace("\r", "\n").strip()
        for it in items
        if it.strip()
    ]
    return md_norm, plain_norm, items


def _markdown_to_plasma_html(md_text: str) -> str:
    """
    Convert markdown-with-bold-markers into Plasma qrichtext HTML.
    Bold is supported only for full-line bold:
      - **Task**
      **Header**
    """
    md_norm, _plain, _items = _normalize_markdown_bold(md_text)

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
    for line in md_norm.splitlines():
        if line.strip() == "":
            parts.append(
                f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
            )
            continue

        m = _LIST_PREFIX_RE.match(line)
        if m:
            prefix, rest = m.groups()
            inner = _unwrap_md_bold(rest)
            if inner is not None:
                pfx = html.escape(prefix, quote=False)
                inn = html.escape(inner, quote=False)
                parts.append(
                    f'<p style="{base_style}">{pfx}<span style=" font-weight:600;">{inn}</span></p>\n'
                )
            else:
                safe = html.escape(line, quote=False)
                parts.append(f'<p style="{base_style}">{safe}</p>\n')
            continue

        inner2 = _unwrap_md_bold(line)
        if inner2 is not None:
            inn = html.escape(inner2, quote=False)
            parts.append(
                f'<p style="{base_style}"><span style=" font-weight:600;">{inn}</span></p>\n'
            )
        else:
            safe = html.escape(line, quote=False)
            parts.append(f'<p style="{base_style}">{safe}</p>\n')

    return header + "".join(parts) + "</body></html>\n"


# ---------------- Bold mirror HTML generation ---------------- #


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


# ---------------- MAIN html -> markdown(+items) ---------------- #


def _looks_like_complete_html(s: str) -> bool:
    s2 = s.lower()
    return (
        ("<html" in s2) and ("<body" in s2) and ("</body>" in s2) and ("</html>" in s2)
    )


def _main_html_to_markdown_and_items(html_raw: str) -> Tuple[str, str, List[str]]:
    """
    Convert MAIN Plasma HTML to:
      md_norm   - markdown with **...** for bold lines
      plain_norm - plain text (markers removed)
      items     - bold items, one per bold line (without list prefix)
    """
    lines = _plasma_html_to_boldaware_lines(html_raw)
    md_lines: List[str] = []

    for ln in lines:
        plain_line = "".join(t for t, _b in ln)
        if plain_line.strip() == "":
            md_lines.append("")
            continue

        has_bold = any(b for _t, b in ln)
        if has_bold:
            md_lines.append(_wrap_md_bold_line(plain_line))
        else:
            md_lines.append(plain_line)

    md_candidate = _normalize_text("\n".join(md_lines))
    md_norm, plain_norm, items = _normalize_markdown_bold(md_candidate)
    return md_norm, plain_norm, items


# ---------------- Mirror -> MAIN bold replacement (preserve prefix/suffix) ---------------- #


def _replace_bold_region_preserve(
    line: List[Tuple[str, bool]],
    item: str,
) -> List[Tuple[str, bool]]:
    """
    Replace the region from first bold segment to last bold segment with ONE bold tuple (item),
    keeping the non-bold prefix and suffix intact.
    This prevents losing list prefix like "- ".
    """
    idxs = [i for i, (_t, b) in enumerate(line) if b]
    if not idxs:
        return line

    first = idxs[0]
    last = idxs[-1]

    out: List[Tuple[str, bool]] = []
    for i in range(0, first):
        t, _b = line[i]
        out.append((t, False))

    out.append((item, True))

    for i in range(last + 1, len(line)):
        t, _b = line[i]
        out.append((t, False))

    return out


def _replace_bold_items_in_lines(
    main_lines: List[List[Tuple[str, bool]]],
    new_items: List[str],
) -> List[List[Tuple[str, bool]]]:
    """
    Map ONE mirror item per MAIN line that contains bold (any bold in the line).
    Keeps non-bold prefix/suffix (like "- ").
    If mirror has more items -> append new bold-only lines at end.
    If mirror has fewer items -> keep remaining lines unchanged.
    """
    items = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in new_items]
    items = [it for it in items if it]

    out: List[List[Tuple[str, bool]]] = []
    k = 0

    for line in main_lines:
        has_bold = any(b for _t, b in line)
        if not has_bold:
            out.append(line)
            continue

        if k < len(items):
            out.append(_replace_bold_region_preserve(line, items[k]))
            k += 1
        else:
            out.append(line)

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


# ---------------- Startup init (cold-start fix) ---------------- #


def _init_from_disk_once(
    widget_path: str, markdown_path: str, bold_widget_path: Optional[str]
) -> None:
    """
    Initialize hashes from disk once.
    Markdown (with **bold**) is preferred as canonical if it exists.
    """
    global _INIT_DONE, _MAIN_BOLD_HASH, _BOLD_NOTE_ITEMS_HASH

    if _INIT_DONE:
        return
    _INIT_DONE = True

    widget_path = _rpath(widget_path)
    markdown_path = _rpath(markdown_path)
    bold_widget_path = _rpath(bold_widget_path) if bold_widget_path else None

    md_raw = _read_file(markdown_path)
    if md_raw:
        md_norm, plain_norm, items = _normalize_markdown_bold(md_raw)
        _update_md_state(md_norm)
        _set_plain_state(plain_norm)
        h = _items_hash(items)
        _MAIN_BOLD_HASH = h
        _BOLD_NOTE_ITEMS_HASH = h
        return

    main_html = _read_file(widget_path)
    if main_html:
        try:
            md_norm, plain_norm, items = _main_html_to_markdown_and_items(main_html)
            _update_md_state(md_norm)
            _set_plain_state(plain_norm)
            h = _items_hash(items)
            _MAIN_BOLD_HASH = h
            _BOLD_NOTE_ITEMS_HASH = h
        except Exception:
            _update_md_state("")
            _set_plain_state("")
            _MAIN_BOLD_HASH = _items_hash([])
            _BOLD_NOTE_ITEMS_HASH = _items_hash([])


# ---------------- Module ---------------- #


class PlasmaSync(AbstractModule):
    name: str = "plasma_sync"
    priority: int = 30

    template: Template = [
        (
            "--plasma-widget-path",
            str,
            None,
            "Path to the main Plasma note HTML file (widget file). Example: --plasma-widget-path ~/.local/share/plasma_notes/123.html",
        ),
        (
            "--plasma-bold-widget-path",
            str,
            None,
            "Optional: path to a separate Plasma widget HTML file used as a 'bold-only mirror'. Example: --plasma-bold-widget-path ~/.local/share/plasma_notes/bold_123.html",
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

            if isinstance(val, list):
                if len(val) != 1:
                    raise ValueError(
                        f"PlasmaSync: {flag} expects exactly one value, got {len(val)}"
                    )
                val = val[0]

            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"PlasmaSync: invalid value for {flag}")

            return _rpath(val)

        widget_path = one_value("plasma_widget_path", "--plasma-widget-path", True)
        markdown_path = one_value(
            "plasma_markdown_note_path", "--plasma-markdown-note-path", True
        )
        bold_widget_path = one_value(
            "plasma_bold_widget_path", "--plasma-bold-widget-path", False
        )

        return widget_path or "", markdown_path or "", bold_widget_path

    def _handle(self, ctx: Context) -> Optional[IgnoreMap]:
        widget_path, markdown_path, bold_widget_path = self._cfg(ctx)

        _init_from_disk_once(widget_path, markdown_path, bold_widget_path)

        path = _rpath(ctx.path)
        widget_abs = _rpath(widget_path)
        md_abs = _rpath(markdown_path)
        bold_abs = _rpath(bold_widget_path) if bold_widget_path else None

        if path == md_abs:
            return self._from_markdown(markdown_path, widget_path, bold_widget_path)

        if bold_abs and path == bold_abs:
            return self._from_bold_mirror(widget_path, markdown_path, bold_widget_path)

        if path == widget_abs:
            return self._from_main_plasma(
                widget_path, markdown_path, bold_widget_path, html_path=path
            )

        return None

    # ---------- Flow: Markdown -> MAIN (+ mirror) ----------

    def _from_markdown(
        self,
        markdown_path: str,
        widget_path: str,
        bold_widget_path: Optional[str],
    ) -> Optional[IgnoreMap]:
        md_raw = _read_file_stable(markdown_path)
        if md_raw == "" and not os.path.exists(markdown_path):
            safe_notify(
                "md_missing:" + markdown_path,
                f"Markdown note file not found:\n{markdown_path}",
            )
            return None

        md_norm, plain_norm, items = _normalize_markdown_bold(md_raw)

        changed = _update_md_state(md_norm)
        _set_plain_state(plain_norm)

        ignore: IgnoreMap = {}

        # Canonicalize markdown on disk (spaces inside **, etc.)
        if md_raw.replace("\r\n", "\n").replace("\r", "\n") != md_norm:
            if _write_if_changed(markdown_path, md_norm):
                _inc_ignore(ignore, markdown_path, 4)

        if not changed and not ignore:
            return None

        main_html = _markdown_to_plasma_html(md_norm)
        if _write_if_changed(widget_path, main_html):
            _inc_ignore(ignore, widget_path, 4)

        global _MAIN_BOLD_HASH, _BOLD_NOTE_ITEMS_HASH
        items_h = _items_hash(items)
        _MAIN_BOLD_HASH = items_h
        _BOLD_NOTE_ITEMS_HASH = items_h

        if bold_widget_path:
            mirror_html = _bold_items_to_plasma_html(items)
            if _write_if_changed(bold_widget_path, mirror_html):
                _inc_ignore(ignore, bold_widget_path, 4)

        if ignore:
            logger.info(
                "Sync Markdown(**bold**) -> MAIN Plasma%s",
                " + Mirror" if bold_widget_path else "",
            )
        return ignore or None

    # ---------- Flow: MAIN -> Markdown (+ mirror) ----------

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

        html_raw = _read_file_stable(html_path)
        if not html_raw:
            return None

        # Avoid reacting to partial writes that can temporarily drop bold.
        if not _looks_like_complete_html(html_raw):
            logger.debug("MAIN html looks incomplete; skipping this event.")
            return None

        md_norm, plain_norm, items = _main_html_to_markdown_and_items(html_raw)

        changed = _update_md_state(md_norm)
        _set_plain_state(plain_norm)

        ignore: IgnoreMap = {}

        # MAIN -> Markdown (canonical)
        if _write_if_changed(markdown_path, md_norm):
            _inc_ignore(ignore, markdown_path, 4)

        global _MAIN_BOLD_HASH, _BOLD_NOTE_ITEMS_HASH
        items_h = _items_hash(items)

        # MAIN -> Mirror (if configured)
        if bold_widget_path:
            if _MAIN_BOLD_HASH != items_h or _BOLD_NOTE_ITEMS_HASH != items_h:
                mirror_html = _bold_items_to_plasma_html(items)
                if _write_if_changed(bold_widget_path, mirror_html):
                    _inc_ignore(ignore, bold_widget_path, 4)
                _BOLD_NOTE_ITEMS_HASH = items_h

        _MAIN_BOLD_HASH = items_h

        if ignore or changed:
            logger.info(
                "Sync MAIN Plasma -> Markdown%s",
                " + Mirror" if bold_widget_path else "",
            )
        return ignore or None

    # ---------- Flow: Mirror -> MAIN -> Markdown ----------

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

        bold_html_raw = _read_file_stable(bold_widget_path)
        if not bold_html_raw:
            return None

        bold_text = _html_to_text(bold_html_raw)
        items = [ln.strip() for ln in bold_text.splitlines() if ln.strip()]

        global _BOLD_NOTE_ITEMS_HASH, _MAIN_BOLD_HASH
        items_h = _items_hash(items)
        if _BOLD_NOTE_ITEMS_HASH == items_h:
            return None
        _BOLD_NOTE_ITEMS_HASH = items_h

        main_html_raw = _read_file_stable(widget_path)
        if not main_html_raw:
            return None
        if not _looks_like_complete_html(main_html_raw):
            logger.debug("MAIN html looks incomplete; skipping mirror->main update.")
            return None

        main_lines = _plasma_html_to_boldaware_lines(main_html_raw)
        new_lines = _replace_bold_items_in_lines(main_lines, items)
        new_main_html = _boldaware_lines_to_plasma_html(new_lines)

        ignore: IgnoreMap = {}

        if _write_if_changed(widget_path, new_main_html):
            _inc_ignore(ignore, widget_path, 4)

        # MAIN -> Markdown (canonical with **bold**)
        md_norm, plain_norm, items2 = _main_html_to_markdown_and_items(new_main_html)
        _update_md_state(md_norm)
        _set_plain_state(plain_norm)

        if _write_if_changed(markdown_path, md_norm):
            _inc_ignore(ignore, markdown_path, 4)

        # normalize mirror (keep it in canonical bold-only form)
        if _write_if_changed(bold_widget_path, _bold_items_to_plasma_html(items2)):
            _inc_ignore(ignore, bold_widget_path, 4)

        items2_h = _items_hash(items2)
        _MAIN_BOLD_HASH = items2_h
        _BOLD_NOTE_ITEMS_HASH = items2_h

        if ignore:
            logger.info("Sync BOLD mirror -> MAIN -> Markdown")
        return ignore or None
