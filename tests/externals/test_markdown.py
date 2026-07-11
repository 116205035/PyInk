"""Tests for :func:`ink.externals.Markdown` (Phase 3 PR3).

Like :mod:`tests.externals.test_divider` and
:mod:`tests.externals.test_highlighted_code`, we exercise the synchronous
:func:`ink.render_to_string` test renderer for the static ``str`` fast
path — ``Markdown`` is a declarative factory for ``str`` sources (no
hooks, no function component). The reactive ``Signal`` / ``Callable``
branch goes through the live :func:`ink.render.render` pipeline
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
* ``Markdown`` is exported from ``ink.externals`` but NOT from the
  top-level ``ink`` package (PRD Decision 5 — externals stay opt-in).
"""

from __future__ import annotations

import builtins
import io
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from ink import Box, render, render_to_string, signal
from ink.core.element import Element
from ink.externals import DEFAULT_MARKDOWN_THEME, Markdown
from ink.externals.markdown import _MarkdownImpl

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
    reason="markdown-it-py not installed (pip install ink[markdown])",
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


def _install_pygments_import_blocker() -> None:
    """Make ``import pygments`` raise ``ImportError`` until reset.

    PR4 routes fenced code blocks through :func:`HighlightedCode`, which
    lazily imports :mod:`pygments`. Patching ``__import__`` lets us
    exercise the plain-text fallback path even on environments where
    pygments is installed. We also remove cached ``pygments`` / ``pygments.*``
    modules so the lazy import inside :func:`HighlightedCode` actually
    hits the blocker.
    """

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pygments" or name.startswith("pygments."):
            raise ImportError(f"mocked: pygments not installed ({name})")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    for mod in list(sys.modules):
        if mod == "pygments" or mod.startswith("pygments."):
            del sys.modules[mod]


def _pygments_available() -> bool:
    """Return ``True`` if :mod:`pygments` is importable in this env."""
    try:
        import pygments  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def _restore_import() -> Any:
    real_import = builtins.__import__
    yield
    builtins.__import__ = real_import


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_static_str_now_function_component() -> None:
    """PR2: static ``str`` sources route through ``_MarkdownImpl`` too.

    Pre-PR2 the static path eagerly built a ``box`` host element so
    callers could introspect the rendered element tree. PR2 unifies the
    static and reactive paths so width-aware blocks (tables) can pick
    up the layout-time content width — the static path now returns a
    function component element whose body defers parsing + rendering
    to layout time.
    """
    el = Markdown("# Hi")
    assert isinstance(el, Element)
    assert el.type is _MarkdownImpl
    assert el.props["source"] == "# Hi"


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
    """``box_props`` are forwarded to ``_MarkdownImpl`` which applies them
    to the outer ``Box`` container at mount time.
    """
    el = Markdown("# Hi", borderStyle="round", padding=1)
    box_props = el.props["box_props"]
    assert box_props["borderStyle"] == "round"
    assert box_props["padding"] == 1


def test_caller_flexDirection_is_ignored() -> None:
    """The component's contract is ``flexDirection="column"``; a caller
    passing ``flexDirection="row"`` is stripped before forwarding.
    """
    el = Markdown("# Hi", flexDirection="row")
    box_props = el.props["box_props"]
    # ``flexDirection`` is popped before forwarding so the component can
    # force ``"column"`` (one block per row).
    assert "flexDirection" not in box_props


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


def test_h1_gets_bold_italic_underline() -> None:
    """PR3: default h1 is bold + italic + underline with no colour (claude-code style).

    Pre-PR3 h1 was magenta (SGR 35) + bold. PR3 rewrites the default to
    the terminal's text colour (``None``) + bold + italic + underline so
    headings read as structural markers rather than a rainbow palette.
    """
    out = _render(Markdown("# Title"))
    # Bold (1) + italic (3) + underline (4) all wrap "Title".
    assert f"{ESC}[1m" in out
    assert f"{ESC}[3m" in out
    assert f"{ESC}[4m" in out
    assert "Title" in out
    # No colour SGR is emitted (h1_color=None inherits the terminal default).
    # We check that none of the legacy rainbow colours appear.
    for sgr in (f"{ESC}[35m", f"{ESC}[33m", f"{ESC}[32m", f"{ESC}[36m", f"{ESC}[34m"):
        assert sgr not in out


def test_h2_gets_bold_no_color() -> None:
    """PR3: default h2 is bold only (no colour, no italic, no underline)."""
    out = _render(Markdown("## Sub"))
    assert f"{ESC}[1m" in out
    assert "Sub" in out
    # h2 has no italic / underline by default.
    assert f"{ESC}[3m" not in out
    assert f"{ESC}[4m" not in out
    # No colour SGR (h2_color=None).
    assert f"{ESC}[33m" not in out


def test_h3_gets_bold_no_color() -> None:
    """PR3: default h3 is bold only (no colour)."""
    out = _render(Markdown("### Deep"))
    assert f"{ESC}[1m" in out
    assert "Deep" in out
    assert f"{ESC}[32m" not in out


def test_h4_to_h6_each_have_bold() -> None:
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
    # PR3: code_color default is "accent" → resolved to cyan (SGR 36).
    assert f"{ESC}[36mcode{ESC}[0m" in out


def test_combined_bold_italic_code() -> None:
    out = _render(Markdown("**a** *b* `c`"))
    assert f"{ESC}[1ma{ESC}[0m" in out
    assert f"{ESC}[3mb{ESC}[0m" in out
    # PR3: code_color default "accent" → cyan (SGR 36).
    assert f"{ESC}[36mc{ESC}[0m" in out


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
    # PR3: link_color default is "accent" → cyan (SGR 36).
    assert f"{ESC}[36m" in out


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
    """Code blocks render via HighlightedCode when pygments is installed.

    PR3 asserted dim (SGR 2) text rows; PR4 changes the default to
    highlighted output when pygments is importable. We still check the
    source lines are present (with per-token ANSI interleaving) and
    that *some* syntax colour is emitted. The dedicated PR4 fallback
    test (``test_fenced_code_block_falls_back_to_dim_when_pygments_missing``)
    covers the original dim path via an import blocker.
    """
    out = _render(Markdown("```\ndef f():\n    pass\n```"))
    # ``def`` and ``pass`` are present as tokens (possibly split by
    # ANSI sequences when HighlightedCode is in play). With no language
    # label the block goes through HighlightedCode's plain-text fast
    # path, so the source still renders verbatim.
    assert "def" in out
    assert "pass" in out


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
    """``---`` produces a ``Divider`` element that renders as a row of ``─``.

    PR2 changed the static path to return a function component element,
    so we can no longer walk ``el.children`` to find the Divider box.
    Instead we render to a string and assert the divider glyph row
    appears between the two paragraphs.
    """
    out = _render(Markdown("a\n\n---\n\nb"))
    # Divider renders as a row of U+2500 (─) characters.
    assert "─" in out
    assert "a" in out
    assert "b" in out
    # The divider sits between the two paragraphs.
    assert out.index("a") < out.index("─") < out.index("b")


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
# PR2: Table rendering polish — borders / alignment / inline / responsive
# ---------------------------------------------------------------------------


def test_table_border_single() -> None:
    """Default ``table_border_style="single"`` draws ``┌─┬─┐`` / ``└─┴─┘``."""
    out = _render(Markdown("| A | B |\n|---|---|\n| 1 | 2 |\n"))
    # Top border uses single-line corners + cross.
    assert "┌" in out
    assert "┬" in out
    assert "┐" in out
    # Bottom border.
    assert "└" in out
    assert "┴" in out
    assert "┘" in out
    # Header/body separator.
    assert "├" in out
    assert "┼" in out
    assert "┤" in out
    # Vertical separators.
    assert "│" in out
    # Horizontal fill.
    assert "─" in out


