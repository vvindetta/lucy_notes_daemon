from __future__ import annotations

from pathlib import Path

import pytest

import lucy_notes_manager.modules.plasma_sync as plasma_mod
from lucy_notes_manager.modules.abstract_module import Context
from lucy_notes_manager.modules.plasma_sync import DocLine, PlasmaSync


@pytest.fixture(autouse=True)
def _reset_plasma_globals(monkeypatch):
    monkeypatch.setattr(plasma_mod, "_INIT_DONE", False)
    monkeypatch.setattr(plasma_mod, "_LAST_DOC_HASH", None)
    monkeypatch.setattr(plasma_mod, "_LAST_BOLD_ITEMS_HASH", None)
    monkeypatch.setattr(plasma_mod, "_LAST_CSS_STYLE", None)


def _canonicalize_md(md_text: str) -> str:
    return plasma_mod._doc_to_md(plasma_mod._md_to_doc(plasma_mod._normalize_md(md_text)))


def test_md_doc_roundtrip_preserves_checkbox_and_bold():
    md = "- [ ] **Task**\nPlain line"
    doc = plasma_mod._md_to_doc(md)

    assert doc[0].kind == "li"
    assert doc[0].state == "unchecked"
    assert doc[1].kind == "p"
    assert plasma_mod._doc_to_md(doc) == md


def test_doc_to_plasma_html_mode_switch_changes_structure():
    doc = [DocLine(kind="li", state="unchecked", segs=[("Task", False)])]

    plain_html = plasma_mod._doc_to_plasma_html(doc, css_style=False)
    css_html = plasma_mod._doc_to_plasma_html(doc, css_style=True)

    assert "<ul>" not in plain_html
    assert "- [ ] Task" in plain_html
    assert "<ul>" in css_html
    assert "li.unchecked::marker" in css_html


def test_apply_mirror_items_to_doc_replaces_bold_lines_and_appends_new():
    main_doc = [
        DocLine(kind="p", state=None, segs=[("plain", False)]),
        DocLine(kind="p", state=None, segs=[("old1", True)]),
        DocLine(kind="li", state="checked", segs=[("old2", True)]),
    ]

    updated = plasma_mod._apply_mirror_items_to_doc(main_doc, ["new1", "new2", "new3"])
    rendered = plasma_mod._doc_to_md(updated)

    assert "plain" in rendered
    assert "**new1**" in rendered
    assert "- [x] **new2**" in rendered
    assert "**new3**" in rendered


def test_cfg_parses_paths_and_boolean_values(tmp_path: Path):
    widget = tmp_path / "widget.html"
    md = tmp_path / "note.md"
    mirror = tmp_path / "mirror.html"

    ctx = Context(
        path=str(md),
        config={
            "plasma_widget_path": str(widget),
            "plasma_markdown_note_path": str(md),
            "plasma_bold_widget_path": str(mirror),
            "plasma_css_style": True,
        },
        arg_lines={},
    )

    module = PlasmaSync()
    widget_path, md_path, mirror_path, css_style = module._cfg(ctx)
    assert widget_path == str(widget.resolve())
    assert md_path == str(md.resolve())
    assert mirror_path == str(mirror.resolve())
    assert css_style is True


def test_from_markdown_writes_widget_and_mirror(tmp_path: Path):
    md = tmp_path / "todo.md"
    widget = tmp_path / "widget.html"
    mirror = tmp_path / "mirror.html"
    md.write_text("Line\n**Bold**\n", encoding="utf-8")

    module = PlasmaSync()
    ignore = module._from_markdown(
        markdown_path=str(md),
        widget_path=str(widget),
        bold_widget_path=str(mirror),
        css_style=False,
    )

    assert ignore is not None
    assert str(widget.resolve()) in ignore
    assert str(mirror.resolve()) in ignore
    assert widget.exists()
    assert mirror.exists()


def test_from_main_plasma_updates_markdown(tmp_path: Path):
    widget = tmp_path / "widget.html"
    md = tmp_path / "todo.md"
    doc = [DocLine(kind="p", state=None, segs=[("Hello", True)])]
    widget.write_text(plasma_mod._doc_to_plasma_html(doc, css_style=False), encoding="utf-8")

    module = PlasmaSync()
    ignore = module._from_main_plasma(
        widget_path=str(widget),
        markdown_path=str(md),
        bold_widget_path=None,
        css_style=False,
        html_path=str(widget),
    )

    assert ignore is not None
    assert str(md.resolve()) in ignore
    assert md.read_text(encoding="utf-8") == "**Hello**"


