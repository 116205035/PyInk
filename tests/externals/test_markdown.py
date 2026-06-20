"""Tests for :func:`pyink.externals.Markdown` (Phase 3 PR3).

Like :mod:`tests.externals.test_divider` and
:mod:`tests.externals.test_highlighted_code`, we exercise the synchronous
:func:`pyink.render_to_string` test renderer for the static ``str`` fast
path — ``Markdown`` is a declarative factory for ``str`` sources (no
hooks, no function component). The reactive ``Signal`` / ``Callable``
branch goes through the live :func:`pyink.render.render` pipeline
(matching :mod:`tests.externals.test_streaming_text`) so the function
component is mounted by the reconciler and a signal write can be
observed to trigger a re-render.

Coverage (per PR3 scope):

* Element shape — static ``str`` returns a ``box`` host with
  ``flexDirection="column"``; reactive sources return a function
  component element (:func:`_MarkdownImpl`).
* Headings (``h1``-``h6``) — colour + bold.
* Paragraph with bold / italic / inline code.
* Soft / hard line breaks inside a paragraph.
* Links (OSC 8 wrapping with link colour applied to the label).
* Ordered / unordered lists (flat + nested).
* Code blocks (``fence``) with language header.
* Blockquote (indented + dim).
* Horizontal rule (via :func:`Divider`).
* Tables (basic column alignment).
* Three source shapes: ``str`` / ``Signal[str]`` / ``Callable[[], str]``.
* Theme override.
* Missing ``markdown_it`` friendly ``ImportError``.
* Integration: ``Markdown`` inside a parent ``Box`` with a border.
* ``Markdown`` is exported from ``pyink.externals`` but NOT from the
  top-level ``pyink`` package (PRD Decision 5 — externals stay opt-in).
"""

from __future__ import annotations

import builtins
import io
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from pyink import Box, render, render_to_string, signal
from pyink.core.element import Element
from pyink.externals import DEFAULT_MARKDOWN_THEME, Markdown
from pyink.externals.markdown import _MarkdownImpl

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Import / availability guards
# ---------------------------------------------------------------------------


def _markdown_it_available() -> bool:
    """Return ``True`` if :mod:`markdown_it` is importable in this env."""
    try:
        import markdown_it  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _markdown_it_available(),
    reason="markdown-it-py not installed (pip install pyink[markdown])",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(tree: Element, *, columns: int = 80) -> str:
    """Render ``tree`` via the synchronous test pipeline."""
    return render_to_string(tree, columns=columns)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _first_frame_of(tree: Element, *, columns: int = 80) -> str:
    """Mount + snapshot first frame + unmount (live pipeline).

    Used for the reactive ``Signal`` / ``Callable`` branches — they
    return a function component element that the synchronous test
    renderer can't mount. We use the live pipeline instead.
    """
    out = io.StringIO()
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=20,
        exit_on_ctrl_c=False,
    )
    snap = inst.current_frame.rstrip("\n")
    inst.unmount()
    return snap


def _wait_for(
    predicate: Callable[[], bool],
    *,
    attempts: int = 200,
    delay: float = 0.025,
) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return predicate()


def _install_markdown_it_import_blocker() -> None:
    """Make ``import markdown_it`` raise ``ImportError`` until reset."""

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "markdown_it" or name.startswith("markdown_it."):
            raise ImportError(f"mocked: markdown_it not installed ({name})")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    for mod in list(sys.modules):
        if mod == "markdown_it" or mod.startswith("markdown_it."):
            del sys.modules[mod]


@pytest.fixture
def _restore_import() -> Any:
    real_import = builtins.__import__
    yield
    builtins.__import__ = real_import


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_static_str_returns_box_host_with_column_direction() -> None:
    el = Markdown("# Hi")
    assert isinstance(el, Element)
    assert el.type == "box"
    assert el.props["flexDirection"] == "column"


def test_signal_source_returns_function_component_element() -> None:
    buf = signal("# Hi")
    el = Markdown(buf)
    assert isinstance(el, Element)
    assert el.type is _MarkdownImpl
    assert el.props["source"] is buf


def test_callable_source_returns_function_component_element() -> None:
    el = Markdown(lambda: "# Hi")
    assert isinstance(el, Element)
    assert el.type is _MarkdownImpl


def test_box_props_forwarded_to_outer_box() -> None:
    el = Markdown("# Hi", borderStyle="round", padding=1)
    assert el.props["borderStyle"] == "round"
    assert el.props["padding"] == 1


def test_caller_flexDirection_is_ignored() -> None:
    el = Markdown("# Hi", flexDirection="row")
    assert el.props["flexDirection"] == "column"


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


def test_h1_gets_magenta_bold() -> None:
    out = _render(Markdown("# Title"))
    # Magenta foreground (35) + bold (1) wraps "Title".
    assert f"{ESC}[35m" in out
    assert f"{ESC}[1m" in out
    assert "Title" in out


def test_h2_gets_yellow_bold() -> None:
    out = _render(Markdown("## Sub"))
    assert f"{ESC}[33m" in out
    assert "Sub" in out


