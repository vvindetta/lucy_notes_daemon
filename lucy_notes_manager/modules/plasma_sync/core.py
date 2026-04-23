from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Tuple

# ---------------- Hashing / Normalization ---------------- #


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _trim_trailing_spaces_per_line(text: str) -> str:
    return "\n".join([line.rstrip() for line in _normalize_newlines(text).split("\n")])


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
    return "".join(text for text, _is_bold in segs)


def _segs_has_bold(segs: List[Tuple[str, bool]]) -> bool:
    return any(is_bold for _text, is_bold in segs)


def _merge_segs(segs: List[Tuple[str, bool]]) -> List[Tuple[str, bool]]:
    out: List[Tuple[str, bool]] = []
    for text, is_bold in segs:
        if not text:
            continue
        if out and out[-1][1] == is_bold:
            out[-1] = (out[-1][0] + text, is_bold)
        else:
            out.append((text, is_bold))
    return out


# ---------------- Mirror de-duplication ---------------- #


def _dedupe_consecutive(items: List[str]) -> List[str]:
    """
    Plasma/QTextDocument sometimes keeps duplicated <p> blocks that may be rendered
    as a single visible line. If we apply mirror->main mapping without de-duping,
    duplicates spam MAIN with repeated identical lines.

    Rule: remove empty strings and consecutive duplicates after normalize+strip.
    """
    out: List[str] = []
    prev: Optional[str] = None

    for raw in items:
        normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            continue
        if prev is not None and normalized == prev:
            continue
        out.append(normalized)
        prev = normalized

    return out


# ---------------- Markdown (**bold**) parsing ---------------- #


def _find_unescaped_double_stars(line: str) -> List[int]:
    positions: List[int] = []
    index = 0
    while index < len(line) - 1:
        if line[index] == "\\":
            index += 2
            continue
        if line[index : index + 2] == "**":
            positions.append(index)
            index += 2
            continue
        index += 1
    # if odd, last one is treated as literal
    if len(positions) % 2 == 1:
        positions = positions[:-1]
    return positions


def _md_line_to_segs(line: str) -> List[Tuple[str, bool]]:
    line = _normalize_newlines(line)
    stars = _find_unescaped_double_stars(line)
    if not stars:
        return [(line.replace("\\*", "*").replace("\\\\", "\\"), False)]

    cut = set(stars)
    segs: List[Tuple[str, bool]] = []
    buf: List[str] = []
    bold = False
    index = 0

    while index < len(line):
        if index in cut and line[index : index + 2] == "**":
            txt = "".join(buf)
            if txt:
                txt = txt.replace("\\*", "*").replace("\\\\", "\\")
                segs.append((txt, bold))
            buf = []
            bold = not bold
            index += 2
            continue

        if line[index] == "\\" and index + 1 < len(line):
            # keep escaped char literally
            buf.append(line[index + 1])
            index += 2
            continue

        buf.append(line[index])
        index += 1

    txt = "".join(buf)
    if txt:
        segs.append((txt, bold))

    return _merge_segs(segs)


def _escape_md_text(text: str) -> str:
    # escape backslash first, then asterisk
    text = text.replace("\\", "\\\\")
    text = text.replace("*", "\\*")
    return text


def _segs_to_md(segs: List[Tuple[str, bool]]) -> str:
    out: List[str] = []
    for text, is_bold in segs:
        safe = _escape_md_text(text)
        if is_bold and safe:
            out.append(f"**{safe}**")
        else:
            out.append(safe)
    return "".join(out)


def _md_to_doc(md_text: str) -> List[DocLine]:
    md_text = _normalize_newlines(md_text)
    lines: List[DocLine] = []
    for raw in md_text.split("\n"):
        line = raw.rstrip("\n")
        if line.strip() == "":
            lines.append(DocLine(kind="p", state=None, segs=[]))
            continue

        low = line.lstrip()

        # checkbox list item
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
        segs = _md_line_to_segs(line)
        lines.append(DocLine(kind="p", state=None, segs=segs))

    # trim leading/trailing empty paragraphs
    while lines and lines[0].kind == "p" and _segs_plain(lines[0].segs).strip() == "":
        lines.pop(0)
    while lines and lines[-1].kind == "p" and _segs_plain(lines[-1].segs).strip() == "":
        lines.pop()

    return lines


def _doc_to_md(doc: List[DocLine]) -> str:
    """
    Important: in PLAIN widget mode, list-like text stays as text in paragraphs.
    So we only prepend "- [ ] / - [x]" when kind == "li".
    """
    out_lines: List[str] = []
    for dl in doc:
        if dl.kind == "p":
            out_lines.append(_segs_to_md(dl.segs) if dl.segs else "")
            continue

        prefix = "- "
        if dl.state == "unchecked":
            prefix = "- [ ] "
        elif dl.state == "checked":
            prefix = "- [x] "
        out_lines.append(prefix + _segs_to_md(dl.segs))

    return _normalize_md("\n".join(out_lines))


def _doc_hash(doc: List[DocLine]) -> str:
    return _hash_text(_doc_to_md(doc))


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
                was_bold = self._span_bold_stack.pop()
                if was_bold:
                    self._bold_depth = max(0, self._bold_depth - 1)
            return

        if tag == "font":
            if self._font_bold_stack:
                was_bold = self._font_bold_stack.pop()
                if was_bold:
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

        doc = self._doc[:]
        while doc and doc[0].kind == "p" and _segs_plain(doc[0].segs).strip() == "":
            doc.pop(0)
        while doc and doc[-1].kind == "p" and _segs_plain(doc[-1].segs).strip() == "":
            doc.pop()
        return doc


