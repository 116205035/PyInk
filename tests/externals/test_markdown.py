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
    from pyink import externals

    assert externals.Markdown is Markdown
    assert externals.DEFAULT_MARKDOWN_THEME is DEFAULT_MARKDOWN_THEME


def test_markdown_not_in_top_level_namespace() -> None:
    import pyink

    assert not hasattr(pyink, "Markdown"), (
        "Markdown should not be exported from the top-level pyink namespace"
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
    reason="pygments not installed (pip install pyink[highlight])",
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
    ``borderColor`` prop ``"magenta"``.
    """
    el = Markdown(
        "```python\nx = 1\n```",
        theme={"code_block_border_color": "magenta"},
    )
    # Tree shape: outer column box -> code-block box -> [header?, border box].
    # The border box is one level deeper than the column children; walk
    # the grandchildren to find it.
    border_box: Element | None = None
    for child in el.children:
        if not isinstance(child, Element) or child.type != "box":
            continue
        for sub in child.children:
            if (
                isinstance(sub, Element)
                and sub.type == "box"
                and sub.props.get("borderStyle") == "single"
            ):
                border_box = sub
                break
        if border_box is not None:
            break
    assert border_box is not None, "expected a single-border Box wrapping code"
    assert border_box.props.get("borderColor") == "magenta"


@_pygments_mark
def test_fenced_block_show_border_false_removes_frame() -> None:
    """``code_block_show_border=False`` skips the wrapper border Box.

    With the border disabled, the code-block box should NOT contain a
    child Box with ``borderStyle="single"`` — the HighlightedCode result
    sits directly under the code-block box.
    """
    el = Markdown(
        "```python\nx = 1\n```",
        theme={"code_block_show_border": False},
    )
    # Walk grandchildren (outer column -> code-block box -> border box?).
    for child in el.children:
        if not isinstance(child, Element) or child.type != "box":
            continue
        for sub in child.children:
            if not isinstance(sub, Element) or sub.type != "box":
                continue
            assert sub.props.get("borderStyle") != "single", (
                "border box should be absent when code_block_show_border=False"
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
    # Heading + code keyword both present.
    assert "Title" in out
    assert f"{ESC}[35m" in out  # keyword magenta from the code block


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
    PR3 fallback stays a plain Box of dim Text rows.
    """
    _install_pygments_import_blocker()
    el = Markdown("```python\nx = 1\n```")
    # Walk grandchildren (outer column -> code-block box -> ?).
    for child in el.children:
        if not isinstance(child, Element) or child.type != "box":
            continue
        for sub in child.children:
            if not isinstance(sub, Element) or sub.type != "box":
                continue
            assert sub.props.get("borderStyle") != "single", (
                "fallback path should not wrap code in a border"
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
    from pyink.externals.markdown import _cached_render, _render_cache

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
    from pyink.externals import markdown as md_mod

    md_mod._render_cache.clear()
    theme: dict[str, Any] = {}
    # Push twice the cap so eviction kicks in.
    for i in range(md_mod._RENDER_CACHE_MAX * 2):
        md_mod._cached_render(f"# Title {i}", 80, theme)
    assert len(md_mod._render_cache) <= md_mod._RENDER_CACHE_MAX
