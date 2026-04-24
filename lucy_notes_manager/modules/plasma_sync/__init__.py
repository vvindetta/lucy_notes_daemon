import logging
import os
from typing import Dict, List, Optional

from lucy_notes_manager.lib import safe_notify
from lucy_notes_manager.lib.path import canonical_path
from lucy_notes_manager.modules.abstract_module import AbstractModule, Context, System
from lucy_notes_manager.modules.plasma_sync.config import PLASMA_SYNC_TEMPLATE
from lucy_notes_manager.modules.plasma_sync.core import (
    DocLine,
    _apply_mirror_items_to_doc,
    _bold_items_to_plasma_html,
    _doc_hash,
    _doc_to_md,
    _doc_to_plasma_html,
    _extract_bold_items_from_doc,
    _hash_text,
    _html_to_doc,
    _items_hash,
    _md_to_doc,
    _mirror_html_to_items,
    _normalize_md,
)

logger = logging.getLogger(__name__)

IgnoreMap = Dict[str, int]

_IGNORE_BURST = 1


# ---------------- State ---------------- #

_INIT_DONE: bool = False

_LAST_DOC_HASH: Optional[str] = None  # canonical doc hash (content + bold + list state)
_LAST_BOLD_ITEMS_HASH: Optional[str] = None  # mirror items hash
_LAST_CSS_STYLE: Optional[bool] = None  # last applied --plasma-css-style state


# ---------------- IO ---------------- #


def _read_file(path: str) -> str:
    try:
        with open(canonical_path(path), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except PermissionError as error:
        logger.error("Permission error reading %s: %s", path, error)
        safe_notify(
            "read_perm:" + path, f"Permission denied reading:\n{path}\n\n{error}"
        )
        return ""
    except OSError as error:
        logger.error("OS error reading %s: %s", path, error)
        safe_notify("read_os:" + path, f"Failed to read file:\n{path}\n\n{error}")
        return ""


def _write_if_changed(path: str, content: str) -> bool:
    path = canonical_path(path)
    old = _read_file(path)
    if old == content:
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except PermissionError as error:
        logger.error("Permission error writing %s: %s", path, error)
        safe_notify(
            "write_perm:" + path, f"Permission denied writing:\n{path}\n\n{error}"
        )
        return False
    except OSError as error:
        logger.error("OS error writing %s: %s", path, error)
        safe_notify("write_os:" + path, f"Failed to write file:\n{path}\n\n{error}")
        return False


def _inc_ignore(ignore: IgnoreMap, path: str, times: int = 1) -> None:
    absolute_path = canonical_path(path)
    ignore[absolute_path] = ignore.get(absolute_path, 0) + int(times)

# ---------------- Startup init ---------------- #


def _init_from_disk_once(
    widget_path: str, markdown_path: str, bold_widget_path: Optional[str]
) -> None:
    global _INIT_DONE, _LAST_DOC_HASH, _LAST_BOLD_ITEMS_HASH, _LAST_CSS_STYLE
    if _INIT_DONE:
        return
    _INIT_DONE = True

    widget_path = canonical_path(widget_path)
    markdown_path = canonical_path(markdown_path)
    bold_widget_path = canonical_path(bold_widget_path) if bold_widget_path else None

    _LAST_CSS_STYLE = None  # unknown until first handle

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

    _LAST_DOC_HASH = _hash_text("")
    _LAST_BOLD_ITEMS_HASH = _hash_text("")


# ---------------- Enforce widget render mode on config toggle ---------------- #


def _ensure_widget_render_mode(
    widget_path: str, css_style: bool, ignore: IgnoreMap
) -> None:
    """
    If config flag changed, rewrite the widget HTML into:
      - css_style=True  -> real <ul>/<li> + marker CSS (☐/☒)
      - css_style=False -> plain <p> lines with literal "- / - [ ] / - [x]"
    """
    global _LAST_CSS_STYLE

    if _LAST_CSS_STYLE is not None and _LAST_CSS_STYLE == css_style:
        return

    html_raw = _read_file(widget_path)
    if not html_raw.strip():
        _LAST_CSS_STYLE = css_style
        return

    doc = _html_to_doc(html_raw)
    html_new = _doc_to_plasma_html(doc, css_style=css_style)

    if _write_if_changed(widget_path, html_new):
        _inc_ignore(ignore, widget_path, _IGNORE_BURST)

    _LAST_CSS_STYLE = css_style


# ---------------- Module ---------------- #


class PlasmaSync(AbstractModule):
    name: str = "plasma_sync"
    priority: int = 30

    template = PLASMA_SYNC_TEMPLATE

    def on_created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def on_modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def on_moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def on_deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def _cfg(self, ctx: Context) -> tuple[str, str, Optional[str], bool]:
        if not ctx.config["plasma_widget_path"] or not ctx.config[
            "plasma_widget_path"
        ].strip():
            raise ValueError("PlasmaSync: invalid value for --plasma-widget-path")
        if not ctx.config["plasma_markdown_note_path"] or not ctx.config[
            "plasma_markdown_note_path"
        ].strip():
            raise ValueError(
                "PlasmaSync: invalid value for --plasma-markdown-note-path"
            )

        if ctx.config["plasma_bold_widget_path"] is not None and not ctx.config[
            "plasma_bold_widget_path"
        ].strip():
            raise ValueError("PlasmaSync: invalid value for --plasma-bold-widget-path")

        return (
            canonical_path(ctx.config["plasma_widget_path"]),
            canonical_path(ctx.config["plasma_markdown_note_path"]),
            canonical_path(ctx.config["plasma_bold_widget_path"])
            if ctx.config["plasma_bold_widget_path"]
            else None,
            ctx.config["plasma_css_style"],
        )

    def _handle(self, ctx: Context) -> Optional[IgnoreMap]:
        widget_path, markdown_path, bold_widget_path, css_style = self._cfg(ctx)

        _init_from_disk_once(widget_path, markdown_path, bold_widget_path)

        path = canonical_path(ctx.path)
        widget_abs = canonical_path(widget_path)
        md_abs = canonical_path(markdown_path)
        bold_abs = canonical_path(bold_widget_path) if bold_widget_path else None

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
                widget_path, markdown_path, bold_widget_path, css_style, html_path=path
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

        # even if doc didn't change, config toggle must rewrite widget render mode
        if _LAST_DOC_HASH == h:
            _ensure_widget_render_mode(widget_path, css_style, ignore)
            self._sync_bold_mirror_from_doc(doc, bold_widget_path, ignore)
            return ignore or None

        _LAST_DOC_HASH = h

        html_new = _doc_to_plasma_html(doc, css_style=css_style)
        if _write_if_changed(widget_path, html_new):
            _inc_ignore(ignore, widget_path, _IGNORE_BURST)

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

        # config toggle enforcement (plain mode removes ☒/☐ by rewriting)
        _ensure_widget_render_mode(widget_path, css_style, ignore)

        html_raw = _read_file(html_path)
        doc = _html_to_doc(html_raw)
        h = _doc_hash(doc)

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
        items = _mirror_html_to_items(mirror_html)  # includes de-dupe
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

        # normalize mirror itself (also keeps it from accumulating hidden duplicates)
        norm_mirror = _bold_items_to_plasma_html(items)
        if _write_if_changed(bold_widget_path, norm_mirror):
            _inc_ignore(ignore, bold_widget_path, _IGNORE_BURST)

        # config-only toggle still must rewrite widget
        _ensure_widget_render_mode(widget_path, css_style, ignore)

        if ignore:
            logger.info("Sync BOLD mirror -> MAIN -> todo.md")
        return ignore or None