@pytest.mark.parametrize(
    "source_md",
    [
        "- [ ] **Task A**\nline with **mid** bold and tail\n- [x] done",
        "a\\*b\\*c\n**bold**\n\n- [ ] item",
        "plain\n\n- [x] **Checked** and plain suffix\n- [ ] second",
        "**one** **two**\n- [ ] mix **x** y **z**",
    ],
)
def test_roundtrip_plain_mode_is_stable_after_many_cycles(source_md: str):
    expected = _canonicalize_md(source_md)
    current = source_md

    for _ in range(40):
        doc_from_md = plasma_mod._md_to_doc(plasma_mod._normalize_md(current))
        html_plain = plasma_mod._doc_to_plasma_html(doc_from_md, css_style=False)
        doc_from_html = plasma_mod._html_to_doc(html_plain)
        current = plasma_mod._doc_to_md(doc_from_html)

    assert current == expected

    # One extra cycle should keep exactly the same canonical text.
    doc = plasma_mod._md_to_doc(plasma_mod._normalize_md(current))
    html_plain = plasma_mod._doc_to_plasma_html(doc, css_style=False)
    after = plasma_mod._doc_to_md(plasma_mod._html_to_doc(html_plain))
    assert after == current


@pytest.mark.parametrize(
    "source_md",
    [
        "- [ ] first\n- [x] **second**",
        "para **bold** text\n\n- [ ] list",
        "**A**\n**B**\n- [ ] **C**",
    ],
)
def test_roundtrip_css_mode_is_stable_after_many_cycles(source_md: str):
    expected = _canonicalize_md(source_md)
    current = source_md

    for _ in range(35):
        doc_from_md = plasma_mod._md_to_doc(plasma_mod._normalize_md(current))
        html_css = plasma_mod._doc_to_plasma_html(doc_from_md, css_style=True)
        doc_from_html = plasma_mod._html_to_doc(html_css)
        current = plasma_mod._doc_to_md(doc_from_html)

    assert current == expected

    doc = plasma_mod._md_to_doc(plasma_mod._normalize_md(current))
    html_css = plasma_mod._doc_to_plasma_html(doc, css_style=True)
    after = plasma_mod._doc_to_md(plasma_mod._html_to_doc(html_css))
    assert after == current


def test_sync_ring_many_texts_keeps_final_state_deterministic(tmp_path: Path):
    md_path = tmp_path / "todo.md"
    widget_path = tmp_path / "widget.html"
    mirror_path = tmp_path / "mirror.html"
    module = PlasmaSync()

    texts = [
        "- [ ] **Task 1**\nline\n- [x] done",
        "plain **bold** text\n\n- [ ] item A\n- [ ] item B",
        "**Header**\nparagraph\n- [x] **Finish**",
    ]

    last_expected_md = ""
    for _ in range(4):
        for text in texts:
            md_path.write_text(text, encoding="utf-8")
            last_expected_md = _canonicalize_md(text)

            module._from_markdown(
                markdown_path=str(md_path),
                widget_path=str(widget_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
            )
            module._from_main_plasma(
                widget_path=str(widget_path),
                markdown_path=str(md_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
                html_path=str(widget_path),
            )
            module._from_bold_mirror(
                widget_path=str(widget_path),
                markdown_path=str(md_path),
                bold_widget_path=str(mirror_path),
                css_style=False,
            )

            current_md = md_path.read_text(encoding="utf-8")
            assert current_md == last_expected_md

            widget_doc = plasma_mod._html_to_doc(widget_path.read_text(encoding="utf-8"))
            expected_items = plasma_mod._extract_bold_items_from_doc(widget_doc)
            mirror_items = plasma_mod._mirror_html_to_items(
                mirror_path.read_text(encoding="utf-8")
            )
            assert mirror_items == expected_items

    final_md = md_path.read_text(encoding="utf-8")
    final_widget = widget_path.read_text(encoding="utf-8")
    final_mirror = mirror_path.read_text(encoding="utf-8")

    # Final idempotence check: one more full ring must not change outputs.
    module._from_markdown(
        markdown_path=str(md_path),
        widget_path=str(widget_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
    )
    module._from_main_plasma(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
        html_path=str(widget_path),
    )
    module._from_bold_mirror(
        widget_path=str(widget_path),
        markdown_path=str(md_path),
        bold_widget_path=str(mirror_path),
        css_style=False,
    )

    assert md_path.read_text(encoding="utf-8") == final_md == last_expected_md
    assert widget_path.read_text(encoding="utf-8") == final_widget
    assert mirror_path.read_text(encoding="utf-8") == final_mirror
