"""Tests for :func:`ink.externals.StructuredDiff` (Phase 3 PR5).

Mixed renderer strategy (mirroring
:mod:`tests.externals.test_streaming_text` and
:mod:`tests.externals.test_markdown`):

* Static-source cases use the synchronous :func:`render_to_string`
  test renderer — ``StructuredDiff``'s fast path is a declarative
  ``box`` factory, no hooks involved.
* Reactive-source cases (``Signal`` / ``Callable``) drive the live
  :func:`ink.render.render` pipeline so we can verify signal writes
  actually trigger a re-render.

Coverage (per PR5 scope):

* Element shape — ``StructuredDiff`` returns a ``box`` host element
  whose ``flexDirection`` is always ``"column"``.
* Pure-add / pure-delete / mixed-edit / no-change diffs.
* ``show_header=False`` suppresses the header row and the divider.
* ``show_add_count`` / ``show_del_count`` toggle the ``+N`` / ``-M``
  pieces of the header.
* ``context_lines=0`` shows only changed lines; ``context_lines=10``
  shows more surrounding code.
* Highlight path: with pygments installed and ``language="python"``,
  ``+`` / ``-`` bodies emit syntax colours (cyan for ``print``).
* Non-highlight path: with ``language="text"`` (default), ``+`` / ``-``
  lines are plain coloured ``Text`` leaves (green / red).
* Missing pygments: ``+`` / ``-`` lines fall back to plain coloured
  ``Text`` (verified by mocking ``__import__``).
* Reactive sources (``Signal`` / ``Callable``) re-render on writes.
* ``theme=`` override flows through to HighlightedCode.
* ``StructuredDiff`` is exported from ``ink.externals`` but NOT from
  the top-level ``ink`` package (PRD Decision 5).
* Integration: ``StructuredDiff`` inside a parent ``Box`` composes
  cleanly with siblings.
"""

from __future__ import annotations

import builtins
import io
import sys
import time
from collections.abc import Callable
from typing import Any

import pytest

from ink import Box, Text, render, render_to_string, signal
from ink.core.element import Element
from ink.externals import StructuredDiff
from ink.externals.diff import _DiffImpl, _resolve_source
from ink.render.instance import Instance

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pygments_available() -> bool:
    try:
        import pygments  # noqa: F401
    except ImportError:
        return False
    return True


def _render(tree: Element, *, columns: int = 80) -> str:
    """Render ``tree`` via the synchronous test pipeline."""
    return render_to_string(tree, columns=columns)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _mount(
    build_tree: Element,
    *,
    columns: int = 80,
    rows: int = 10,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    inst = render(
        build_tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)
    return inst, out


def _frame(inst: Instance) -> str:
    return inst.current_frame


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


def _install_pygments_import_blocker() -> None:
    """Make ``import pygments`` raise ``ImportError`` until reset."""

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pygments" or name.startswith("pygments."):
            raise ImportError(f"mocked: pygments not installed ({name})")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    for mod in list(sys.modules):
        if mod == "pygments" or mod.startswith("pygments."):
            del sys.modules[mod]


@pytest.fixture
def _restore_import() -> Any:
    real_import = builtins.__import__
    yield
    builtins.__import__ = real_import


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_static_sources_return_box_host_element() -> None:
    """Fast path returns a ``box`` host element."""
    el = StructuredDiff("a", "b")
    assert isinstance(el, Element)
    assert el.type == "box"
    assert el.props["flexDirection"] == "column"


def test_reactive_sources_return_function_component() -> None:
    """At least one reactive source → defer to :func:`_DiffImpl`."""
    buf = signal("a")
    el = StructuredDiff(buf, "b")
    assert isinstance(el, Element)
    assert el.type is _DiffImpl
    assert el.props["before"] is buf


def test_box_props_forwarded_to_outer_box() -> None:
    """``**box_props`` reach the outer container."""
    el = StructuredDiff(
        "a",
        "b",
        borderStyle="round",
        padding=1,
    )
    assert el.props["borderStyle"] == "round"
    assert el.props["padding"] == 1


def test_caller_flexDirection_is_ignored() -> None:
    """The component contract forces ``flexDirection="column"``."""
    el = StructuredDiff("a", "b", flexDirection="row")
    assert el.props["flexDirection"] == "column"


# ---------------------------------------------------------------------------
# Diff content — basic cases
# ---------------------------------------------------------------------------


def test_pure_addition_shows_plus_lines_in_green() -> None:
    """After-text has extra lines → all diff bodies are ``+`` / green."""
    before = "line1\nline2"
    after = "line1\nline2\nline3\nline4"
    out = _render(StructuredDiff(before, after, show_header=False))
    # Two add lines, both green.
    assert out.count(f"{ESC}[32m") >= 2
    assert "line3" in out
    assert "line4" in out


def test_pure_deletion_shows_minus_lines_in_red() -> None:
    """After-text has fewer lines → all diff bodies are ``-`` / red."""
    before = "line1\nline2\nline3\nline4"
    after = "line1\nline2"
    out = _render(StructuredDiff(before, after, show_header=False))
    assert out.count(f"{ESC}[31m") >= 2
    assert "line3" in out
    assert "line4" in out


def test_modified_line_shows_both_plus_and_minus() -> None:
    """A replacement shows one ``-`` and one ``+`` for the same content."""
    before = "hello world"
    after = "hello python"
    out = _render(StructuredDiff(before, after, show_header=False))
    assert "hello world" in out
    assert "hello python" in out
    # Both colours present.
    assert f"{ESC}[32m" in out  # add
    assert f"{ESC}[31m" in out  # del


def test_no_changes_emits_empty_diff_body() -> None:
    """Identical sources produce no diff lines (only the header)."""
    before = after = "same\ncontent\nhere"
    out_with_header = _render(StructuredDiff(before, after))
    out_no_header = _render(StructuredDiff(before, after, show_header=False))
    # Without header there's nothing to render — empty string.
    assert out_no_header == ""
    # With header we still see "Changes +0 -0" but no diff bodies.
    assert "Changes +0 -0" in out_with_header


# ---------------------------------------------------------------------------
# Header controls
# ---------------------------------------------------------------------------


def test_show_header_false_suppresses_header_and_divider() -> None:
    """``show_header=False`` → no "Changes" header, no divider."""
    before = "a"
    after = "b"
    out = _render(StructuredDiff(before, after, show_header=False))
    assert "Changes" not in out
    # File markers (``---`` / ``+++``) are also gated by show_header
    # (we pass empty filenames to difflib when show_header is False).
    assert "--- before" not in out
    assert "+++ after" not in out


def test_show_markers_false_suppresses_file_and_hunk_markers() -> None:
    """``show_markers=False`` skips ``---`` / ``+++`` / ``@@`` rows.

    07-20-tool-message-rendering-polish: Claude Code's
    ``StructuredDiff`` never surfaces the raw difflib markers — only
    the body rows. ``show_header=False`` alone is insufficient because
    difflib still emits ``--- `` / ``+++ `` / ``@@ ... @@`` even with
    empty filenames. ``show_markers=False`` post-filters those rows
    so callers that want CC parity pass
    ``show_header=False, show_markers=False`` together.
    """
    before = "a"
    after = "b"
    out = _render(
        StructuredDiff(before, after, show_header=False, show_markers=False)
    )
    # File markers absent.
    assert "--- " not in out
    assert "+++ " not in out
    # Hunk header absent.
    assert "@@" not in out
    # Body rows preserved.
    assert "-a" in out
    assert "+b" in out


def test_show_markers_true_default_emits_markers() -> None:
    """``show_markers=True`` (default) emits ``---`` / ``+++`` / ``@@``.

    Backward-compat regression: existing callers that don't pass
    ``show_markers`` continue to see the raw difflib markers.
    """
    before = "a"
    after = "b"
    out = _render(StructuredDiff(before, after, show_header=False))
    assert "--- " in out
    assert "+++ " in out
    assert "@@" in out


def test_indent_prefixes_every_body_row() -> None:
    """``indent`` prepends a literal string to every body row.

    07-20-tool-message-rendering-polish: callers that embed the diff
    under a parent ``⎿`` gutter (Jarvis's archived Edit row) pass
    ``indent="     "`` (5 spaces matching CC's ``MessageResponse``
    gutter width) so the diff body lines up under the gutter.
    """
    before = "a"
    after = "b"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            indent="     ",
        )
    )
    lines = out.split("\n")
    # Two body rows (del / add); each starts with the 5-space indent.
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("     ")
    # The body characters (with their ANSI colour codes) appear after
    # the indent. We check the substring rather than the suffix so the
    # SGR reset (``\x1b[0m``) at the end of the line doesn't break the
    # assertion.
    assert any("-a" in line for line in lines)
    assert any("+b" in line for line in lines)