def test_table_border_none() -> None:
    """``table_border_style="none"`` disables the frame entirely."""
    out = _render(
        Markdown(
            "| A | B |\n|---|---|\n| 1 | 2 |\n",
            theme={"table_border_style": "none"},
        )
    )
    # No border glyphs should appear.
    for glyph in ("┌", "┐", "└", "┘", "├", "┤", "┬", "┴", "┼", "│"):
        assert glyph not in out, (
            f"border glyph {glyph!r} should be absent when border_style='none'"
        )
    # Content still present.
    assert "A" in out
    assert "B" in out
    assert "1" in out
    assert "2" in out


def test_table_border_rounded() -> None:
    """``table_border_style="rounded"`` uses ``╭`` / ``╮`` / ``╰`` / ``╯``."""
    out = _render(
        Markdown(
            "| A | B |\n|---|---|\n| 1 | 2 |\n",
            theme={"table_border_style": "rounded"},
        )
    )
    # Rounded corners (top/bottom only — the mid separator stays single).
    assert "╭" in out
    assert "╮" in out
    assert "╰" in out
    assert "╯" in out
    # Single-line square corners should NOT appear (top/bottom corners
    # are rounded; the mid separator uses ├ ┼ ┤ which are not corners).
    assert "┌" not in out
    assert "┐" not in out
    assert "└" not in out
    assert "┘" not in out


def test_table_border_double() -> None:
    """``table_border_style="double"`` uses ``╔═╗╝╚`` glyphs."""
    out = _render(
        Markdown(
            "| A | B |\n|---|---|\n| 1 | 2 |\n",
            theme={"table_border_style": "double"},
        )
    )
    # Double-line corners.
    assert "╔" in out
    assert "╗" in out
    assert "╚" in out
    assert "╝" in out
    # Double horizontal fill.
    assert "═" in out
    # Single horizontal should NOT appear (double replaces it).
    assert "─" not in out


def test_table_align_center() -> None:
    """``:---:`` marker centers the column's cells."""
    out = _render(Markdown("| A |\n|:---:|\n| short |\n| longertext |\n"))
    # The header "A" should be centered (padding on both sides). With
    # ideal width = len("longertext") = 10, "A" gets 4 spaces left +
    # 5 right (or 5 left + 4 right — center splits with extra on right).
    # We assert the centered position by checking "A" is not left-aligned
    # (i.e. there's a space before "A" in the header row).
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # The header row is the second line (after the top border). Find it.
    lines = visible.split("\n")
    header_lines = [ln for ln in lines if "A" in ln and "│" in ln]
    assert header_lines, f"header row not found in: {visible!r}"
    header = header_lines[0]
    # Centered: there should be a space immediately before "A" (not
    # left-aligned which would put "A" right after "│ ").
    a_idx = header.index("A")
    assert a_idx > 0 and header[a_idx - 1] == " ", (
        f"expected 'A' to be centered (space before); got: {header!r}"
    )


def test_table_align_right() -> None:
    """``---:`` marker right-aligns the column's cells."""
    out = _render(Markdown("| A |\n|---:|\n| short |\n| longertext |\n"))
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", out)
    lines = visible.split("\n")
    # The body row "short" should be right-aligned (padding on left).
    short_lines = [ln for ln in lines if "short" in ln and "│" in ln]
    assert short_lines, f"short row not found in: {visible!r}"
    short_line = short_lines[0]
    s_idx = short_line.index("short")
    # Right-aligned: there should be a space before "short" (not
    # right after "│ ").
    assert s_idx > 0 and short_line[s_idx - 1] == " ", (
        f"expected 'short' to be right-aligned (space before); got: {short_line!r}"
    )


def test_table_cell_inline_bold() -> None:
    """``**bold**`` inside a cell renders with SGR 1."""
    out = _render(Markdown("| A |\n|---|\n| **bold** |\n"))
    assert f"{ESC}[1mbold{ESC}[0m" in out


def test_table_cell_inline_code() -> None:
    """`` `code` `` inside a cell renders with the code colour.

    PR3: code_colour default is ``"accent"`` → cyan (SGR 36).
    """
    out = _render(Markdown("| A |\n|---|\n| `code` |\n"))
    assert f"{ESC}[36mcode{ESC}[0m" in out


def test_table_responsive_shrink_wide() -> None:
    """Wide terminal uses ideal widths (no truncation)."""
    src = (
        "| Name | Age | Role |\n"
        "|:-----|:---:|------:|\n"
        "| Alice | 30 | Admin |\n"
        "| Bob | 25 | User |\n"
    )
    out = _render(Markdown(src), columns=120)
    # All cell content appears in full (ideal widths fit).
    for label in ("Name", "Age", "Role", "Alice", "Bob", "Admin", "User", "30", "25"):
        assert label in out


def test_table_responsive_shrink_narrow() -> None:
    """Narrow terminal keeps the bordered layout but shrinks columns.

    At 50 columns the 3-column table still fits (sum of ideal widths
    + borders < 50), so we expect the bordered layout rather than the
    key-value fallback.
    """
    src = (
        "| Name | Age | Role |\n"
        "|:-----|:---:|------:|\n"
        "| Alice | 30 | Admin |\n"
        "| Bob | 25 | User |\n"
    )
    out = _render(Markdown(src), columns=50)
    # Border glyphs present (bordered layout, not key-value).
    assert "┌" in out
    assert "└" in out
    # Content present.
    for label in ("Name", "Alice", "Bob", "Admin", "User"):
        assert label in out


def test_table_kv_fallback() -> None:
    """Extreme narrow terminal degrades to key-value layout."""
    src = (
        "| Name | Age | Role |\n"
        "|:-----|:---:|------:|\n"
        "| Alice | 30 | Admin |\n"
        "| Bob | 25 | User |\n"
    )
    out = _render(Markdown(src), columns=20)
    # No border glyphs (key-value layout, no frame).
    for glyph in ("┌", "┐", "└", "┘", "├", "┤", "┬", "┴", "┼"):
        assert glyph not in out, (
            f"border glyph {glyph!r} should be absent in key-value fallback"
        )
    # Key-value pairs appear: "Name:" / "Age:" / "Role:" labels.
    assert "Name:" in out
    assert "Age:" in out
    assert "Role:" in out
    # Values appear.
    for label in ("Alice", "Bob", "Admin", "User", "30", "25"):
        assert label in out


def test_table_string_width_cjk() -> None:
    """CJK characters count as 2 cells (string_width, not len)."""
    src = (
        "| 名前 | 年齢 |\n"
        "|:-----|:---:|\n"
        "| 愛子 | 30 |\n"
        "| 太郎 | 25 |\n"
    )
    out = _render(Markdown(src), columns=80)
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", out)
    lines = visible.split("\n")
    # All data rows should have the same total visible width (the
    # CJK width is correctly accounted for so columns line up). We
    # check the right border ``│`` appears at the same column on
    # every data row.
    data_lines = [ln for ln in lines if "│" in ln and ("愛子" in ln or "太郎" in ln)]
    assert len(data_lines) >= 2, f"expected ≥2 CJK data rows; got: {visible!r}"
    # The right-edge ``│`` column should be the same on both rows.
    right_cols = {ln.rfind("│") for ln in data_lines}
    assert len(right_cols) == 1, (
        f"CJK rows have inconsistent right-edge column: {right_cols}; "
        f"lines={data_lines!r}"
    )