def _html_to_doc(html_src: str) -> List[DocLine]:
    parser = _PlasmaDocParser()
    parser.feed(html_src)
    return parser.get_doc()


# ---------------- Plasma HTML generation (from doc) ---------------- #


def _doc_to_plasma_html(doc: List[DocLine], css_style: bool = False) -> str:
    """
    css_style=True  -> real UL/LI + CSS marker checkbox glyphs (☐/☒).
    css_style=False -> NO UL/LI. Everything is rendered as plain <p> lines:
                       "- something", "- [ ] something", "- [x] something".
                       This guarantees: no ☒ ☐ and no list bullets.
    """
    header = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" '
        '"http://www.w3.org/TR/REC-html40/strict.dtd">\n'
        + '<html><head><meta name="qrichtext" content="1" />'
        + '<meta charset="utf-8" />'
        + '<style type="text/css">\n'
        + "p, li { white-space: pre-wrap; }\n"
        + "hr { height: 1px; border-width: 0; }\n"
        + (
            'li.unchecked::marker { content: "\\2610"; }\n'
            'li.checked::marker { content: "\\2612"; }\n'
            if css_style
            else ""
        )
        + "</style></head>"
        + "<body style=\" font-family:'Noto Sans'; font-size:10pt; "
        + 'font-weight:400; font-style:normal;">\n'
    )

    base_style = (
        " margin-top:0px; margin-bottom:0px; margin-left:0px; "
        "margin-right:0px; -qt-block-indent:0; text-indent:0px;"
    )

    def segs_to_inner(segs: List[Tuple[str, bool]]) -> str:
        inner: List[str] = []
        for text, is_bold in _merge_segs(segs):
            safe_text = html.escape(text, quote=False)
            inner.append(
                f'<span style=" font-weight:700;">{safe_text}</span>'
                if is_bold
                else safe_text
            )
        return "".join(inner)

    parts: List[str] = []

    if not css_style:
        # Plain mode: render list items as text lines, keep "- / - [ ] / - [x]" literally.
        for dl in doc:
            if dl.kind == "li":
                if dl.state == "unchecked":
                    prefix = "- [ ] "
                elif dl.state == "checked":
                    prefix = "- [x] "
                else:
                    prefix = "- "
                inner = html.escape(prefix, quote=False) + segs_to_inner(dl.segs)
                parts.append(f'<p style="{base_style}">{inner}</p>\n')
                continue

            # paragraph
            if _segs_plain(dl.segs).strip() == "":
                parts.append(
                    f'<p style="-qt-paragraph-type:empty;{base_style}"><br /></p>\n'
                )
            else:
                inner = segs_to_inner(dl.segs)
                parts.append(f'<p style="{base_style}">{inner}</p>\n')

        return header + "".join(parts) + "</body></html>\n"

    # CSS mode: real list structure + checkbox marker CSS
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
            parts.append(f'<li{cls}><p style="{base_style}">{inner}</p></li>\n')
            continue

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
    """
    Mirror rule:
    - DO NOT cut "-", "- [ ]", "- [x]".
    - Mirror shows the visible bold text as-is.

    How it works:
    - For paragraph lines: take bold fragments.
    - For list-item lines: also take bold fragments (without adding/removing prefixes).
      (If you made the whole line bold in plain mode, prefix is inside the text already,
       because it was edited as text inside <p>, so it will appear in mirror.)
    """
    items: List[str] = []
    for dl in doc:
        bold_fragments = [text for (text, is_bold) in dl.segs if is_bold and text]
        joined = "".join(bold_fragments).strip()
        if joined:
            items.append(joined)

    # Prevent MAIN->mirror from preserving hidden duplicated lines
    return _dedupe_consecutive(items)


def _items_hash(items: List[str]) -> str:
    norm = [it.replace("\r\n", "\n").replace("\r", "\n").strip() for it in items]
    norm = [it for it in norm if it]
    return _hash_text("\n".join(norm))


def _bold_items_to_plasma_html(items: List[str]) -> str:
    doc = [
        DocLine(kind="p", state=None, segs=[(it.strip(), True)])
        for it in items
        if it.strip()
    ]
    # mirror always plain (no checkbox glyphs)
    return _doc_to_plasma_html(doc, css_style=False)


def _mirror_html_to_items(mirror_html: str) -> List[str]:
    doc = _html_to_doc(mirror_html)
    items: List[str] = []
    for dl in doc:
        s = _segs_plain(dl.segs).strip()
        if s:
            items.append(s)

    # IMPORTANT: prevent hidden QTextDocument duplicates from spamming MAIN
    return _dedupe_consecutive(items)


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
    cleaned = _dedupe_consecutive(cleaned)

    out: List[DocLine] = []
    index = 0

    for dl in main_doc:
        if not _segs_has_bold(dl.segs):
            out.append(dl)
            continue

        if index < len(cleaned):
            out.append(
                DocLine(kind=dl.kind, state=dl.state, segs=[(cleaned[index], True)])
            )
            index += 1
        else:
            out.append(dl)

    # collect all existing bold lines after replacement
    existing_bold_lines = {
        _segs_plain(dl.segs).strip()
        for dl in out
        if _segs_has_bold(dl.segs) and _segs_plain(dl.segs).strip()
    }

    # append only truly new items
    while index < len(cleaned):
        candidate = cleaned[index].strip()
        if candidate and candidate not in existing_bold_lines:
            out.append(DocLine(kind="p", state=None, segs=[(candidate, True)]))
            existing_bold_lines.add(candidate)
        index += 1

    return out