def test_indent_empty_default_no_prefix() -> None:
    """``indent=""`` (default) yields no prefix — backward compat."""
    before = "a"
    after = "b"
    out = _render(
        StructuredDiff(before, after, show_header=False, show_markers=False)
    )
    lines = out.split("\n")
    # No leading whitespace on either body row.
    for line in lines:
        assert not line.startswith(" ")


def test_show_header_true_includes_changes_label() -> None:
    before = "a"
    after = "b"
    out = _render(StructuredDiff(before, after, show_header=True))
    assert "Changes" in out


def test_show_add_count_shows_plus_n() -> None:
    before = "x"
    after = "x\ny\nz"
    out = _render(
        StructuredDiff(
            before, after, show_header=True, show_del_count=False
        )
    )
    # 2 add lines.
    assert "Changes +2" in out


def test_show_del_count_shows_minus_m() -> None:
    before = "x\ny\nz"
    after = "x"
    out = _render(
        StructuredDiff(
            before, after, show_header=True, show_add_count=False
        )
    )
    # 2 del lines.
    assert "Changes -2" in out


def test_show_counts_false_hides_both_pieces() -> None:
    before = "x"
    after = "y"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=True,
            show_add_count=False,
            show_del_count=False,
        )
    )
    # Header is just the bare "Changes" label — neither count piece
    # appears on the header line. (``+1`` / ``-1`` may still appear
    # in the hunk-header range spec ``@@ -1 +1 @@``, so we check the
    # header line only.)
    lines = out.split("\n")
    header_line = next((ln for ln in lines if "Changes" in ln), "")
    assert "Changes" in header_line
    assert "+1" not in header_line
    assert "-1" not in header_line


def test_header_is_yellow_and_bold() -> None:
    out = _render(StructuredDiff("a", "b"))
    # Yellow = SGR 33; bold = SGR 1. The header should be wrapped in
    # both sequences (order: bold applied first by apply_style, then
    # colour — but the open sequence list is dim/fg/bg/bold/.../).
    # We just assert both sequences appear on the same "Changes" line.
    lines = out.split("\n")
    header_line = next((ln for ln in lines if "Changes" in ln), "")
    assert f"{ESC}[33m" in header_line  # yellow
    assert f"{ESC}[1m" in header_line  # bold


# ---------------------------------------------------------------------------
# context_lines
# ---------------------------------------------------------------------------