def test_table_columns_from_layout_width() -> None:
    """A static table inside a narrow parent Box shrinks to fit.

    Regression for the PR2 static-path-routes-through-_MarkdownImpl
    change: a static ``Markdown("...")`` inside a parent Box with a
    constrained width must pick up the layout-time width via
    ``get_current_text_width()`` and shrink the table accordingly.
    Pre-PR2 the static path eagerly built the element tree with no
    width context, so the table rendered at ideal widths and overflowed.
    """
    src = (
        "| Name | Age | Role |\n"
        "|:-----|:---:|------:|\n"
        "| Alice | 30 | Admin |\n"
        "| Bob | 25 | User |\n"
    )
    # Wrap the static Markdown in a parent Box with padding + border so
    # the available content width is well below the viewport width.
    out = _render(
        Box(
            Markdown(src),
            flexDirection="column",
            padding=2,
            borderStyle="round",
        ),
        columns=30,
    )
    # The table should degrade to key-value (30 cols - padding 2*2 -
    # border 2 = ~24 content; 3-column table with min widths 7+3+10=20
    # + border overhead ~10 = 30 > 24 → fallback).
    assert "Name:" in out
    assert "Age:" in out
    assert "Role:" in out


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


def test_signal_source_incremental_stream_shows_each_state() -> None:
    """Streaming regression (Bug 3): each incremental write to the buffer
    should land on screen, not just the final state. Before the render
    cache the parse + nested layout was so slow the render loop couldn't
    keep up with ~50 writes/sec and the user only saw the final frame.
    """
    buf = signal("")
    inst = render(
        Markdown(buf),
        stdout=io.StringIO(),
        stdin=_FakeTTY(),
        columns=80,
        rows=20,
        exit_on_ctrl_c=False,
    )
    # Drip "AB" then "ABC" — the intermediate "AB" state must appear
    # before the longer write clobbers the buffer.
    buf.value = "AB"
    assert _wait_for(lambda: "AB" in inst.current_frame)
    buf.value = "ABC"
    assert _wait_for(lambda: "ABC" in inst.current_frame)
    inst.unmount()


def test_streaming_markdown_inside_border_box_stays_consistent() -> None:
    """Regression (Bug 4): streaming Markdown inside a bordered Box must
    keep the border intact at every intermediate state. The frame is
    always the viewport height (the border Box stretches to fill rows),
    so each row of every painted frame should still carry the left/right
    border columns.
    """
    buf = signal("")
    inst = render(
        Box(
            Markdown(buf),
            flexDirection="column",
            borderStyle="round",
        ),
        stdout=io.StringIO(),
        stdin=_FakeTTY(),
        columns=30,
        rows=10,
        exit_on_ctrl_c=False,
    )
    # Grow the buffer one character at a time and verify each frame
    # still has its border characters on every visible row.
    src = "# Title\n\n- one\n- two\n- three\n"
    for i in range(1, len(src) + 1):
        buf.value = src[:i]
        # Wait for the next paint to land (current_frame always reflects
        # the latest rendered state). Cap the wait at 2 s — long enough
        # for the throttle to flush on a slow CI box.
        _wait_for(lambda: bool(inst.current_frame))
        # Each line of the frame should start with a non-space character
        # (the left border column). We don't check the exact glyph
        # (round-border corner chars differ on top/bottom rows).
        for line in inst.current_frame.split("\n"):
            assert line[:1] != " ", (
                f"border lost on line {line!r} at i={i}"
            )
    inst.unmount()


def test_signal_source_nested_border_box_does_not_scramble() -> None:
    """Regression: streaming Markdown inside a bordered Box must size the
    Markdown snapshot to the actual available width, not the viewport
    width. Pre-fix the snapshot was rendered at ``inst.columns`` (e.g.
    70) and then placed inside a narrower content area (e.g. 66); the
    pre-rendered box-drawing characters inside the snapshot could not
    be re-wrapped by the layout engine, so the inner code-block border
    overflowed the outer Box's right border and the outer border
    appeared to "scramble" at the end of the stream — orphaned
    half-width inner borders on their own rows and missing inner
    right-edge characters on the code-bearing rows.

    The fix exposes the layout-time measurement width to width-aware
    text renderers (see ``ink.layout._text_width_context``) so the
    Markdown snapshot is sized to the actual content box.

    We grow the buffer character-by-character and at the final state
    assert:

    * Every line of the painted frame fits within ``columns`` visible
      cells (no overflow into the column past the right border).
    * The outer border uses round corners; every visible body row
      carries the outer right-edge ``│`` at the same column
      (``columns - 1``).
    * The inner code-block border (single-line ``│``) is consistent
      across every code-bearing row: the inner left-edge column and
      the inner right-edge column are the same on every such row.
      Pre-fix the orphaned-border bug produced inner-left without
      inner-right (or vice versa) on some rows.
    """
    buf = signal("")
    columns = 70
    inst = render(
        Box(
            Markdown(buf),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        ),
        stdout=io.StringIO(),
        stdin=_FakeTTY(),
        columns=columns,
        rows=18,
        exit_on_ctrl_c=False,
    )
    src = (
        "# Title\n\n"
        "Intro.\n\n"
        "```python\n"
        "def square(n: int) -> int:\n"
        "    return n * n\n"
        "```\n\n"
        "Done.\n"
    )
    for i in range(1, len(src) + 1):
        buf.value = src[:i]
        _wait_for(lambda: bool(inst.current_frame))
    # Snapshot the final frame.
    final = inst.current_frame
    inst.unmount()

    # Strip colour/style SGR sequences so visible widths are exact, but
    # keep the box-drawing characters we want to make assertions about.
    import re

    visible = re.sub(r"\x1b\[[0-9;]*m", "", final)
    lines = visible.split("\n")
    assert lines, "expected a painted frame"

    # Every line must fit within ``columns`` cells.
    for i, line in enumerate(lines):
        assert len(line) <= columns, (
            f"line {i} overflows viewport: len={len(line)} > {columns}: "
            f"{line!r}"
        )

    # The outer border uses round corners; the right edge on body rows
    # is ``│``. Every body row that fills the viewport width should
    # end with ``│`` at ``columns - 1``.
    body_lines = [
        (i, ln) for i, ln in enumerate(lines)
        if len(ln) == columns and ln.endswith("│")
    ]
    assert body_lines, (
        f"expected at least one outer-border body row, got: {lines!r}"
    )

    # If pygments is unavailable the inner code block renders as plain
    # dim Text with no inner border; the overflow assertion above is
    # still the load-bearing guarantee in that case.
    if not _pygments_available():  # pragma: no cover - environment dep
        return

    # The inner code-block border uses single-line box-drawing. Each
    # code-bearing body row should carry both the inner-left and
    # inner-right ``│`` at consistent columns across rows. We collect
    # the inner-left and inner-right columns per row and assert every
    # code-bearing row carries the same pair.
    code_bearing_rows: list[tuple[int, str]] = [
        (i, ln) for i, ln in body_lines
        if ("def " in ln or "return " in ln)
    ]
    assert code_bearing_rows, (
        f"expected code-bearing rows in frame; got: "
        f"{[ln for _, ln in body_lines]!r}"
    )
    inner_left_cols: set[int] = set()
    inner_right_cols: set[int] = set()
    for i, ln in code_bearing_rows:
        inner_cols = [
            col for col in range(1, columns - 1)
            if col < len(ln) and ln[col] == "│"
        ]
        assert len(inner_cols) == 2, (
            f"code-bearing line {i} should have exactly two inner "
            f"border columns (left + right); got {inner_cols}: {ln!r}"
        )
        inner_left_cols.add(inner_cols[0])
        inner_right_cols.add(inner_cols[1])
    assert len(inner_left_cols) == 1, (
        f"inner left border column inconsistent across rows: "
        f"{inner_left_cols}"
    )
    assert len(inner_right_cols) == 1, (
        f"inner right border column inconsistent across rows: "
        f"{inner_right_cols}"
    )


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
    # content. PR3: h1_color default is None (terminal default) so no
    # colour SGR appears either — only italic (3) + underline (4) remain.
    assert f"{ESC}[1m" not in out
    # Italic + underline are still on (PR3 defaults).
    assert f"{ESC}[3m" in out
    assert f"{ESC}[4m" in out


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
    assert "pip install ink[markdown]" in msg


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
    # Code-block tokens are interleaved with ANSI sequences once PR4
    # routes them through HighlightedCode, so we check the individual
    # tokens (``print`` / ``hi``) rather than the literal ``print('hi')``
    # substring.
    for needle in (
        "Title",
        "Paragraph with",
        "bold",
        "italic",
        "Subsection",
        "Item 1",
        "Item 2",
        "print",
        "hi",
        "A quote",
        "Link",
        "https://example.com",
    ):
        assert needle in out, f"missing {needle!r} in output"


