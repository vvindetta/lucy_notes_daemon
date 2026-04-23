from __future__ import annotations

from lucy_notes_manager.lib.args import Template

PLASMA_SYNC_TEMPLATE: Template = [
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
        "If True: use CSS checkbox markers (☐/☒) via li.*::marker and real UL/LI. "
        "If False (default): render plain text only (no glyphs, no bullets).",
    ),
]