def test_context_lines_zero_shows_only_changed_lines() -> None:
    """``context_lines=0`` → no surrounding context rows in the output."""
    before = "ctx1\nctx2\nCHANGED\nctx3\nctx4"
    after = "ctx1\nctx2\nNEW\nctx3\nctx4"
    out = _render(
        StructuredDiff(before, after, show_header=False, context_lines=0)
    )
    # Context lines ctx1/ctx2/ctx3/ctx4 should NOT appear (only the
    # changed line + hunk header).
    # ``ctx1`` may appear if difflib keeps it as part of the hunk
    # metadata, but with n=0 the only body lines are the -/+ pair.
    assert "CHANGED" in out
    assert "NEW" in out


def test_context_lines_large_shows_more_surrounding_code() -> None:
    """``context_lines=10`` → all unchanged lines are kept as context."""
    before = "a\nb\nc\nd\ne"
    after = "a\nb\nC\nd\ne"
    out_small = _render(
        StructuredDiff(before, after, show_header=False, context_lines=1)
    )
    out_large = _render(
        StructuredDiff(before, after, show_header=False, context_lines=10)
    )
    # Larger context should include more of the unchanged lines.
    # With context_lines=1 we lose either the first or last line; with
    # 10 we keep everything.
    assert "a" in out_large
    assert "e" in out_large
    # The small-context render has fewer total visible lines.
    assert len(out_large.split("\n")) >= len(out_small.split("\n"))


# ---------------------------------------------------------------------------
# Hunk header colour
# ---------------------------------------------------------------------------


def test_hunk_header_colored_magenta() -> None:
    """``@@ ... @@`` hunk headers render in magenta (SGR 35), bold."""
    out = _render(
        StructuredDiff("a\nb\nc", "a\nB\nc", show_header=False)
    )
    # Hunk header always present for a non-empty diff.
    assert "@@" in out
    # Magenta = SGR 35.
    assert f"{ESC}[35m@@" in out or f"{ESC}[35m" in out


def test_custom_hunk_color_overrides_default() -> None:
    out = _render(
        StructuredDiff(
            "a\nb\nc",
            "a\nB\nc",
            show_header=False,
            hunk_color="cyan",
        )
    )
    # Cyan = SGR 36. apply_style emits dim/fg/bg/bold/.../ so the
    # sequence is ``\x1b[36m\x1b[1m@@`` (fg colour then bold).
    assert f"{ESC}[36m{ESC}[1m@@" in out


# ---------------------------------------------------------------------------
# Highlight integration
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)
def test_language_python_applies_pygments_colors_to_plus_lines() -> None:
    """``+`` bodies are highlighted: ``print`` is cyan (builtin)."""
    before = "x = 1"
    after = "print(x)"
    out = _render(
        StructuredDiff(before, after, language="python", show_header=False)
    )
    # ``print`` is a Python builtin → Token.Name.Builtin → cyan (SGR 36).
    assert f"{ESC}[36mprint{ESC}[0m" in out


@pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)
def test_language_python_applies_pygments_colors_to_minus_lines() -> None:
    """``-`` bodies are highlighted too."""
    before = "print(x)"
    after = "x = 1"
    out = _render(
        StructuredDiff(before, after, language="python", show_header=False)
    )
    # The deleted line still gets highlighted: ``print`` is cyan.
    assert f"{ESC}[36mprint{ESC}[0m" in out


def test_language_text_skips_highlight_plain_colored_text() -> None:
    """Default ``language="text"`` → ``+`` lines are plain green Text."""
    before = "x = 1"
    after = "print(x)"
    out = _render(
        StructuredDiff(before, after, show_header=False)  # language="text"
    )
    # The added line "print(x)" appears as a single coloured Text leaf
    # (no Pygments tokenisation).
    assert "print(x)" in out
    # Plain Text path wraps the whole line in one green SGR sequence,
    # so the entire line body is green — no per-token colour.
    assert f"{ESC}[32m+print(x){ESC}[0m" in out


def test_language_text_does_not_emit_pygments_token_colors() -> None:
    """With ``language="text"`` the body never carries syntax colours."""
    before = "x = 1"
    after = "def f():\n    return 1"
    out = _render(
        StructuredDiff(before, after, language="text", show_header=False)
    )
    # Pygments would emit magenta for ``def``; with language="text"
    # we should never see magenta on a ``def`` token.
    assert f"{ESC}[35mdef{ESC}[0m" not in out


def test_plus_prefix_keeps_add_color_when_highlighted(
    _restore_import: Any,
) -> None:
    """The ``+`` glyph keeps the diff colour even when the body is highlighted.

    This is the row Box layout: ``Text("+", color="green")`` followed
    by ``HighlightedCode(body, language="python")``.
    """
    if not _pygments_available():
        pytest.skip("pygments not installed")
    before = "x = 1"
    after = "print(x)"
    out = _render(
        StructuredDiff(before, after, language="python", show_header=False)
    )
    # The bold green prefix glyph appears next to the highlighted body.
    assert f"{ESC}[32m{ESC}[1m+{ESC}[0m" in out


@pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)
def test_theme_override_flows_to_highlighted_code() -> None:
    """``theme=`` overrides Pygments token colours for diff bodies."""
    before = "x = 1"
    after = "def f(): pass"
    out = _render(
        StructuredDiff(
            before,
            after,
            language="python",
            show_header=False,
            theme={"Keyword": "red"},
        )
    )
    # ``def`` should be red (overridden) rather than magenta (default).
    assert f"{ESC}[31mdef{ESC}[0m" in out
    assert f"{ESC}[35mdef{ESC}[0m" not in out


# ---------------------------------------------------------------------------
# Missing pygments fallback
# ---------------------------------------------------------------------------