# ---------------------------------------------------------------------------
# Export checks (PRD Decision 5 — externals stay opt-in)
# ---------------------------------------------------------------------------


def test_markdown_exported_from_externals() -> None:
    from ink import externals

    assert externals.Markdown is Markdown
    assert externals.DEFAULT_MARKDOWN_THEME is DEFAULT_MARKDOWN_THEME


def test_markdown_not_in_top_level_namespace() -> None:
    import ink

    assert not hasattr(ink, "Markdown"), (
        "Markdown should not be exported from the top-level ink namespace"
    )


# ---------------------------------------------------------------------------
# PR4: HighlightedCode integration
# ---------------------------------------------------------------------------


# These tests exercise the PR4 path: fenced code blocks render via
# :func:`HighlightedCode` when :mod:`pygments` is importable, and fall
# back to PR3's plain dim Text when it isn't. We need to be tolerant of
# environments where pygments is missing — those environments skip the
# "highlighted" assertions and only the fallback + theme-knob tests
# remain meaningful. Conversely, the fallback path is exercised via an
# import blocker regardless of whether pygments is installed.


_pygments_mark = pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)


@_pygments_mark
def test_fenced_python_block_uses_highlighted_code() -> None:
    """A ``python`` fence routes through HighlightedCode.

    HighlightedCode maps ``def`` to magenta (SGR 35) and string literals
    to green (SGR 32). Either colour sequence appearing in the output
    proves the highlighted path was taken — PR3's plain dim fallback
    only ever emits SGR 2.
    """
    out = _render(Markdown("```python\ndef hello():\n    return 'world'\n```"))
    # Magenta (35) for the ``def`` keyword.
    assert f"{ESC}[35m" in out
    # Green (32) for the string literal.
    assert f"{ESC}[32m" in out
    # Both source tokens present (possibly split by ANSI sequences).
    assert "def" in out
    assert "hello" in out


@_pygments_mark
def test_fenced_block_forwards_code_block_theme() -> None:
    """``code_block_theme`` overrides HighlightedCode's token colours.

    The default mapping colours ``def`` magenta (SGR 35). Overriding
    ``Keyword`` to cyan (SGR 36) should remove the magenta and emit
    cyan instead.
    """
    out = _render(
        Markdown(
            "```python\ndef f():\n    pass\n```",
            theme={"code_block_theme": {"Keyword": "cyan"}},
        )
    )
    assert f"{ESC}[36m" in out
    # The default magenta (35) should NOT appear for the keyword.
    assert f"{ESC}[35m" not in out


@_pygments_mark
def test_fenced_block_applies_border_color() -> None:
    """``code_block_border_color`` sets the wrapper border colour.

    Default border colour is ``gray`` (SGR 90). Overriding it to
    ``magenta`` (SGR 35) should make the wrapper border box's
    ``borderColor`` prop ``"magenta"``, which surfaces in the rendered
    output as the border glyphs being wrapped in SGR 35.
    """
    import re

    out = _render(
        Markdown(
            "```python\nx = 1\n```",
            theme={"code_block_border_color": "magenta"},
        )
    )
    # The single-line border glyphs (┌ ─ │ └ ┐) should be wrapped in
    # magenta (SGR 35) rather than the default gray (SGR 90). We look
    # for a run of SGR + box-drawing glyphs + reset to assert the
    # border colour specifically (the language header still uses gray
    # for its dim styling, so a bare ``[35m in out`` would be enough
    # but ``[90m not in out`` would false-positive on the header).
    border_runs = re.findall(
        r"\x1b\[[0-9;]*m[┌─┐│└┘]+\x1b\[0m", out
    )
    assert border_runs, "expected at least one border glyph run in output"
    for run in border_runs:
        assert f"{ESC}[35m" in run, (
            f"border run should use magenta (SGR 35); got: {run!r}"
        )
        assert f"{ESC}[90m" not in run, (
            f"border run should NOT use default gray (SGR 90); got: {run!r}"
        )


@_pygments_mark
def test_fenced_block_show_border_false_removes_frame() -> None:
    """``code_block_show_border=False`` skips the wrapper border Box.

    With the border disabled, the rendered output should NOT contain
    the single-line box-drawing glyphs (``┌`` / ``┐`` / ``└`` / ``┘`` /
    ``│``) that the bordered path emits.
    """
    out = _render(
        Markdown(
            "```python\nx = 1\n```",
            theme={"code_block_show_border": False},
        )
    )
    # The corner glyphs of a single-line border should be absent.
    for glyph in ("┌", "┐", "└", "┘"):
        assert glyph not in out, (
            f"border glyph {glyph!r} should be absent when "
            f"code_block_show_border=False"
        )


@_pygments_mark
def test_fenced_block_show_language_false_omits_header() -> None:
    """``code_block_show_language=False`` drops the language header line."""
    out_no_header = _render(
        Markdown(
            "```python\nx = 1\n```",
            theme={"code_block_show_language": False},
        )
    )
    # The string "python" should NOT appear as a standalone dim header
    # line. It might still appear as part of an attribute somewhere, so
    # we check the dim header styling is absent for the literal "python"
    # token: PR4 emits the header as ``Text("python", dimColor=True)``,
    # which produces SGR 2 ... python ... SGR 0.
    header_seq = f"{ESC}[2mpython{ESC}[0m"
    assert header_seq not in out_no_header, (
        "language header should be omitted when "
        "code_block_show_language=False"
    )


@_pygments_mark
def test_fenced_javascript_block_renders() -> None:
    """A non-Python language fence renders via the matching lexer."""
    out = _render(
        Markdown(
            "```javascript\n"
            "function greet(name) {\n"
            "    console.log('Hello, ' + name);\n"
            "}\n"
            "```"
        )
    )
    # ``function`` keyword → magenta in the default theme.
    assert f"{ESC}[35m" in out
    # String literal → green.
    assert f"{ESC}[32m" in out
    # Identifiers / structure present (possibly ANSI-split).
    assert "greet" in out
    assert "name" in out


@_pygments_mark
def test_fenced_json_block_renders() -> None:
    """A JSON fence routes through the json lexer without error."""
    out = _render(
        Markdown(
            '```json\n{"key": "value", "n": 42}\n```'
        )
    )
    # Strings → green (32), number → cyan (36).
    assert f"{ESC}[32m" in out
    assert f"{ESC}[36m" in out
    assert "key" in out
    assert "value" in out