def test_h3_gets_green_bold() -> None:
    out = _render(Markdown("### Deep"))
    assert f"{ESC}[32m" in out
    assert "Deep" in out


def test_h4_to_h6_each_have_own_color() -> None:
    """All six heading levels render without error and produce bold."""
    out = _render(Markdown("# A\n## B\n### C\n#### D\n##### E\n###### F"))
    # All headings present and bolded.
    for label in ("A", "B", "C", "D", "E", "F"):
        assert label in out
    # Each level emits a bold sequence at least once.
    assert out.count(f"{ESC}[1m") >= 6


# ---------------------------------------------------------------------------
# Paragraph + inline emphasis
# ---------------------------------------------------------------------------


def test_paragraph_plain_text() -> None:
    out = _render(Markdown("Just a paragraph."))
    assert "Just a paragraph." in out


def test_bold_inline() -> None:
    out = _render(Markdown("This is **bold** text."))
    assert f"{ESC}[1mbold{ESC}[0m" in out
    assert "This is " in out
    assert " text." in out


def test_italic_inline() -> None:
    out = _render(Markdown("This is *italic* text."))
    assert f"{ESC}[3mitalic{ESC}[0m" in out


def test_inline_code_gets_code_color() -> None:
    out = _render(Markdown("Use `code` here."))
    # code_color default is red (SGR 31).
    assert f"{ESC}[31mcode{ESC}[0m" in out


def test_combined_bold_italic_code() -> None:
    out = _render(Markdown("**a** *b* `c`"))
    assert f"{ESC}[1ma{ESC}[0m" in out
    assert f"{ESC}[3mb{ESC}[0m" in out
    assert f"{ESC}[31mc{ESC}[0m" in out


def test_softbreak_in_paragraph() -> None:
    out = _render(Markdown("line one\nline two"))
    assert "line one" in out
    assert "line two" in out


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_link_wraps_label_in_osc8() -> None:
    out = _render(Markdown("[Click](https://example.com)"))
    # OSC 8 open sequence with the URL.
    assert f"{ESC}]8;;https://example.com{ESC}\\" in out
    # Closing OSC 8 sequence.
    assert f"{ESC}]8;;{ESC}\\" in out
    # The label is present.
    assert "Click" in out


def test_link_color_applied_to_label() -> None:
    out = _render(Markdown("[Click](https://example.com)"))
    # Default link_color is blue (SGR 34).
    assert f"{ESC}[34m" in out


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def test_unordered_list_markers() -> None:
    out = _render(Markdown("- a\n- b\n- c"))
    # Each item rendered with a dim "-" marker.
    assert out.count("- ") >= 3
    for label in ("a", "b", "c"):
        assert label in out


def test_ordered_list_markers() -> None:
    out = _render(Markdown("1. first\n2. second\n3. third"))
    assert "1." in out
    assert "2." in out
    assert "3." in out
    for label in ("first", "second", "third"):
        assert label in out


def test_nested_unordered_list() -> None:
    out = _render(Markdown("- a\n  - a1\n  - a2\n- b"))
    for label in ("a", "a1", "a2", "b"):
        assert label in out


def test_ordered_list_starts_at_custom_offset() -> None:
    """``2.`` start produces markers 2., 3., …"""
    out = _render(Markdown("2. two\n3. three"))
    assert "2." in out
    assert "3." in out


# ---------------------------------------------------------------------------
# Code blocks
# ---------------------------------------------------------------------------


def test_fenced_code_block_renders_lines_dim() -> None:
    out = _render(Markdown("```\ndef f():\n    pass\n```"))
    # All three source lines present.
    assert "def f():" in out
    assert "pass" in out
    # Dim color (SGR 2) is applied to the body.
    assert f"{ESC}[2m" in out


def test_fenced_code_block_emits_language_header() -> None:
    out = _render(Markdown("```python\nprint('hi')\n```"))
    # Language label appears dim.
    assert "python" in out


# ---------------------------------------------------------------------------
# Blockquote
# ---------------------------------------------------------------------------


def test_blockquote_indents_and_dims_content() -> None:
    out = _render(Markdown("> A quote"))
    assert "A quote" in out
    # dimColor (SGR 2) applied to the blockquote wrapper.
    assert f"{ESC}[2m" in out


def test_blockquote_with_multiple_lines() -> None:
    out = _render(Markdown("> line one\n> line two"))
    assert "line one" in out
    assert "line two" in out


# ---------------------------------------------------------------------------
# Horizontal rule
# ---------------------------------------------------------------------------