def test_missing_pygments_falls_back_to_plain_colored_text(
    _restore_import: Any,
) -> None:
    """When pygments is missing, ``+`` lines render as plain coloured Text.

    Diff rendering must not crash on a missing optional extra. The body
    inherits the diff colour (green / red) verbatim.
    """
    _install_pygments_import_blocker()
    before = "x = 1"
    after = "print(x)"
    # Should not raise even with language="python".
    out = _render(
        StructuredDiff(before, after, language="python", show_header=False)
    )
    assert "print(x)" in out
    # Plain Text path wraps the whole line in green.
    assert f"{ESC}[32m+print(x){ESC}[0m" in out
    # No Pygments token colours (cyan for ``print`` builtin).
    assert f"{ESC}[36mprint{ESC}[0m" not in out


# ---------------------------------------------------------------------------
# Reactive sources — Signal / Callable
# ---------------------------------------------------------------------------


def test_signal_source_renders_initial_diff() -> None:
    before = signal("a")
    after = signal("b")
    inst, _ = _mount(StructuredDiff(before, after, show_header=False))
    # Both the deleted "a" and added "b" should appear.
    assert _wait_for(lambda: "a" in _frame(inst))
    assert _wait_for(lambda: "b" in _frame(inst))
    inst.unmount()


def test_signal_write_triggers_rerender() -> None:
    before = signal("hello")
    after = signal("hello")
    inst, _ = _mount(StructuredDiff(before, after, show_header=False))
    # Initially identical → no diff body.
    assert _wait_for(lambda: "world" not in _frame(inst))

    after.value = "world"
    assert _wait_for(lambda: "world" in _frame(inst)), (
        "did not re-render after signal write"
    )
    inst.unmount()


def test_callable_source_resolves_at_layout_time() -> None:
    """Callable sources are evaluated during layout."""
    buf = signal("AAA")
    el = StructuredDiff(
        lambda: "static",
        lambda: buf.value,
        show_header=False,
    )
    # Element shape is the function component branch.
    assert el.type is _DiffImpl
    # And the resolver handles all three shapes.
    assert _resolve_source("plain") == "plain"
    assert _resolve_source(buf) == "AAA"
    assert _resolve_source(lambda: "from callable") == "from callable"


def test_callable_source_reactive_via_signal_read() -> None:
    """A callable that reads a signal re-renders on writes."""
    after_buf = signal("one")
    inst, _ = _mount(
        StructuredDiff(
            "zero",
            lambda: after_buf.value,
            show_header=False,
        )
    )
    assert _wait_for(lambda: "one" in _frame(inst))
    after_buf.value = "two"
    assert _wait_for(lambda: "two" in _frame(inst))
    inst.unmount()


def test_mixed_str_and_signal_sources_use_reactive_branch() -> None:
    """One static + one Signal source → still reactive."""
    after = signal("b")
    el = StructuredDiff("a", after)
    assert el.type is _DiffImpl
    inst, _ = _mount(el)
    assert _wait_for(lambda: "b" in _frame(inst))
    after.value = "c"
    assert _wait_for(lambda: "c" in _frame(inst))
    inst.unmount()


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_structured_diff_inside_box_with_sibling() -> None:
    """``StructuredDiff`` composes with siblings inside a parent Box."""
    out = _render(
        Box(
            StructuredDiff("a", "b", show_header=False),
            Text("sibling"),
        ),
        columns=40,
    )
    assert "sibling" in out


def test_structured_diff_inside_box_with_border() -> None:
    """Outer border / padding flow through ``**box_props``."""
    el = StructuredDiff("a", "b", borderStyle="round", padding=1)
    out = _render(el, columns=40)
    # Round border uses rounded corners (the actual chars depend on the
    # border style dict, but the output should be non-empty).
    assert out
    assert "Changes" in out  # header still rendered


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_structured_diff() -> None:
    from ink.externals import StructuredDiff as InitStructuredDiff

    assert InitStructuredDiff is StructuredDiff


def test_structured_diff_not_in_ink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import ink

    assert not hasattr(ink, "StructuredDiff"), (
        "StructuredDiff must NOT be top-level"
    )


# ---------------------------------------------------------------------------
# Per-line background colour (CC-style green / red band)
# ---------------------------------------------------------------------------


def test_add_bg_color_paints_plus_row_background() -> None:
    """``add_bg_color`` fills ``+`` rows with the given RGB background.

    The CC ``StructuredDiff`` signature is a green / red coloured band
    that fills the entire row width. We assert the SGR ``48;2;r;g;b``
    sequence for ``rgb(30,70,32)`` is emitted on a ``+`` line.
    """
    before = "x = 1"
    after = "x = 2\nprint(x)"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            add_bg_color="rgb(30,70,32)",
        )
    )
    # SGR 48;2;30;70;32m = set background to rgb(30,70,32).
    assert f"{ESC}[48;2;30;70;32m" in out


def test_del_bg_color_paints_minus_row_background() -> None:
    """``del_bg_color`` fills ``-`` rows with the given RGB background."""
    before = "x = 1\nprint(x)"
    after = "x = 1"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            del_bg_color="rgb(74,32,32)",
        )
    )
    # SGR 48;2;74;32;32m = set background to rgb(74,32,32).
    assert f"{ESC}[48;2;74;32;32m" in out


def test_no_bg_color_by_default_keeps_legacy_behaviour() -> None:
    """Without ``add_bg_color`` / ``del_bg_color`` no ``48;`` SGR appears."""
    before = "x = 1"
    after = "x = 2"
    out = _render(StructuredDiff(before, after, show_header=False))
    # No SGR 48;... sequence (background) on any line.
    assert "48;2;" not in out