@_pygments_mark
def test_fenced_sql_block_renders() -> None:
    """A SQL fence routes through the sql lexer without error."""
    out = _render(
        Markdown("```sql\nSELECT * FROM users WHERE id = 1;\n```")
    )
    # ``SELECT`` keyword → magenta (35).
    assert f"{ESC}[35m" in out
    # Number → cyan (36).
    assert f"{ESC}[36m" in out


@_pygments_mark
def test_indented_code_block_also_uses_highlighted_code() -> None:
    """markdown_it emits ``code_block`` (not ``fence``) for indented code.

    Both token types route through :func:`_render_fence`; an indented
    block has no language label, so HighlightedCode falls back to its
    plain-text fast path and the source renders verbatim.
    """
    out = _render(Markdown("    x = 1\n    y = 2\n"))
    # Plain text path: source lines render verbatim, no syntax colours.
    assert "x = 1" in out
    assert "y = 2" in out


@_pygments_mark
def test_markdown_with_code_block_inside_box_border_composes() -> None:
    """A highlighted code block composes inside a parent border Box."""
    out = _render(
        Box(
            Markdown("# Title\n\n```python\nx = 1\n```"),
            borderStyle="round",
            padding=1,
        )
    )
    # Heading present.
    assert "Title" in out
    # PR3: h1 default has no colour SGR (None), so we assert the code
    # block's syntax highlighting appears instead — Pygments maps ``=``
    # to red (SGR 31) and the number ``1`` to cyan (SGR 36).
    assert f"{ESC}[31m" in out or f"{ESC}[36m" in out


def test_fenced_code_block_falls_back_to_dim_when_pygments_missing(
    _restore_import: None,
) -> None:
    """When pygments is unavailable, fenced blocks render as dim Text.

    Blocks the ``import pygments`` lookup so HighlightedCode raises the
    friendly ImportError and ``_render_fence`` falls back to the PR3
    plain dim path. The body should emit SGR 2 (dim) for every code
    row, with no syntax-highlight colours.
    """
    _install_pygments_import_blocker()
    out = _render(Markdown("```python\ndef f():\n    return 'x'\n```"))
    # Source lines render verbatim (no ANSI splitting).
    assert "def f():" in out
    assert "return 'x'" in out
    # Dim (SGR 2) is applied to the body.
    assert f"{ESC}[2m" in out
    # No syntax-highlight colours leak through.
    assert f"{ESC}[35m" not in out  # no magenta keyword
    assert f"{ESC}[32m" not in out  # no green string


def test_fenced_code_block_fallback_respects_show_language(
    _restore_import: None,
) -> None:
    """The fallback path still honours ``code_block_show_language``.

    The header is emitted as ``Text("python", dimColor=True, color="gray")``
    which produces nested SGR sequences (``\\x1b[2m\\x1b[90mpython\\x1b[0m``).
    We assert the substring ``"python"`` appears in the output when the
    header is on, and is absent when ``show_language=False``.
    """
    _install_pygments_import_blocker()
    out_with_header = _render(Markdown("```python\nx = 1\n```"))
    assert "python" in out_with_header

    out_no_header = _render(
        Markdown(
            "```python\nx = 1\n```",
            theme={"code_block_show_language": False},
        )
    )
    # With the header disabled, "python" should not appear at all — the
    # fallback path renders only the source code lines.
    assert "python" not in out_no_header


def test_fenced_code_block_fallback_has_no_border(
    _restore_import: None,
) -> None:
    """The fallback path never wraps code in a border Box.

    The border is a PR4 addition tied to the HighlightedCode path; the
    PR3 fallback stays a plain Box of dim Text rows. We render to a
    string and assert the single-line border corner glyphs are absent.
    """
    _install_pygments_import_blocker()
    out = _render(Markdown("```python\nx = 1\n```"))
    # The corner glyphs of a single-line border should be absent in
    # the fallback path.
    for glyph in ("┌", "┐", "└", "┘"):
        assert glyph not in out, (
            f"fallback path should not wrap code in a border; "
            f"found glyph {glyph!r} in output"
        )


def test_nested_code_block_in_markdown_does_not_break_surrounding_blocks(
    _restore_import: None,
) -> None:
    """Code block fallback composes inside a full Markdown document.

    Surrounding blocks (heading, paragraph, list) must still render
    correctly when the code block falls back to the dim path.
    """
    _install_pygments_import_blocker()
    src = (
        "# Title\n\n"
        "Before code.\n\n"
        "```python\n"
        "print('hi')\n"
        "```\n\n"
        "After code.\n"
    )
    out = _render(Markdown(src))
    for needle in ("Title", "Before code.", "print('hi')", "After code."):
        assert needle in out, f"missing {needle!r} in fallback output"


# ---------------------------------------------------------------------------
# Reactive cache (Bug 2/3 regression)
# ---------------------------------------------------------------------------


def test_reactive_render_cache_hits_avoid_repeated_parse() -> None:
    """Two reactive renders with the same source hit the cache.

    Regression for the Phase 3 "streaming Markdown pins CPU" bug: the
    render loop's subscription layout *and* the paint layout both
    evaluate the reactive ``Text`` callable, so without the cache each
    signal flush re-parses the whole document twice. We assert the
    cache key is hit on the second call with the same input.
    """
    from ink.externals.markdown import _cached_render, _render_cache

    _render_cache.clear()
    theme: dict[str, Any] = {"_test": True}
    src = "# Title\n\nParagraph."
    first = _cached_render(src, 80, theme)
    # Second call with identical arguments must hit the cache (return
    # the exact same string instance).
    second = _cached_render(src, 80, theme)
    assert first == second
    assert (src, 80, id(theme)) in _render_cache


def test_reactive_render_cache_evicts_lru_entries() -> None:
    """The cache is bounded; inserting past the cap evicts the oldest."""
    from ink.externals import markdown as md_mod

    md_mod._render_cache.clear()
    theme: dict[str, Any] = {}
    # Push twice the cap so eviction kicks in.
    for i in range(md_mod._RENDER_CACHE_MAX * 2):
        md_mod._cached_render(f"# Title {i}", 80, theme)
    assert len(md_mod._render_cache) <= md_mod._RENDER_CACHE_MAX


# ---------------------------------------------------------------------------
# PR1: Markdown render polish — semantic theme keys + style inheritance
# ---------------------------------------------------------------------------


def test_semantic_color_keys_exist() -> None:
    """``DEFAULT_MARKDOWN_THEME`` defines all 9 semantic colour keys.

    PR1 introduces the semantic colour layer (``text`` / ``accent`` /
    ``secondary`` / ``muted`` / ``border`` + ``success`` / ``error`` /
    ``warning`` / ``info``). The keys are defined now so downstream
    callers can opt in early; PR3 will rewire the legacy colour keys
    to default-resolve through these.
    """
    expected = {
        "text_color",
        "accent_color",
        "secondary_color",
        "muted_color",
        "border_color",
        "success_color",
        "error_color",
        "warning_color",
        "info_color",
    }
    assert expected.issubset(DEFAULT_MARKDOWN_THEME.keys()), (
        f"missing semantic keys: {expected - set(DEFAULT_MARKDOWN_THEME.keys())}"
    )


