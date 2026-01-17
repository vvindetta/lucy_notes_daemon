import hashlib
import html
import logging
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System

logger = logging.getLogger(__name__)

IgnoreMap = Dict[str, int]

_IGNORE_BURST = 1


# ---------------- State ---------------- #

_INIT_DONE: bool = False

_LAST_DOC_HASH: Optional[str] = None  # canonical doc hash (content + bold + list state)
_LAST_BOLD_ITEMS_HASH: Optional[str] = None  # mirror items hash
_LAST_CSS_STYLE: Optional[bool] = None  # last applied --plasma-css-style state


# ---------------- IO ---------------- #


def _rpath(p: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(p)))


def _read_file(path: str) -> str:
    try:
        with open(_rpath(path), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
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


# ---------------- Hashing / Normalization ---------------- #


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _trim_trailing_spaces_per_line(text: str) -> str:
    return "\n".join([ln.rstrip() for ln in _normalize_newlines(text).split("\n")])


def _normalize_md(text: str) -> str:
    # keep user formatting, just normalize newlines + trailing spaces
    return _trim_trailing_spaces_per_line(text).strip("\n")


# ---------------- Document model ---------------- #


@dataclass
class DocLine:
    kind: str  # "p" or "li"
    state: Optional[str]  # for li: "unchecked" / "checked" / None
    segs: List[Tuple[str, bool]]  # (text, is_bold)


def _segs_plain(segs: List[Tuple[str, bool]]) -> str:
    return "".join(t for t, _b in segs)


def _segs_has_bold(segs: List[Tuple[str, bool]]) -> bool:
    return any(b for _t, b in segs)


def _merge_segs(segs: List[Tuple[str, bool]]) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    for t, b in segs:
        if not t:
            continue
        if out and out[-1][1] == b:
            out[-1] = (out[-1][0] + t, b)
        else:
            out.append((t, b))
    return out


# ---------------- Markdown (**bold**) parsing ---------------- #


def _find_unescaped_double_stars(s: str) -> List[int]:
    pos: List[int] = []
    i = 0
    while i < len(s) - 1:
        if s[i] == "\\":
            i += 2
            continue
        if s[i : i + 2] == "**":
            pos.append(i)
            i += 2
            continue
        i += 1
    # if odd, last one is treated as literal
    if len(pos) % 2 == 1:
        pos = pos[:-1]
    return pos


def _md_line_to_segs(line: str) -> List[Tuple[str, bool]]:
    line = _normalize_newlines(line)
    stars = _find_unescaped_double_stars(line)
    if not stars:
        return [(line.replace("\\*", "*").replace("\\\\", "\\"), False)]

    cut = set(stars)
    segs: List[Tuple[str, bool]] = []
    buf: List[str] = []
    bold = False
    i = 0

    while i < len(line):
        if i in cut and line[i : i + 2] == "**":
            txt = "".join(buf)
            if txt:
                txt = txt.replace("\\*", "*").replace("\\\\", "\\")
                segs.append((txt, bold))
            buf = []
            bold = not bold
            i += 2
            continue

        if line[i] == "\\" and i + 1 < len(line):
            # keep escaped char literally
            buf.append(line[i + 1])
            i += 2
            continue

        buf.append(line[i])
        i += 1

    txt = "".join(buf)
    if txt:
        segs.append((txt, bold))

    return _merge_segs(segs)


def _escape_md_text(s: str) -> str:
    # escape backslash first, then asterisk
    s = s.replace("\\", "\\\\")
    s = s.replace("*", "\\*")
    return s


def _segs_to_md(segs: List[Tuple[str, bool]]) -> str:
    out: List[str] = []
    for t, b in segs:
        t = _escape_md_text(t)
        if b and t:
            out.append(f"**{t}**")
        else:
            out.append(t)
    return "".join(out)


def _md_to_doc(md_text: str) -> List[DocLine]:
    md_text = _normalize_newlines(md_text)
    lines: List[DocLine] = []
    for raw in md_text.split("\n"):
        ln = raw.rstrip("\n")
        if ln.strip() == "":
            lines.append(DocLine(kind="p", state=None, segs=[]))
            continue

        # checkbox list item
        low = ln.lstrip()
        if low.startswith("- [ ] "):
            content = low[len("- [ ] ") :]
            segs = _md_line_to_segs(content)
            lines.append(DocLine(kind="li", state="unchecked", segs=segs))
            continue
        if low.lower().startswith("- [x] "):
            content = low[6:]
            segs = _md_line_to_segs(content)
            lines.append(DocLine(kind="li", state="checked", segs=segs))
            continue

        # normal paragraph
        segs = _md_line_to_segs(ln)
        lines.append(DocLine(kind="p", state=None, segs=segs))

    # trim leading/trailing empty paragraphs
    while lines and lines[0].kind == "p" and _segs_plain(lines[0].segs).strip() == "":
        lines.pop(0)
    while lines and lines[-1].kind == "p" and _segs_plain(lines[-1].segs).strip() == "":
        lines.pop()

    return lines


def _doc_to_md(doc: List[DocLine]) -> str:
    out_lines: List[str] = []
    for dl in doc:
        if dl.kind == "p":
            out_lines.append(_segs_to_md(dl.segs) if dl.segs else "")
            continue

        # li
        prefix = "- "
        if dl.state == "unchecked":
            prefix = "- [ ] "
        elif dl.state == "checked":
            prefix = "- [x] "
        out_lines.append(prefix + _segs_to_md(dl.segs))

    return _normalize_md("\n".join(out_lines))


def _doc_hash(doc: List[DocLine]) -> str:
    return _hash_text(_doc_to_md(doc))


# ---------------- Checkbox CSS toggling (in-place) ---------------- #

_CHECKBOX_CSS_UNCHECKED = 'li.unchecked::marker { content: "\\2610"; }'
_CHECKBOX_CSS_CHECKED = 'li.checked::marker { content: "\\2612"; }'


def _apply_checkbox_marker_css(html_src: str, enable: bool) -> str:
    """
    Enable/disable ONLY the CSS marker rules:
      - li.unchecked::marker ...
      - li.checked::marker ...
    Keeps the rest of the HTML intact (no body reformat).
    """
    m = re.search(
        r'(<style[^>]*type="text/css"[^>]*>\s*)(.*?)(\s*</style>)',
        html_src,
        flags=re.I | re.S,
    )
    if not m:
        return html_src

    pre, css, post = m.group(1), m.group(2), m.group(3)
    lines = css.splitlines()

    # remove existing marker rules (any variant/spacing)
    cleaned: List[str] = []
    for ln in lines:
        if "li.unchecked::marker" in ln:
            continue
        if "li.checked::marker" in ln:
            continue
        cleaned.append(ln)

    if enable:
        cleaned.append(_CHECKBOX_CSS_UNCHECKED)
        cleaned.append(_CHECKBOX_CSS_CHECKED)

    new_block = pre + "\n".join(cleaned) + post
    return html_src[: m.start()] + new_block + html_src[m.end() :]


def _ensure_widget_checkbox_css(
    widget_path: str, css_style: bool, ignore: IgnoreMap
) -> None:
    """
    If config flag changed, patch only the <style> block in the widget HTML
    to add/remove marker rules. This runs even when semantic doc didn't change.
    """
    global _LAST_CSS_STYLE

    if _LAST_CSS_STYLE is not None and _LAST_CSS_STYLE == css_style:
        return

    html_raw = _read_file(widget_path)
    if not html_raw.strip():
        _LAST_CSS_STYLE = css_style
        return

    html_new = _apply_checkbox_marker_css(html_raw, css_style)
    if _write_if_changed(widget_path, html_new):
        _inc_ignore(ignore, widget_path, _IGNORE_BURST)

    _LAST_CSS_STYLE = css_style


# ---------------- Plasma HTML parsing (bold-aware + list-aware) ---------------- #


def _style_is_bold(style: str) -> bool:
    s = (style or "").lower().replace(" ", "")
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


class _PlasmaDocParser(HTMLParser):
    """
    Robust against nested blocks like: <li ...><p ...>text</p></li>
    - top-level <li> produces one DocLine(kind="li")
    - top-level <p> produces one DocLine(kind="p")
    - <p> inside <li> is treated as inline container, not a separate line
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._in_li_depth = 0

        self._cur: Optional[DocLine] = None

        self._bold_depth = 0
        self._span_bold_stack: List[bool] = []
        self._font_bold_stack: List[bool] = []

        self._doc: List[DocLine] = []

    def _finalize(self) -> None:
        if self._cur is None:
            return
        self._cur.segs = _merge_segs(self._cur.segs)
        self._doc.append(self._cur)
        self._cur = None

    def _ensure_cur(self, kind: str, state: Optional[str]) -> None:
        if self._cur is None:
            self._cur = DocLine(kind=kind, state=state, segs=[])

    def _append(self, text: str) -> None:
        if self._cur is None:
            return
        if not text:
            return
        self._cur.segs.append((text, self._bold_depth > 0))

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "body":
            self._in_body = True
            return
        if not self._in_body:
            return

        if tag == "li":
            # new logical line (list item)
            self._finalize()
            cls = ""
            for k, v in attrs:
                if k.lower() == "class" and isinstance(v, str):
                    cls = v.lower()
                    break
            state = None
            if "unchecked" in cls:
                state = "unchecked"
            elif "checked" in cls:
                state = "checked"
            self._ensure_cur("li", state)
            self._in_li_depth += 1
            return

        if tag == "p":
            # only top-level <p> makes a new line
            if self._in_li_depth == 0:
                self._finalize()
                self._ensure_cur("p", None)
            return

        if tag == "br":
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
            is_b = _style_is_bold(style)
            self._span_bold_stack.append(is_b)
            if is_b:
                self._bold_depth += 1
            return

        if tag == "font":
            style = ""
            for k, v in attrs:
                if k.lower() == "style" and isinstance(v, str):
                    style = v
                    break
            is_b = _style_is_bold(style)
            self._font_bold_stack.append(is_b)
            if is_b:
                self._bold_depth += 1
            return

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "body":
            self._finalize()
            self._in_body = False
            self._in_li_depth = 0
            return
        if not self._in_body:
            return

        if tag == "li":
            self._in_li_depth = max(0, self._in_li_depth - 1)
            # only finalize when we close the outer li
            if self._in_li_depth == 0:
                self._finalize()
            return

        if tag == "p":
            if self._in_li_depth == 0:
                self._finalize()
            return

        if tag in ("b", "strong"):
            self._bold_depth = max(0, self._bold_depth - 1)
            return

        if tag == "span":
            if self._span_bold_stack:
                was = self._span_bold_stack.pop()
                if was:
                    self._bold_depth = max(0, self._bold_depth - 1)
            return

        if tag == "font":
            if self._font_bold_stack:
                was = self._font_bold_stack.pop()
                if was:
                    self._bold_depth = max(0, self._bold_depth - 1)
            return

    def handle_data(self, data):
        if not self._in_body or not isinstance(data, str):
            return
        if self._cur is None and data.strip() == "":
            return
        text = html.unescape(data)
        if self._cur is None and text.strip() == "":
            return
        if self._cur is None:
            self._ensure_cur("p", None)
        self._append(text)

    def get_doc(self) -> List[DocLine]:
        self._finalize()

        # trim empty leading/trailing paragraphs
        doc = self._doc[:]
        while doc and doc[0].kind == "p" and _segs_plain(doc[0].segs).strip() == "":
            doc.pop(0)
        while doc and doc[-1].kind == "p" and _segs_plain(doc[-1].segs).strip() == "":
            doc.pop()
        return doc


def _html_to_doc(html_src: str) -> List[DocLine]:
    p = _PlasmaDocParser()
    p.feed(html_src)
    return p.get_doc()


# ---------------- Plasma HTML generation (from doc) ---------------- #


def _doc_to_plasma_html(doc: List[DocLine], css_style: bool = False) -> str:
    header = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        '<html><head><meta name="qrichtext" content="1" />'
        '<meta charset="utf-8" />'
        '<style type="text/css">\n'
        "p, li { white-space: pre-wrap; }\n"
        "hr { height: 1px; border-width: 0; }\n"
        + (
            'li.unchecked::marker { content: "\\2610"; }\n'
            'li.checked::marker { content: "\\2612"; }\n'
            if css_style
            else ""
        )
        "</style></head>"
        "<body style=\" font-family:'Noto Sans'; font-size:10pt; "
        'font-weight:400; font-style:normal;">\n'
    )

    base_style = (
        " margin-top:0px; margin-bottom:0px; margin-left:0px; "
        "margin-right:0px; -qt-block-indent:0; text-indent:0px;"
    )

    def segs_to_inner(segs: List[Tuple[str, bool]]) -> str:
        inner: List[str] = []
        for t, b in _merge_segs(segs):
            safe = html.escape(t, quote=False)
            inner.append(f'<span style=" font-weight:600;">{safe}</span>' if b else safe)
        return "".join(inner)

    parts: List[str] = []
    in_ul = False

    for dl in doc:
        if dl.kind == "li":
            if not in_ul:
                parts.append("<ul>\n")
                in_ul = True

            cls = ""
            if dl.state == "unchecked":
                cls = ' class="unchecked"'
            elif dl.state == "checked":
                cls = ' class="checked"'

            inner = segs_to_inner(dl.segs)
            # keep per-item paragraph style for consistent margins
            parts.append(f'<li{cls}><p style="{base_style}">{inner}</p></li>\n')
            continue

        # paragraph
        if in_ul:
            parts.append("</ul>\n")
            in_ul = False

        if _segs_plain(dl.segs).strip() == "":
            parts.append(
                f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
            )
        else:
            inner = segs_to_inner(dl.segs)
            parts.append(f'<p style="{base_style}">{inner}</p>\n')

    if in_ul:
        parts.append("</ul>\n")

    return header + "".join(parts) + "</body></html>\n"


# ---------------- Bold mirror helpers ---------------- #


def _extract_bold_items_from_doc(doc: List[DocLine]) -> List[str]:
    items: List[str] = []
    for dl in doc:
        buf = [t for (t, b) in dl.segs if b and t]
        s = "".join(buf).strip()
        if s:
            items.append(s)
    return items


def _items_hash(items: List[str]) -> str:
    norm = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm = [it for it in norm if it]
    return _hash_text("\n".join(norm))


def _bold_items_to_plasma_html(items: List[str]) -> str:
    # each line is fully bold in the mirror; no checkbox marker CSS needed
    doc = [
        DocLine(kind="p", state=None, segs=[(it.strip(), True)])
        for it in items
        if it.strip()
    ]
    return _doc_to_plasma_html(doc, css_style=False)


def _mirror_html_to_items(mirror_html: str) -> List[str]:
    # mirror contains bold lines only; we read visible text lines
    doc = _html_to_doc(mirror_html)
    items: List[str] = []
    for dl in doc:
        s = _segs_plain(dl.segs).strip()
        if s:
            items.append(s)
    return items


def _apply_mirror_items_to_doc(
    main_doc: List[DocLine], items: List[str]
) -> List[DocLine]:
    """
    Line-safe mapping:
    - every line that contains ANY bold in MAIN consumes exactly 1 item from mirror
    - we replace the whole line content with that item (fully bold), preserving line kind/state
    - if mirror has more items, append them as new bold paragraphs
    - if mirror has fewer, we keep remaining bold lines unchanged (no data loss)
    """
    cleaned = [it.strip() for it in items if it.strip()]
    out: List[DocLine] = []
    k = 0

    for dl in main_doc:
        if not _segs_has_bold(dl.segs):
            out.append(dl)
            continue

        if k < len(cleaned):
            out.append(DocLine(kind=dl.kind, state=dl.state, segs=[(cleaned[k], True)]))
            k += 1
        else:
            out.append(dl)

    while k < len(cleaned):
        out.append(DocLine(kind="p", state=None, segs=[(cleaned[k], True)]))
        k += 1

    return out


# ---------------- Startup init ---------------- #


def _init_from_disk_once(
    widget_path: str, markdown_path: str, bold_widget_path: Optional[str]
) -> None:
    global _INIT_DONE, _LAST_DOC_HASH, _LAST_BOLD_ITEMS_HASH, _LAST_CSS_STYLE
    if _INIT_DONE:
        return
    _INIT_DONE = True

    widget_path = _rpath(widget_path)
    markdown_path = _rpath(markdown_path)
    bold_widget_path = _rpath(bold_widget_path) if bold_widget_path else None

    # reset css-style tracking on startup (unknown until first handle)
    _LAST_CSS_STYLE = None

    # prefer markdown as canonical at boot if it exists
    md = _read_file(markdown_path)
    if md.strip():
        doc = _md_to_doc(_normalize_md(md))
        _LAST_DOC_HASH = _doc_hash(doc)
        _LAST_BOLD_ITEMS_HASH = _items_hash(_extract_bold_items_from_doc(doc))
        return

    html_main = _read_file(widget_path)
    if html_main.strip():
        doc = _html_to_doc(html_main)
        _LAST_DOC_HASH = _doc_hash(doc)
        _LAST_BOLD_ITEMS_HASH = _items_hash(_extract_bold_items_from_doc(doc))
        return

    # empty
    _LAST_DOC_HASH = _hash_text("")
    _LAST_BOLD_ITEMS_HASH = _hash_text("")


# ---------------- Module ---------------- #


class PlasmaSync(AbstractModule):
    name: str = "plasma_sync"
    priority: int = 30

    template: Template = [
        (
            "--plasma-widget-path",
            str,
            None,
            "Path to the main Plasma note HTML file (widget file).",
        ),
        (
            "--plasma-bold-widget-path",
            str,
            None,
            "Optional: path to a Plasma widget HTML file used as a 'bold-only mirror'.",
        ),
        (
            "--plasma-markdown-note-path",
            str,
            None,
            "Path to the Markdown note (supports **bold** and - [ ] / - [x]).",
        ),
        (
            "--plasma-css-style",
            bool,
            False,
            "Enable CSS checkbox markers for Plasma HTML (li.*::marker). Default: False.",
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

    def _cfg(self, ctx: Context) -> tuple[str, str, Optional[str], bool]:
        cfg = ctx.config

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

        def bool_value(keys: List[str], default: bool) -> bool:
            # supports both plasma_css_style and plasma-css-style
            v = None
            for k in keys:
                if k in cfg:
                    v = cfg.get(k)
                    break
            if v is None:
                return default
            if isinstance(v, list):
                if len(v) != 1:
                    raise ValueError("PlasmaSync: --plasma-css-style expects one value")
                v = v[0]
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            if isinstance(v, str):
                s = v.strip().lower()
                if s in ("1", "true", "yes", "y", "on", "enable", "enabled"):
                    return True
                if s in ("0", "false", "no", "n", "off", "disable", "disabled"):
                    return False
            raise ValueError("PlasmaSync: invalid value for --plasma-css-style")

        widget_path = one_value("plasma_widget_path", "--plasma-widget-path", True)
        markdown_path = one_value(
            "plasma_markdown_note_path", "--plasma-markdown-note-path", True
        )
        bold_widget_path = one_value(
            "plasma_bold_widget_path", "--plasma-bold-widget-path", False
        )

        css_style = bool_value(["plasma_css_style", "plasma-css-style"], default=False)

        return widget_path or "", markdown_path or "", bold_widget_path, css_style

    def _handle(self, ctx: Context) -> Optional[IgnoreMap]:
        widget_path, markdown_path, bold_widget_path, css_style = self._cfg(ctx)

        _init_from_disk_once(widget_path, markdown_path, bold_widget_path)

        path = _rpath(ctx.path)
        widget_abs = _rpath(widget_path)
        md_abs = _rpath(markdown_path)
        bold_abs = _rpath(bold_widget_path) if bold_widget_path else None

        if path == md_abs:
            return self._from_markdown(
                markdown_path, widget_path, bold_widget_path, css_style
            )

        if bold_abs and path == bold_abs:
            return self._from_bold_mirror(
                widget_path, markdown_path, bold_widget_path, css_style
            )

        if path == widget_abs:
            return self._from_main_plasma(
                widget_path,
                markdown_path,
                bold_widget_path,
                css_style,
                html_path=path,
            )

        return None

    def _sync_bold_mirror_from_doc(
        self, doc: List[DocLine], bold_widget_path: Optional[str], ignore: IgnoreMap
    ) -> None:
        global _LAST_BOLD_ITEMS_HASH

        if not bold_widget_path:
            return

        items = _extract_bold_items_from_doc(doc)
        h = _items_hash(items)
        if _LAST_BOLD_ITEMS_HASH == h:
            return

        _LAST_BOLD_ITEMS_HASH = h
        mirror_html = _bold_items_to_plasma_html(items)
        if _write_if_changed(bold_widget_path, mirror_html):
            _inc_ignore(ignore, bold_widget_path, _IGNORE_BURST)

    def _from_markdown(
        self,
        markdown_path: str,
        widget_path: str,
        bold_widget_path: Optional[str],
        css_style: bool,
    ) -> Optional[IgnoreMap]:
        global _LAST_DOC_HASH

        md_raw = _read_file(markdown_path)
        if md_raw == "" and not os.path.exists(markdown_path):
            safe_notify(
                "md_missing:" + markdown_path,
                f"Markdown note file not found:\n{markdown_path}",
            )
            return None

        md_norm = _normalize_md(md_raw)
        doc = _md_to_doc(md_norm)
        h = _doc_hash(doc)

        ignore: IgnoreMap = {}

        # If semantic doc didn't change, still enforce css marker rules toggle
        if _LAST_DOC_HASH == h:
            _ensure_widget_checkbox_css(widget_path, css_style, ignore)
            self._sync_bold_mirror_from_doc(doc, bold_widget_path, ignore)
            return ignore or None

        _LAST_DOC_HASH = h

        html_new = _doc_to_plasma_html(doc, css_style=css_style)
        if _write_if_changed(widget_path, html_new):
            _inc_ignore(ignore, widget_path, _IGNORE_BURST)

        # also update bold mirror FROM markdown immediately
        self._sync_bold_mirror_from_doc(doc, bold_widget_path, ignore)

        if ignore:
            logger.info(
                "Sync todo.md (**bold**) -> MAIN Plasma"
                + (" + BOLD mirror" if bold_widget_path else "")
            )
        return ignore or None

    def _from_main_plasma(
        self,
        widget_path: str,
        markdown_path: str,
        bold_widget_path: Optional[str],
        css_style: bool,
        html_path: str,
    ) -> Optional[IgnoreMap]:
        global _LAST_DOC_HASH

        if not os.path.exists(html_path):
            return None

        ignore: IgnoreMap = {}

        # Enforce checkbox marker CSS toggle on the widget file itself (style block only).
        _ensure_widget_checkbox_css(widget_path, css_style, ignore)

        html_raw = _read_file(html_path)
        doc = _html_to_doc(html_raw)
        h = _doc_hash(doc)

        # update markdown only if semantic doc changed
        if _LAST_DOC_HASH != h:
            _LAST_DOC_HASH = h
            md_out = _doc_to_md(doc)
            if _write_if_changed(markdown_path, md_out):
                _inc_ignore(ignore, markdown_path, _IGNORE_BURST)

        # always keep mirror aligned with MAIN
        self._sync_bold_mirror_from_doc(doc, bold_widget_path, ignore)

        if ignore:
            logger.info(
                "Sync MAIN Plasma -> todo.md (with **bold**)"
                + (" + BOLD mirror" if bold_widget_path else "")
            )
        return ignore or None

    def _from_bold_mirror(
        self,
        widget_path: str,
        markdown_path: str,
        bold_widget_path: Optional[str],
        css_style: bool,
    ) -> Optional[IgnoreMap]:
        """
        Optional: editing mirror updates MAIN bold lines.
        Mirror contains one line per bold-line in MAIN (line-safe mapping).
        """
        if not bold_widget_path or not os.path.exists(bold_widget_path):
            return None

        global _LAST_BOLD_ITEMS_HASH, _LAST_DOC_HASH

        mirror_html = _read_file(bold_widget_path)
        items = _mirror_html_to_items(mirror_html)
        items_h = _items_hash(items)
        if _LAST_BOLD_ITEMS_HASH == items_h:
            return None
        _LAST_BOLD_ITEMS_HASH = items_h

        main_html = _read_file(widget_path)
        main_doc = _html_to_doc(main_html)

        new_doc = _apply_mirror_items_to_doc(main_doc, items)
        new_h = _doc_hash(new_doc)

        ignore: IgnoreMap = {}

        if _LAST_DOC_HASH != new_h:
            _LAST_DOC_HASH = new_h

            # write MAIN
            new_main_html = _doc_to_plasma_html(new_doc, css_style=css_style)
            if _write_if_changed(widget_path, new_main_html):
                _inc_ignore(ignore, widget_path, _IGNORE_BURST)

            # write MD
            new_md = _doc_to_md(new_doc)
            if _write_if_changed(markdown_path, new_md):
                _inc_ignore(ignore, markdown_path, _IGNORE_BURST)

        # normalize mirror itself
        norm_mirror = _bold_items_to_plasma_html(items)
        if _write_if_changed(bold_widget_path, norm_mirror):
            _inc_ignore(ignore, bold_widget_path, _IGNORE_BURST)

        # Also ensure checkbox marker CSS toggle is applied if only config changed.
        _ensure_widget_checkbox_css(widget_path, css_style, ignore)

        if ignore:
            logger.info("Sync BOLD mirror -> MAIN -> todo.md")
        return ignore or None