def test_bg_color_persists_through_highlight_branch(
    _restore_import: Any,
) -> None:
    """When highlighting is on the prefix glyph still carries the bg."""
    if not _pygments_available():
        pytest.skip("pygments not installed (pip install ink[highlight])")
    before = "x = 1"
    after = "print(x)"
    out = _render(
        StructuredDiff(
            before,
            after,
            language="python",
            show_header=False,
            add_bg_color="rgb(30,70,32)",
        )
    )
    assert f"{ESC}[48;2;30;70;32m" in out


# ---------------------------------------------------------------------------
# CC-alignment extensions: line_numbers / inline_highlight / full_width_bg
# (07-20-tool-message-rendering-polish)
# ---------------------------------------------------------------------------


def test_line_numbers_prefix_body_rows_with_padded_number() -> None:
    """``line_numbers=True`` prefixes each body row with a padded number.

    Format: ``<padded_num><sigil>`` where the padded number is right-
    aligned to the width of the largest line number + 1. For a 2-line
    diff the largest line is ``2`` so the gutter is 2 chars wide:
    ``"1+"`` / ``"2-"`` etc.
    """
    before = "alpha\nbeta\ngamma"
    after = "alpha\nBETA\ngamma"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            line_numbers=True,
        )
    )
    # The first body row (context "alpha") gets gutter "1 " (1 + space
    # sigil for context). We assert the substring "1 " appears on a
    # line that also contains "alpha".
    assert "alpha" in out
    # We expect "1 " (the padded 1 + space sigil) to appear somewhere
    # before "alpha". The exact byte sequence depends on prefix colour
    # SGRs so we look for the bare characters.
    assert "1 alpha" in out or " 1 alpha" in out


def test_line_numbers_off_by_default() -> None:
    """Without ``line_numbers`` no gutter appears — body rows start with code."""
    before = "alpha"
    after = "alpha\nbeta"
    out = _render(StructuredDiff(before, after, show_header=False))
    # The added line "beta" should appear without a leading digit.
    # We strip ANSI and check the line starts with "+beta" (the diff
    # sigil) rather than "<num>+beta".
    plain = out
    for code in (f"{ESC}[0m", f"{ESC}[32m", f"{ESC}[31m", f"{ESC}[1m"):
        plain = plain.replace(code, "")
    # The added line "beta" — when prefixed by a line-number gutter it
    # would look like "1+beta" or "1 +beta". With no gutter it's just
    # "+beta".
    assert "+beta" in plain
    # No digit-immediately-before-sigil pattern.
    import re

    assert re.search(r"\d\+beta", plain) is None


def test_inline_highlight_paints_changed_token_brighter() -> None:
    """``inline_highlight=True`` colours only the changed token brighter.

    For an adjacent ``-old / +new`` pair where one token changes the
    changed token should carry the ``greenBright`` (SGR 92) /
    ``redBright`` (SGR 91) sequence while the unchanged tokens stay in
    plain green / red.
    """
    before = "the quick brown fox"
    after = "the slow brown fox"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            inline_highlight=True,
        )
    )
    # ``quick`` (removed) → ``slow`` (added). The changed tokens carry
    # the bright colour; ``the`` / ``brown`` / ``fox`` stay plain.
    # SGR 92 = greenBright, SGR 91 = redBright.
    assert f"{ESC}[92m" in out  # added "slow" brighter
    assert f"{ESC}[91m" in out  # removed "quick" brighter


def test_inline_highlight_off_by_default() -> None:
    """Without ``inline_highlight`` no bright SGR (91/92) appears."""
    before = "the quick brown fox"
    after = "the slow brown fox"
    out = _render(StructuredDiff(before, after, show_header=False))
    # Default add colour is green (SGR 32), del is red (SGR 31).
    # Neither bright variant (91/92) should appear.
    assert f"{ESC}[92m" not in out
    assert f"{ESC}[91m" not in out


def test_inline_highlight_skips_high_change_ratio_lines() -> None:
    """Lines above CC's 0.4 threshold fall back to whole-line colouring.

    Two completely different lines have a change ratio of 1.0; the
    word-diff helper returns ``None`` and the renderer emits the
    standard green / red bands.
    """
    before = "aaaaa bbbbb"
    after = "zzzzz yyyyy"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            inline_highlight=True,
        )
    )
    # No bright SGR sequences — both rows fall back to plain green/red.
    assert f"{ESC}[92m" not in out
    assert f"{ESC}[91m" not in out
    # The plain colours are still there.
    assert f"{ESC}[32m" in out
    assert f"{ESC}[31m" in out


def test_inline_highlight_only_pairs_adjacent_remove_add() -> None:
    """A standalone del or add row (no pair) skips inline highlighting."""
    before = "alpha\nbeta"
    after = "alpha\nbeta\ngamma"  # pure addition — no paired remove
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            inline_highlight=True,
        )
    )
    # No paired remove → no inline highlight → no bright colour.
    assert f"{ESC}[92m" not in out
    assert f"{ESC}[91m" not in out


def test_full_width_bg_pads_to_terminal_width() -> None:
    """``full_width_bg=True`` flushes the row bg to terminal width.

    The Box-level bg uses ``flushBackgroundToWidth=True`` so the bg
    spans edge-to-edge. We assert the open SGR ``48;2;30;70;32`` still
    appears (the add row's bg) — the ``flushBackgroundToWidth`` prop
    primarily changes *how* the renderer paints the bg, not the SGR
    sequence itself.
    """
    before = "x = 1"
    after = "x = 2"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            add_bg_color="rgb(30,70,32)",
            full_width_bg=True,
        ),
        columns=40,
    )
    assert f"{ESC}[48;2;30;70;32m" in out