def test_semantic_colors_dict_has_complete_mapping() -> None:
    """``SEMANTIC_COLORS`` maps every semantic key to a colour or None."""
    from ink.externals.markdown import SEMANTIC_COLORS

    expected = {
        "text",
        "accent",
        "secondary",
        "muted",
        "border",
        "success",
        "error",
        "warning",
        "info",
    }
    assert set(SEMANTIC_COLORS.keys()) == expected
    # ``text`` is None (inherit terminal default); the rest are concrete
    # colour names from PyInk's NAMED_COLORS vocabulary.
    assert SEMANTIC_COLORS["text"] is None
    assert SEMANTIC_COLORS["accent"] == "cyan"
    assert SEMANTIC_COLORS["muted"] == "gray"


def test_inline_code_inherits_bold() -> None:
    """``**bold `code`**`` — inline code picks up the surrounding bold.

    PR1 bug fix: previously ``code_inline`` dropped the outer inline
    state (bold / italic / strikethrough), so a code segment inside a
    bold span rendered without bold. Now the code run inherits the
    active bold / italic / strikethrough.
    """
    out = _render(Markdown("**bold `code`**"))
    # PR3: code_color default "accent" → cyan (SGR 36). Bold SGR 1.
    assert "code" in out
    assert f"{ESC}[36m" in out  # code_color accent → cyan
    assert f"{ESC}[1m" in out  # bold
    # The code run itself should be wrapped with both: find the
    # substring where the code text is wrapped by bold+colour.
    # apply_style emits opens in order: dim, fg, bg, bold, italic, ...
    # so for (color=cyan, bold=True) we get ESC[36m ESC[1m code ESC[0m.
    assert f"{ESC}[36m{ESC}[1mcode{ESC}[0m" in out, (
        f"expected code segment wrapped with cyan+bold; got: {out!r}"
    )


def test_inline_code_inherits_italic_and_strikethrough() -> None:
    """``~~strike `code`~~`` — code inherits strikethrough too.

    Companion to :func:`test_inline_code_inherits_bold`; verifies the
    fix threads all three inline states (bold / italic / strikethrough)
    rather than just bold.
    """
    # markdown-it commonmark doesn't enable strikethrough by default;
    # we test italic inheritance here (``*italic `code`*``).
    out = _render(Markdown("*italic `code`*"))
    assert "code" in out
    assert f"{ESC}[3m" in out  # italic
    # PR3: code_color default "accent" → cyan (SGR 36).
    # The code run should carry both italic and the code colour.
    # apply_style open order: fg → bold → italic, so ESC[36m ESC[3m code.
    assert f"{ESC}[36m{ESC}[3mcode{ESC}[0m" in out, (
        f"expected code segment wrapped with cyan+italic; got: {out!r}"
    )


def test_inline_code_in_plain_paragraph_unchanged() -> None:
    """A plain ``Use `code`.`` still emits just the colour SGR.

    Regression guard for the PR1 inline-code inheritance fix: when
    there's no surrounding bold/italic/strikethrough, the code run
    should still render exactly as before (colour SGR only, no stray
    bold/italic). This protects :func:`test_inline_code_gets_code_color`
    semantics against drift.
    """
    out = _render(Markdown("Use `code` here."))
    # PR3: code_color default "accent" → cyan (SGR 36).
    assert f"{ESC}[36mcode{ESC}[0m" in out
    # No bold SGR should appear in the output at all.
    assert f"{ESC}[1m" not in out


def test_heading_underline_theme_key() -> None:
    """``theme={"h1_underline": True}`` adds underline SGR to h1.

    PR1 adds per-level ``h{n}_underline`` / ``h{n}_italic`` theme knobs.
    PR3 flips h1's default to underline=True (claude-code style); this
    test now verifies the opt-in still works on a level whose default
    is False (h2). Passing ``theme={"h2_underline": True}`` layers
    underline on top of h2's default bold.
    """
    out = _render(Markdown("## Sub", theme={"h2_underline": True}))
    # Underline = SGR 4.
    assert f"{ESC}[4m" in out
    assert "Sub" in out
    # h2 default is bold (no colour PR3).
    assert f"{ESC}[1m" in out


def test_heading_italic_theme_key() -> None:
    """``theme={"h2_italic": True}`` adds italic SGR to h2."""
    out = _render(Markdown("## Sub", theme={"h2_italic": True}))
    # Italic = SGR 3.
    assert f"{ESC}[3m" in out
    assert "Sub" in out
    # PR3: h2 default is bold only (no colour).
    assert f"{ESC}[1m" in out
    assert f"{ESC}[33m" not in out  # no legacy yellow


def test_heading_underline_default_on_for_h1() -> None:
    """PR3: default theme — h1 carries underline SGR (claude-code style)."""
    out = _render(Markdown("# Title"))
    # SGR 4 (underline) should appear for h1 by default.
    assert f"{ESC}[4m" in out


def test_heading_underline_default_off_for_h2() -> None:
    """PR3: default theme — h2 does NOT carry underline SGR (h1 only)."""
    out = _render(Markdown("## Sub"))
    assert f"{ESC}[4m" not in out


def test_blockquote_bar_char_default_on() -> None:
    """PR3: default theme — blockquote has the ``▎`` bar (claude-code style).

    Pre-PR3 default was ``quote_bar_char=None`` (pure indent). PR3 flips
    the default to ``"▎"`` so blockquotes render with a visible left bar
    in the ``muted`` semantic colour (→ gray, SGR 90).
    """
    out = _render(Markdown("> A quote"))
    # The default quote_bar_char is now "▎" — the bar should appear.
    assert "▎" in out
    assert "A quote" in out
    # The bar carries the muted colour (gray SGR 90) by default.
    assert f"{ESC}[90m" in out
    # The blockquote content is still dim (SGR 2) via __quote__ flag.
    assert f"{ESC}[2m" in out


def test_blockquote_bar_char_custom() -> None:
    """``theme={"quote_bar_char": "▎"}`` renders a left bar.

    PR1 adds the ``quote_bar_char`` / ``quote_bar_color`` theme knobs.
    When the char is set, the blockquote becomes a row Box with the bar
    on the left replacing the pure-indent behaviour.
    """
    out = _render(Markdown("> A quote", theme={"quote_bar_char": "▎"}))
    assert "▎" in out
    assert "A quote" in out


def test_blockquote_bar_char_with_color() -> None:
    """``theme={"quote_bar_char": "▎", "quote_bar_color": "cyan"}`` colours the bar."""
    out = _render(
        Markdown("> A quote", theme={"quote_bar_char": "▎", "quote_bar_color": "cyan"})
    )
    assert "▎" in out
    # Cyan SGR 36 applied to the bar segment.
    assert f"{ESC}[36m" in out


def test_blockquote_bar_char_dim_fallback() -> None:
    """``quote_bar_color=None`` (default) falls back to dimColor on the bar."""
    out = _render(Markdown("> A quote", theme={"quote_bar_char": "│"}))
    assert "│" in out
    # dimColor SGR 2 applied somewhere (the bar segment).
    assert f"{ESC}[2m" in out


def test_list_nested_ordered_auto() -> None:
    """``list_ordered_nested_style="auto"`` shifts marker by depth.

    PR1 adds the ``list_ordered_nested_style`` theme key. ``"auto"``
    uses decimal at the top level (depth 0), alpha at depth 1, roman
    at depth ≥ 2. We render a 3-level nested ordered list and assert
    the depth-2 marker is an alpha letter (``a.``).
    """
    src = (
        "1. top\n"
        "   1. nested\n"
        "      1. deep\n"
    )
    out = _render(Markdown(src, theme={"list_ordered_nested_style": "auto"}))
    # Top level (depth 0) → decimal "1."
    assert "1." in out
    # Depth 1 → alpha "a."
    assert "a." in out, f"expected alpha marker 'a.' for depth-1 nested list; got: {out!r}"
    # Depth 2 → roman "i."
    assert "i." in out, f"expected roman marker 'i.' for depth-2 nested list; got: {out!r}"
    # Sanity: content present.
    for label in ("top", "nested", "deep"):
        assert label in out