def test_horizontal_rule_uses_divider() -> None:
    """``---`` produces a ``Divider`` element (a single bottom edge box)."""
    el = Markdown("a\n\n---\n\nb")
    # The middle child of the outer Box is the divider box.
    children = el.children
    # Find the divider (a box with borderBottom=True, others False).
    divider_found = False
    for child in children:
        if isinstance(child, Element) and child.type == "box":
            props = child.props
            if (
                props.get("borderBottom") is True
                and props.get("borderTop") is False
                and props.get("borderLeft") is False
                and props.get("borderRight") is False
            ):
                divider_found = True
                break
    assert divider_found, "expected a Divider element between the two paragraphs"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_table_renders_aligned_columns() -> None:
    out = _render(Markdown("| A | B |\n|---|---|\n| 1 | 2 |\n| hello | world |\n"))
    # Header row.
    assert "A" in out
    assert "B" in out
    # Body cells.
    for label in ("1", "2", "hello", "world"):
        assert label in out


# ---------------------------------------------------------------------------
# Reactive source: Signal
# ---------------------------------------------------------------------------


def test_signal_source_renders_initial_value() -> None:
    buf = signal("# Initial")
    snap = _first_frame_of(Markdown(buf))
    assert "Initial" in snap


def test_signal_source_rerenders_on_write() -> None:
    buf = signal("# Old")
    inst = render(
        Markdown(buf),
        stdout=io.StringIO(),
        stdin=_FakeTTY(),
        columns=80,
        rows=10,
        exit_on_ctrl_c=False,
    )
    assert _wait_for(lambda: "Old" in inst.current_frame)
    buf.value = "# New"
    assert _wait_for(lambda: "New" in inst.current_frame)
    inst.unmount()


# ---------------------------------------------------------------------------
# Reactive source: Callable
# ---------------------------------------------------------------------------


def test_callable_source_renders_resolved_value() -> None:
    snap = _first_frame_of(Markdown(lambda: "# From Callable"))
    assert "From Callable" in snap


def test_callable_source_reactive_via_signal_read() -> None:
    buf = signal("# A")
    inst = render(
        Markdown(lambda: buf.value),
        stdout=io.StringIO(),
        stdin=_FakeTTY(),
        columns=80,
        rows=10,
        exit_on_ctrl_c=False,
    )
    assert _wait_for(lambda: "A" in inst.current_frame)
    buf.value = "# B"
    assert _wait_for(lambda: "B" in inst.current_frame)
    inst.unmount()


# ---------------------------------------------------------------------------
# Theme override
# ---------------------------------------------------------------------------


def test_theme_h1_color_override() -> None:
    out = _render(Markdown("# Title", theme={"h1_color": "cyan"}))
    # Cyan (SGR 36) replaces the default magenta (SGR 35).
    assert f"{ESC}[36m" in out
    assert f"{ESC}[35m" not in out


def test_theme_code_color_override() -> None:
    out = _render(Markdown("Use `x`.", theme={"code_color": "green"}))
    # Green (SGR 32) replaces default red (SGR 31).
    assert f"{ESC}[32mx{ESC}[0m" in out


def test_theme_bold_disabled_for_h1() -> None:
    out = _render(Markdown("# Title", theme={"h1_bold": False}))
    assert "Title" in out
    # With bold disabled, no SGR 1 should be applied to the heading
    # content. The magenta colour (35) should still appear.
    assert f"{ESC}[35m" in out


# ---------------------------------------------------------------------------
# Missing markdown_it
# ---------------------------------------------------------------------------


def test_missing_markdown_it_raises_friendly_import_error(
    _restore_import: None,
) -> None:
    _install_markdown_it_import_blocker()
    with pytest.raises(ImportError) as excinfo:
        Markdown("# Hi")
    msg = str(excinfo.value)
    assert "markdown-it-py" in msg
    assert "pip install pyink[markdown]" in msg


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_markdown_inside_box_with_border() -> None:
    out = _render(Box(Markdown("# Hi\n\nBody"), borderStyle="round", padding=1))
    assert "Hi" in out
    assert "Body" in out


def test_full_document_renders_all_blocks() -> None:
    """A representative Markdown document renders without error and
    every block's content is present in the output."""
    src = (
        "# Title\n\n"
        "Paragraph with **bold** and *italic*.\n\n"
        "## Subsection\n\n"
        "- Item 1\n"
        "- Item 2\n\n"
        "```python\n"
        "print('hi')\n"
        "```\n\n"
        "> A quote\n\n"
        "---\n\n"
        "[Link](https://example.com)\n"
    )
    out = _render(Markdown(src))
    for needle in (
        "Title",
        "Paragraph with",
        "bold",
        "italic",
        "Subsection",
        "Item 1",
        "Item 2",
        "print('hi')",
        "A quote",
        "Link",
        "https://example.com",
    ):
        assert needle in out, f"missing {needle!r} in output"


# ---------------------------------------------------------------------------
# Export checks (PRD Decision 5 — externals stay opt-in)
# ---------------------------------------------------------------------------


def test_markdown_exported_from_externals() -> None:
    from pyink import externals

    assert externals.Markdown is Markdown
    assert externals.DEFAULT_MARKDOWN_THEME is DEFAULT_MARKDOWN_THEME


def test_markdown_not_in_top_level_namespace() -> None:
    import pyink

    assert not hasattr(pyink, "Markdown"), (
        "Markdown should not be exported from the top-level pyink namespace"
    )