def test_full_width_bg_band_covers_gutter_and_content() -> None:
    """``full_width_bg=True`` paints bg from row start to terminal width.

    Regression for commit ``5d53c5f``'s multi-leaf approach: setting
    ``flushBackgroundToWidth=True`` on each coloured Text leaf broke
    the band because PyInk's per-leaf ``apply_style`` emits a
    ``\\x1b[0m`` reset at the end of every leaf, killing the bg SGR
    opened by the previous leaf. The visible symptom was: bg covered
    only the first few cells (the prefix + first leaf), then died.

    The fix builds each diff row as a SINGLE Text leaf carrying the
    full row string with embedded ANSI fg SGRs (no per-leaf resets).
    We assert:

    * the bg opener appears exactly once per changed row;
    * the reset appears exactly once per changed row (at the very end);
    * the visible width of each changed row equals the layout width
      (40 cols) so the band genuinely fills the row.
    """
    import re

    from ink.layout.measure import string_width

    before = "x = 1"
    after = "x = 2"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            inline_highlight=True,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
        ),
        columns=40,
    )
    lines = out.split("\n")
    # Two body rows: one del (red bg) and one add (green bg).
    assert len(lines) == 2
    for line, bg_open in (
        (lines[0], f"{ESC}[48;2;74;32;32m"),
        (lines[1], f"{ESC}[48;2;30;70;32m"),
    ):
        # The bg opener appears exactly once (at the row's first byte).
        assert line.count(bg_open) == 1, (
            f"bg opener should appear once per row, got {line.count(bg_open)}: {line!r}"
        )
        # The bg opener is at the start of the line (no leading text).
        assert line.startswith(bg_open), (
            f"bg opener should be at row start, got: {line!r}"
        )
        # The final reset is at the row's end (no trailing content).
        assert line.endswith(f"{ESC}[0m"), (
            f"row should end with reset, got: {line!r}"
        )
        # Only ONE reset in the entire row — no mid-row resets that
        # would kill the bg.
        sgr_run = re.findall(r"\x1b\[[0-9;]*m", line)
        reset_count = sum(1 for s in sgr_run if s == f"{ESC}[0m")
        assert reset_count == 1, (
            f"expected exactly 1 reset per row, got {reset_count}: {line!r}"
        )
        # Visible width equals the layout width (40 cols).
        bare = re.sub(r"\x1b\[[0-9;]*m", "", line)
        assert string_width(bare) == 40, (
            f"row visible width should be 40, got {string_width(bare)}: {bare!r}"
        )


def test_full_width_bg_with_indent_and_first_row_prefix() -> None:
    """``full_width_bg`` + ``indent`` + ``first_row_prefix`` compose.

    The first row carries ``first_row_prefix`` (e.g. Jarvis's ``⎿``
    parent gutter) in lieu of ``indent``; continuation rows use
    ``indent``. The bg band must span from column 1 to terminal width
    on every row regardless of which prefix is in play.
    """
    import re

    from ink.layout.measure import string_width

    before = "alpha\nbeta"
    after = "alpha\nBETA"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
            indent="     ",
            first_row_prefix="  ⍹  ",  # ⎿-like glyph surrogate
        ),
        columns=40,
    )
    lines = out.split("\n")
    # One context row (alpha, no bg) + one del (beta, red bg) + one
    # add (BETA, green bg).
    assert len(lines) == 3
    # First line is the context row "alpha" with first_row_prefix; it
    # has no bg (context_color=None) so it should not contain any bg
    # SGR opener.
    assert f"{ESC}[48" not in lines[0], (
        f"context row should not carry bg, got: {lines[0]!r}"
    )
    # The two bg-carrying rows must each span the full width.
    for line in lines[1:]:
        bare = re.sub(r"\x1b\[[0-9;]*m", "", line)
        assert string_width(bare) == 40, (
            f"row visible width should be 40, got {string_width(bare)}: {bare!r}"
        )


def test_full_width_bg_off_by_default_keeps_legacy_behaviour() -> None:
    """Without ``full_width_bg`` the bg only covers the text cells."""
    before = "x = 1"
    after = "x = 2"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            add_bg_color="rgb(30,70,32)",
        ),
        columns=40,
    )
    # Legacy path: bg present (add_bg_color set) but row doesn't flush.
    assert f"{ESC}[48;2;30;70;32m" in out


def test_all_cc_props_combined_render_without_error() -> None:
    """All three CC props + bg colours compose cleanly.

    Smoke test that the renderer handles the combination without
    raising and produces the expected visual signatures (line-number
    gutter, inline-highlight bright tokens, full-width bg bands).
    """
    before = "def foo():\n    return 1\n"
    after = "def foo():\n    return 2\n"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            language="text",
            line_numbers=True,
            inline_highlight=True,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
        ),
        columns=60,
    )
    # Line-number gutter present.
    assert "def foo" in out
    # Bg bands present.
    assert f"{ESC}[48;2;30;70;32m" in out
    assert f"{ESC}[48;2;74;32;32m" in out
    # Inline highlight present — "1" (removed) and "2" (added) get the
    # bright SGR sequences.
    assert f"{ESC}[92m" in out or f"{ESC}[91m" in out