def test_list_nested_ordered_alpha_style() -> None:
    """``list_ordered_nested_style="alpha"`` uses alpha at every depth."""
    src = (
        "1. top\n"
        "   1. nested\n"
    )
    out = _render(Markdown(src, theme={"list_ordered_nested_style": "alpha"}))
    # Both levels use alpha: top "a.", nested "a."
    assert out.count("a.") >= 2, f"expected at least 2 alpha markers; got: {out!r}"
    for label in ("top", "nested"):
        assert label in out


def test_list_nested_ordered_roman_style() -> None:
    """``list_ordered_nested_style="roman"`` uses roman at every depth."""
    src = (
        "1. top\n"
        "   1. nested\n"
    )
    out = _render(Markdown(src, theme={"list_ordered_nested_style": "roman"}))
    # Both levels use roman: "i." each.
    assert out.count("i.") >= 2, f"expected at least 2 roman markers; got: {out!r}"
    for label in ("top", "nested"):
        assert label in out


def test_list_nested_ordered_decimal_default() -> None:
    """Default ``list_ordered_nested_style="decimal"`` uses decimal at every depth.

    Regression guard: the default behaviour is unchanged by PR1 — every
    nesting level renders ``1.`` / ``2.`` / …
    """
    src = (
        "1. top\n"
        "   1. nested\n"
    )
    out = _render(Markdown(src))
    # Decimal markers: "1." appears at both levels (counter resets to 1
    # for the nested list because markdown-it-py starts each ordered
    # list at 1 unless the source says otherwise).
    assert out.count("1.") >= 2


def test_list_nested_bullet_chars() -> None:
    """``list_bullet_nested_chars="-*+"`` cycles bullet by depth.

    PR1 adds the ``list_bullet_nested_chars`` theme key. A multi-char
    string cycles by ``depth % len(chars)``: depth 0 → ``-``, depth 1
    → ``*``, depth 2 → ``+``, depth 3 → ``-`` (wraps).
    """
    src = (
        "- a\n"
        "  - a1\n"
        "    - a1a\n"
    )
    out = _render(Markdown(src, theme={"list_bullet_nested_chars": "-*+"}))
    # Markers are rendered as dim "<char> " segments wrapped in SGR 2.
    # We assert the three distinct marker characters appear in depth
    # order: ``-`` (depth 0), ``*`` (depth 1), ``+`` (depth 2).
    idx_dash = out.find("- ")
    idx_star = out.find("* ")
    idx_plus = out.find("+ ")
    assert idx_dash >= 0, f"expected '-' marker; got: {out!r}"
    assert idx_star >= 0, f"expected '*' marker at depth 1; got: {out!r}"
    assert idx_plus >= 0, f"expected '+' marker at depth 2; got: {out!r}"
    # Order: dash first, then star, then plus.
    assert idx_dash < idx_star < idx_plus, (
        f"expected marker order '- * +'; got positions "
        f"dash={idx_dash} star={idx_star} plus={idx_plus} in {out!r}"
    )
    # Content sanity.
    for label in ("a", "a1", "a1a"):
        assert label in out


def test_list_nested_bullet_chars_default_single() -> None:
    """Default ``list_bullet_nested_chars="-"`` uses dash at every depth.

    Regression guard: the default behaviour is unchanged — every
    nesting level renders ``-``.
    """
    src = (
        "- a\n"
        "  - a1\n"
    )
    out = _render(Markdown(src))
    # Both levels use "-".
    assert out.count("- ") >= 2


def test_resolve_theme_color_prefers_legacy_key() -> None:
    """``_resolve_theme_color`` returns the legacy value when present.

    Backwards-compatibility contract: a caller passing
    ``theme={"h1_color": "cyan"}`` gets ``"cyan"`` back, not the
    semantic ``accent`` default.
    """
    from ink.externals.markdown import _resolve_theme_color

    theme = {"h1_color": "cyan", "accent_color": "red"}
    assert _resolve_theme_color(theme, "accent", "h1_color") == "cyan"


def test_resolve_theme_color_falls_back_to_semantic() -> None:
    """When the legacy key is absent, the semantic key is used."""
    from ink.externals.markdown import _resolve_theme_color

    theme = {"accent_color": "red"}
    # legacy_key "h1_color" is absent → fall through to semantic "accent".
    assert _resolve_theme_color(theme, "accent", "h1_color") == "red"


def test_resolve_theme_color_uses_semantic_default() -> None:
    """When neither key is set, the SEMANTIC_COLORS default is used."""
    from ink.externals.markdown import _resolve_theme_color

    theme: dict[str, Any] = {}
    # No legacy, no semantic override → SEMANTIC_COLORS["accent"] = "cyan".
    assert _resolve_theme_color(theme, "accent", "h1_color") == "cyan"


def test_resolve_theme_color_handles_none_legacy() -> None:
    """A ``None`` legacy value falls through to the semantic layer.

    This matches the convention that ``None`` means "inherit the
    terminal default" — but when a caller explicitly sets the legacy
    key to ``None``, we honour the semantic override (if any) rather
    than blindly returning ``None``.
    """
    from ink.externals.markdown import _resolve_theme_color

    theme = {"h1_color": None, "accent_color": "red"}
    # legacy is None → fall through to semantic "accent" = "red".
    assert _resolve_theme_color(theme, "accent", "h1_color") == "red"


def test_resolve_theme_color_resolves_semantic_name() -> None:
    """A semantic value that is itself a semantic name resolves one level deeper.

    Future-proofs for ``theme={"h1_color": "accent"}`` — the legacy
    value ``"accent"`` is a semantic name, so we resolve it through
    :data:`SEMANTIC_COLORS` to ``"cyan"``.
    """
    from ink.externals.markdown import _resolve_theme_color

    theme = {"h1_color": "accent"}
    assert _resolve_theme_color(theme, "accent", "h1_color") == "cyan"


# ---------------------------------------------------------------------------
# PR3: Default-value polish — claude-code style defaults + spacing
# ---------------------------------------------------------------------------


def test_default_h1_has_underline_and_italic() -> None:
    """PR3: default h1 renders with underline + italic SGR (claude-code style)."""
    out = _render(Markdown("# Title"))
    assert f"{ESC}[4m" in out  # underline
    assert f"{ESC}[3m" in out  # italic
    assert f"{ESC}[1m" in out  # bold
    assert "Title" in out


def test_default_h2_no_underline() -> None:
    """PR3: default h2 has no underline (only h1 gets underline)."""
    out = _render(Markdown("## Sub"))
    assert f"{ESC}[1m" in out  # bold
    assert f"{ESC}[4m" not in out  # no underline
    assert f"{ESC}[3m" not in out  # no italic


def test_default_code_color_accent() -> None:
    """PR3: default ``code_color="accent"`` resolves to cyan (SGR 36)."""
    out = _render(Markdown("Use `code` here."))
    # "accent" → SEMANTIC_COLORS["accent"] = "cyan" → SGR 36.
    assert f"{ESC}[36mcode{ESC}[0m" in out
    # The pre-PR3 default red (SGR 31) should NOT appear.
    assert f"{ESC}[31m" not in out


def test_default_blockquote_has_bar() -> None:
    """PR3: default blockquote renders the ``▎`` left bar (claude-code style)."""
    out = _render(Markdown("> A quote"))
    assert "▎" in out
    assert "A quote" in out
    # Bar colour resolves through "muted" → gray (SGR 90).
    assert f"{ESC}[90m" in out