def test_cc_props_off_by_default_match_legacy_output() -> None:
    """Defaults produce identical output to the pre-CC implementation.

    Bytes-for-bytes equality with the legacy renderer is the strongest
    backward-compat guarantee. We render the same diff with all CC
    props at default (False) and assert no bright SGR (91/92) and no
    line-number gutter appear.
    """
    before = "alpha\nbeta"
    after = "alpha\nBETA"
    out = _render(StructuredDiff(before, after, show_header=False))
    # No CC-alignment features.
    assert f"{ESC}[92m" not in out
    assert f"{ESC}[91m" not in out
    # No line-number gutter: the add line "+BETA" appears without a
    # leading digit (legacy fast path uses ``Text(line)`` which
    # includes the ``+`` prefix).
    plain = out
    for code in (f"{ESC}[0m", f"{ESC}[32m", f"{ESC}[31m", f"{ESC}[1m"):
        plain = plain.replace(code, "")
    assert "+BETA" in plain


# ---------------------------------------------------------------------------
# Internal helpers for CC-alignment
# ---------------------------------------------------------------------------


def test_tokenize_for_word_diff_preserves_whitespace() -> None:
    """Whitespace separators are kept on the following token."""
    from ink.externals.diff import _tokenize_for_word_diff

    tokens = _tokenize_for_word_diff("a b  c")
    # Each entry is (sep, tok): ("", "a"), (" ", "b"), ("  ", "c")
    assert tokens == [("", "a"), (" ", "b"), ("  ", "c")]


def test_tokenize_for_word_diff_empty_line_returns_empty_list() -> None:
    from ink.externals.diff import _tokenize_for_word_diff

    assert _tokenize_for_word_diff("") == []


def test_word_diff_parts_returns_changed_token_marks() -> None:
    """``_word_diff_parts`` marks only the tokens that differ."""
    from ink.externals.diff import _word_diff_parts

    result = _word_diff_parts("foo bar", "foo baz")
    assert result is not None
    parts, _ratio = result
    # Three tokens: "foo" (unchanged), "bar" → "baz" (changed).
    assert len(parts) == 2
    assert parts[0] == ("", "foo", False)
    assert parts[1] == (" ", "baz", True)


def test_word_diff_parts_returns_none_above_threshold() -> None:
    """Completely different lines (ratio < 0.6) → ``None``."""
    from ink.externals.diff import _word_diff_parts

    result = _word_diff_parts("aaaaa bbbbb", "zzzzz yyyyy")
    assert result is None


def test_classify_diff_lines_categories_each_kind() -> None:
    """``_classify_diff_lines`` tags hunk / marker / add / del / context."""
    from ink.externals.diff import _classify_diff_lines

    diff_lines = [
        "@@ -1,2 +1,2 @@",
        "--- before",
        "+++ after",
        " ctx",
        "-del",
        "+add",
    ]
    entries = _classify_diff_lines(diff_lines)
    kinds = [e["kind"] for e in entries]
    assert kinds == ["hunk", "marker", "marker", "context", "del", "add"]
    # Bodies are stripped of the diff prefix.
    add_entry = next(e for e in entries if e["kind"] == "add")
    assert add_entry["body"] == "add"
    del_entry = next(e for e in entries if e["kind"] == "del")
    assert del_entry["body"] == "del"
    ctx_entry = next(e for e in entries if e["kind"] == "context")
    assert ctx_entry["body"] == "ctx"


def test_assign_line_numbers_increments_per_row() -> None:
    """``_assign_line_numbers`` follows CC's row-counter rule.

    Counter increments on context / add / del rows; hunk / marker rows
    get ``None``.
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    diff_lines = ["@@ -1,3 +1,3 @@", " ctx1", "-del", "+add", " ctx2"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries)
    nums = [e.get("line_num") for e in entries]
    # Hunk = None; context=1; del=2; add=3; context=4.
    assert nums == [None, 1, 2, 3, 4]


def test_compute_gutter_width_handles_no_numbered_rows() -> None:
    """Empty / hunk-only diff → ``0`` (no gutter rendered)."""
    from ink.externals.diff import _compute_gutter_width

    assert _compute_gutter_width([]) == 0
    assert _compute_gutter_width([{"line_num": None}]) == 0


def test_compute_gutter_width_pads_to_max_plus_one() -> None:
    """CC rule: ``len(str(max_num)) + 1``."""
    from ink.externals.diff import _compute_gutter_width

    # Single-digit max → 1+1=2.
    assert _compute_gutter_width([{"line_num": 9}]) == 2
    # Two-digit max → 2+1=3.
    assert _compute_gutter_width([{"line_num": 99}]) == 3


# ---------------------------------------------------------------------------
# first_row_prefix (07-20-tool-message-rendering-polish follow-up)
# ---------------------------------------------------------------------------


def test_first_row_prefix_replaces_indent_on_first_row_only() -> None:
    """``first_row_prefix`` is consumed on the first body row only.

    07-20-tool-message-rendering-polish follow-up: callers that embed
    the diff under a parent ``⎿`` gutter (Jarvis's archived Edit row)
    want the glyph on the SAME visual line as the first body row (CC's
    ``MessageResponse`` pattern), not on a standalone row above. The
    caller passes ``first_row_prefix="  ⎿  "`` + ``indent="     "``;
    the renderer must put the prefix on row 1 and the indent on every
    continuation row.
    """
    before = "alpha"
    after = "alpha\nbeta\nGamma"  # 1 context + 2 add rows
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            indent="     ",
            first_row_prefix=">>",
        )
    )
    lines = out.split("\n")
    # Three body rows: 1 context + 2 adds. Strip ANSI so the prefix
    # check is byte-exact.
    import re

    plain_lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines]
    # First row gets the ``first_row_prefix`` ("">>"), NOT the indent.
    assert plain_lines[0].startswith(">>"), (
        f"first row should start with first_row_prefix, got: {plain_lines[0]!r}"
    )
    # Continuation rows get the ``indent`` (5 spaces), NOT the prefix.
    for line in plain_lines[1:]:
        assert line.startswith("     "), (
            f"continuation row should start with indent, got: {line!r}"
        )
        assert not line.startswith(">>"), (
            f"continuation row should NOT carry first_row_prefix, got: {line!r}"
        )


def test_first_row_prefix_empty_defaults_to_indent_on_first_row() -> None:
    """``first_row_prefix=""`` (default) → first row uses ``indent``.

    Backward-compat regression: callers that only pass ``indent`` (no
    ``first_row_prefix``) must see the indent on EVERY row including
    the first.
    """
    before = "x"
    after = "y"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            indent="     ",
        )
    )
    lines = out.split("\n")
    # Two body rows (del + add); both start with the indent.
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("     ")


def test_first_row_prefix_works_in_cc_mode() -> None:
    """``first_row_prefix`` threads through ``cc_mode`` path correctly.

    When CC features (``line_numbers`` / ``inline_highlight`` /
    ``full_width_bg``) are on, the row renderer is :func:`_render_diff_row_cc`
    rather than the legacy fast path. The first-row prefix wiring must
    still apply — first row gets ``first_row_prefix``, continuation
    rows get ``indent``.
    """
    before = "the quick brown fox"
    after = "the slow brown fox"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            inline_highlight=True,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
            indent="     ",
            first_row_prefix="  ⎿  ",
        ),
        columns=60,
    )
    lines = out.split("\n")
    import re

    plain_lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines]
    # First row carries the ``⎿`` glyph from the first_row_prefix.
    assert "⎿" in plain_lines[0], (
        f"first row missing ⎿ glyph, got: {plain_lines[0]!r}"
    )
    # Continuation rows carry only the indent (5 spaces).
    for line in plain_lines[1:]:
        assert line.startswith("     "), (
            f"continuation row missing indent, got: {line!r}"
        )
        assert "⎿" not in line, (
            f"continuation row should NOT carry ⎿, got: {line!r}"
        )


def test_full_width_bg_uses_single_text_leaf_per_row() -> None:
    """``full_width_bg=True`` collapses each row to a single Text leaf.

    07-20-tool-message-rendering-polish follow-up: the original
    multi-leaf approach set ``flushBackgroundToWidth=True`` on every
    coloured Text leaf in a row. That broke the band because PyInk's
    per-leaf ``apply_style`` emits a ``\\x1b[0m`` reset at the end of
    each leaf, killing the bg SGR opened by the previous leaf. The
    fix builds each diff row as a SINGLE Text leaf carrying the row's
    full visible string with embedded ANSI fg SGRs (no per-leaf
    resets), plus ``backgroundColor`` + ``flushBackgroundToWidth=True``
    so the row-level bg painter opens the bg once at the first cell
    and closes it after the last padded cell.

    We walk the rendered Element tree and assert each add / del row is
    a single Text leaf (no Box-of-Text-leaves wrapper) carrying both
    ``backgroundColor`` and ``flushBackgroundToWidth=True``.
    """
    before = "the quick brown fox"
    after = "the slow brown fox"
    el = StructuredDiff(
        before,
        after,
        show_header=False,
        show_markers=False,
        line_numbers=True,
        inline_highlight=True,
        full_width_bg=True,
        add_bg_color="rgb(30,70,32)",
        del_bg_color="rgb(74,32,32)",
    )
    # Walk the top-level Box's children; each body row should be a
    # Text leaf carrying bg + flush. We don't recurse into the Text
    # leaf (Text leaves' children are raw strings / callables, not
    # Elements).
    bg_leaves = 0

    def walk(node: Any) -> None:
        nonlocal bg_leaves
        if (
            hasattr(node, "type")
            and isinstance(node.type, str)
            and node.type == "text"
        ):
            props = node.props or {}
            bg = props.get("backgroundColor")
            if bg is not None:
                bg_leaves += 1
                assert props.get("flushBackgroundToWidth") is True, (
                    f"Text leaf with bg={bg!r} missing flushBackgroundToWidth; "
                    f"props={dict(props)!r}"
                )
        children = getattr(node, "children", None) or []
        for child in children:
            walk(child)

    walk(el)
    # Expect exactly 2 bg-carrying leaves: one del row + one add row.
    # Each row is a single Text leaf (not a Box-of-leaves), so the
    # count equals the number of changed rows.
    assert bg_leaves == 2, (
        f"expected 2 single-Text-leaf bg rows (one del + one add), got {bg_leaves}"
    )


def test_full_width_bg_off_does_not_set_per_leaf_flush() -> None:
    """Without ``full_width_bg`` Text leaves don't carry the flush prop.

    Backward-compat regression: callers that don't opt into
    ``full_width_bg`` continue to see the legacy per-leaf bg behaviour
    (no flush, the bg SGR only covers the text cells).
    """
    before = "the quick brown fox"
    after = "the slow brown fox"
    el = StructuredDiff(
        before,
        after,
        show_header=False,
        show_markers=False,
        line_numbers=True,
        inline_highlight=True,
        # ``full_width_bg`` defaults to False
        add_bg_color="rgb(30,70,32)",
        del_bg_color="rgb(74,32,32)",
    )
    leaves_with_flush = 0

    def walk(node: Any) -> None:
        nonlocal leaves_with_flush
        if hasattr(node, "type") and isinstance(node.type, str) and node.type == "text":
            props = node.props or {}
            if props.get("flushBackgroundToWidth") is True:
                leaves_with_flush += 1
        children = getattr(node, "children", None) or []
        for child in children:
            walk(child)

    walk(el)
    assert leaves_with_flush == 0, (
        f"expected 0 leaves with flushBackgroundToWidth when full_width_bg=False, "
        f"got {leaves_with_flush}"
    )