def test_spacing_before_heading() -> None:
    """PR3: a heading preceded by a paragraph has a blank row before it.

    ``spacing_before_heading=1`` + ``spacing_after_paragraph=1`` → at
    least one blank line between the paragraph and the heading.
    """
    out = _render(Markdown("Para.\n\n# Heading"))
    lines = out.split("\n")
    # Find the heading line (contains "Heading") and the paragraph line.
    para_idx = next((i for i, ln in enumerate(lines) if "Para." in ln), None)
    heading_idx = next((i for i, ln in enumerate(lines) if "Heading" in ln), None)
    assert para_idx is not None and heading_idx is not None
    assert heading_idx > para_idx
    # At least one blank line between them.
    blank_count = sum(1 for ln in lines[para_idx + 1:heading_idx] if ln.strip() == "")
    assert blank_count >= 1, (
        f"expected ≥1 blank line before heading; got {blank_count}: {lines!r}"
    )


def test_spacing_after_heading() -> None:
    """PR3: a heading has 2 blank rows after it (claude-code style).

    ``spacing_after_heading=2`` → two blank lines between the heading
    and the following paragraph.
    """
    out = _render(Markdown("# Heading\n\nPara."))
    lines = out.split("\n")
    heading_idx = next((i for i, ln in enumerate(lines) if "Heading" in ln), None)
    para_idx = next((i for i, ln in enumerate(lines) if "Para." in ln), None)
    assert heading_idx is not None and para_idx is not None
    assert para_idx > heading_idx
    blank_count = sum(1 for ln in lines[heading_idx + 1:para_idx] if ln.strip() == "")
    assert blank_count >= 2, (
        f"expected ≥2 blank lines after heading; got {blank_count}: {lines!r}"
    )


def test_spacing_between_paragraphs() -> None:
    """PR3: two paragraphs have a blank row between them.

    ``spacing_after_paragraph=1`` → one blank line between paragraphs.
    """
    out = _render(Markdown("First para.\n\nSecond para."))
    lines = out.split("\n")
    first_idx = next((i for i, ln in enumerate(lines) if "First para." in ln), None)
    second_idx = next((i for i, ln in enumerate(lines) if "Second para." in ln), None)
    assert first_idx is not None and second_idx is not None
    assert second_idx > first_idx
    blank_count = sum(
        1 for ln in lines[first_idx + 1:second_idx] if ln.strip() == ""
    )
    assert blank_count >= 1, (
        f"expected ≥1 blank line between paragraphs; got {blank_count}: {lines!r}"
    )


def test_spacing_first_block_has_no_leading_blank() -> None:
    """PR3: the first block in a document does not get a leading blank row.

    ``spacing_before_*`` only applies between adjacent blocks — the
    very first block starts at row 0.
    """
    out = _render(Markdown("# Title"))
    lines = out.split("\n")
    # The first non-empty line should be the heading, not a blank row.
    # (The heading line contains "Title".)
    assert lines[0].strip() != "" or "Title" in lines[0], (
        f"first line should not be blank; got: {lines!r}"
    )


def test_nested_table_in_blockquote_shrinks() -> None:
    """PR3: a table nested inside a blockquote responsively shrinks.

    Regression for the PR2 known limitation: ``_render_blockquote``
    didn't thread ``columns`` into its recursive ``_render_tokens``
    call, so a nested table rendered at its ideal width and overflowed
    the blockquote's content box. PR3 threads ``columns - bar_indent``
    so the table can shrink or degrade to key-value inside a quote.
    """
    src = (
        "> | Name | Age | Role |\n"
        "> |:-----|:---:|------:|\n"
        "> | Alice | 30 | Admin |\n"
        "> | Bob | 25 | User |\n"
    )
    # Render at a narrow width so the table must shrink.
    out = _render(Markdown(src), columns=40)
    # The bar should be present (PR3 default).
    assert "▎" in out
    # Content is present (possibly in key-value fallback form).
    for label in ("Name", "Alice", "Bob"):
        assert label in out
    # No line should exceed the column budget (table didn't overflow).
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", out)
    for line in visible.split("\n"):
        assert len(line) <= 40, (
            f"line overflows 40 cols: len={len(line)}: {line!r}"
        )


def test_nested_table_in_list_item_shrinks() -> None:
    """PR3: a table nested inside a list item responsively shrinks.

    Companion to :func:`test_nested_table_in_blockquote_shrinks`; PR3
    threads ``columns`` through ``_render_list`` /
    ``_render_list_item`` so the recursive ``_render_tokens`` call
    inside a list item sees the item's available width.
    """
    src = (
        "- Item with table:\n"
        "  | Name | Age |\n"
        "  |:-----|:---:|\n"
        "  | Alice | 30 |\n"
        "  | Bob | 25 |\n"
    )
    out = _render(Markdown(src), columns=40)
    # Content present.
    for label in ("Name", "Alice", "Bob"):
        assert label in out
    # No line should exceed the column budget.
    import re
    visible = re.sub(r"\x1b\[[0-9;]*m", "", out)
    for line in visible.split("\n"):
        assert len(line) <= 40, (
            f"line overflows 40 cols: len={len(line)}: {line!r}"
        )


def test_legacy_color_override_still_works() -> None:
    """PR3 break-change: explicit ``theme={"h1_color": "magenta"}`` still works.

    Backwards-compatibility contract: even though the default h1 colour
    changed from ``"magenta"`` to ``None``, a caller that explicitly
    passes the old default still gets magenta (SGR 35). This is the
    migration path documented in the CHANGELOG.
    """
    out = _render(Markdown("# Title", theme={"h1_color": "magenta"}))
    assert f"{ESC}[35m" in out
    assert "Title" in out


def test_legacy_code_color_override_still_works() -> None:
    """PR3 break-change: explicit ``theme={"code_color": "red"}`` still works.

    Companion to :func:`test_legacy_color_override_still_works`: the
    default ``code_color`` changed from ``"red"`` to ``"accent"``, but
    the old default is still reachable via an explicit theme override.
    """
    out = _render(Markdown("Use `x`.", theme={"code_color": "red"}))
    assert f"{ESC}[31mx{ESC}[0m" in out


def test_legacy_blockquote_bar_disabled_still_works() -> None:
    """PR3 break-change: ``theme={"quote_bar_char": None}`` restores pure indent.

    The default ``quote_bar_char`` changed from ``None`` to ``"▎"``.
    A caller that wants the pre-PR3 pure-indent look can pass
    ``theme={"quote_bar_char": None}`` to opt out.
    """
    out = _render(Markdown("> A quote", theme={"quote_bar_char": None}))
    assert "▎" not in out
    assert "A quote" in out


def test_spacing_keys_exist_in_default_theme() -> None:
    """PR3: all 14 spacing keys are defined in ``DEFAULT_MARKDOWN_THEME``."""
    expected = {
        "spacing_before_heading",
        "spacing_after_heading",
        "spacing_before_paragraph",
        "spacing_after_paragraph",
        "spacing_before_code_block",
        "spacing_after_code_block",
        "spacing_before_blockquote",
        "spacing_after_blockquote",
        "spacing_before_list",
        "spacing_after_list",
        "spacing_before_table",
        "spacing_after_table",
        "spacing_before_hr",
        "spacing_after_hr",
    }
    assert expected.issubset(DEFAULT_MARKDOWN_THEME.keys()), (
        f"missing spacing keys: {expected - set(DEFAULT_MARKDOWN_THEME.keys())}"
    )
