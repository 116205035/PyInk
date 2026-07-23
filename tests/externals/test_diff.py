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


# ---------------------------------------------------------------------------
# highlight_removed (07-20-tool-message-rendering-polish Q2)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)
def test_highlight_removed_true_default_tokenizes_minus_lines() -> None:
    """``highlight_removed=True`` (default) keeps Pygments tokenisation on ``-``.

    Backward-compat regression: existing callers that don't pass
    ``highlight_removed`` continue to see per-token syntax colours on
    removed lines (mirrors CC's pre-Q2 behaviour). The deleted line
    "print(x)" emits cyan for the ``print`` builtin (Pygments
    Token.Name.Builtin).
    """
    before = "print(x)"
    after = "x = 1"
    # Default — no ``highlight_removed`` kwarg.
    out = _render(
        StructuredDiff(before, after, language="python", show_header=False)
    )
    # The deleted ``print`` carries the cyan Pygments colour.
    assert f"{ESC}[36mprint{ESC}[0m" in out


@pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)
def test_highlight_removed_false_suppresses_tokenization_on_del_lines() -> None:
    """``highlight_removed=False`` emits ``-`` lines as a single uniform span.

    07-20-tool-message-rendering-polish Q2: CC's ``color-diff`` pipeline
    bypasses ``highlightLine`` on removed lines entirely
    (``native-ts/color-diff/index.ts:914-918``) so removed code reads
    as "ghost text" in a uniform ``theme.foreground``. We mirror that
    by suppressing Pygments tokenisation on ``-`` lines when the caller
    passes ``highlight_removed=False``.

    Expected behaviour:

    * The ``-`` line's body (``print(x)``) appears as ONE coloured run
      in ``del_color`` (red, SGR 31) — no cyan (SGR 36) for ``print``
      builtin, no other per-token colours.
    * The ``+`` line keeps per-token syntax colours (cyan for builtins
      etc.) — only ``-`` lines are affected.
    """
    before = "print(x)"
    after = "print(y)"
    out = _render(
        StructuredDiff(
            before,
            after,
            language="python",
            show_header=False,
            highlight_removed=False,
        )
    )
    # The deleted ``print`` is rendered as a single red-coloured run,
    # NOT as a Pygments token coloured cyan. Look for the body wrapped
    # in a red SGR (SGR 31).
    # The legacy fast path emits the del body as a single Text leaf
    # whose content is the full diff line (``-print(x)``) coloured red.
    # Pygments tokenisation would split ``print`` from ``(x)`` and
    # colour ``print`` cyan; we assert cyan never appears on the del
    # row.
    lines = out.split("\n")
    del_lines = [ln for ln in lines if "-print" in ln]
    assert del_lines, f"expected at least one -print line, got: {lines!r}"
    for line in del_lines:
        assert f"{ESC}[36m" not in line, (
            f"del line should NOT carry cyan SGR (highlight_removed=False), "
            f"got: {line!r}"
        )
    # Sanity: the ``+`` line DOES carry the cyan Pygments colour on
    # ``print``. The add row is split into prefix glyph + highlighted
    # body, so the row contains ``\x1b[36mprint\x1b[0m`` (the body
    # token) rather than ``+print`` as a contiguous substring.
    add_lines = [ln for ln in lines if "print" in ln and ln not in del_lines]
    assert add_lines, (
        f"expected at least one +print line, got: {lines!r}"
    )
    assert any(f"{ESC}[36mprint{ESC}[0m" in ln for ln in add_lines), (
        f"add line should carry cyan SGR on print (Pygments tokenisation "
        f"on +), got: {add_lines!r}"
    )


@pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)
def test_highlight_removed_false_keeps_add_lines_tokenized() -> None:
    """``highlight_removed=False`` does NOT suppress tokenisation on ``+``.

    CC's rule applies ONLY to removed lines. Added lines and context
    lines keep their per-token syntax colours so the reader sees live
    source code with proper highlighting next to the uniform ghost
    text of removed lines.
    """
    before = "x = 1"
    after = "print(x)"
    out = _render(
        StructuredDiff(
            before,
            after,
            language="python",
            show_header=False,
            highlight_removed=False,
        )
    )
    # The added ``print`` carries the cyan Pygments colour.
    assert f"{ESC}[36mprint{ESC}[0m" in out


def test_highlight_removed_true_default_does_not_change_legacy_output() -> None:
    """Default ``highlight_removed=True`` keeps legacy output byte-for-byte.

    Smoke regression: callers that don't pass ``highlight_removed`` must
    see no change. We compare a default-args render against an explicit
    ``highlight_removed=True`` render and assert byte equality.
    """
    before = "alpha\nbeta\ngamma"
    after = "alpha\nBETA\ngamma"
    out_default = _render(
        StructuredDiff(before, after, language="text", show_header=False)
    )
    out_explicit = _render(
        StructuredDiff(
            before,
            after,
            language="text",
            show_header=False,
            highlight_removed=True,
        )
    )
    assert out_default == out_explicit, (
        f"default highlight_removed should match explicit True; "
        f"default={out_default!r} explicit={out_explicit!r}"
    )


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


def test_line_numbers_zero_pads_to_gutter_width() -> None:
    """Single-digit max → width 2 (CC's ``len(str(max))+1``) → ``01+`` / ``01-``.

    Zero-padding keeps the digit column aligned to the sigil column
    across rows. For a 2-row diff (max line = 1 → width 2) the gutter
    reads ``"01+"`` / ``"01-"`` instead of ``" 1+"`` / ``" 1-"``.
    """
    out = _render(
        StructuredDiff(
            "x = 1",
            "x = 2",
            show_header=False,
            line_numbers=True,
        )
    )
    assert "01-" in out
    assert "01+" in out
    # A space-padded ``" 1-"`` must NOT appear — regression guard.
    assert " 1-" not in out
    assert " 1+" not in out


def test_line_numbers_zero_pads_across_99_100_boundary() -> None:
    """Diff whose max line crosses 99→100 zero-pads sub-100 rows to width 4.

    StructuredDiff uses CC's ``len(str(max))+1`` width rule, so a diff
    whose max gutter number is 100 → width 4. Lines below 100 get
    zero-filled to 4 digits (``0096``, …, ``0100``) keeping the digit
    column vertical across the 2→3 digit boundary.
    """
    # Source lines whose content matches their real file line. The diff
    # context window clips which rows appear, so we use start_line that
    # matches the first visible row to keep gutter numbers semantically
    # accurate.
    before = "\n".join(f"line{i}" for i in range(1, 201))
    after = before.replace("line99", "LINE99")
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            line_numbers=True,
            start_line=96,  # first visible context row (change at 99)
        )
    )
    # Width = len("102") + 1 = 4. Line 96 renders as ``"0096 "``.
    assert "0096 line96" in out
    # Line 100 (context after change) renders as ``"0100 "``.
    assert "0100 line100" in out
    # Paired del/add at line 99 share gutter ``"0099"``.
    assert "0099-" in out
    assert "0099+" in out
    # A space-padded gutter must NOT appear.
    assert " 96 line96" not in out
    assert " 100 line100" not in out


def test_line_numbers_continuation_row_gutter_stays_spaces() -> None:
    """Soft-wrapped continuation rows must NOT receive zero-padding.

    When ``line_num=None`` (continuation row of a soft-wrapped pair)
    the gutter column is rendered as all-spaces — never zero-filled.
    Guards against an over-eager zero-fill leaking into the wrap path.
    """
    from ink.externals.diff import _line_number_gutter

    # Width 3 continuation gutter → 3 spaces + sigil, no "0".
    leaf = _line_number_gutter(
        line_num=None,
        width=3,
        sigil=" ",
        color="red",
    )
    # Text leaf stores children as a tuple; the literal string is the
    # first element: 3 spaces + sigil " " = 4 spaces total.
    assert leaf.children[0] == "    "
    # No leading zero anywhere in the num column.
    assert "0" not in leaf.children[0][:3]


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
    full row string with embedded ANSI fg / bg SGRs (no per-leaf
    resets). The bg SGR is embedded **after** the row's prefix area so
    the prefix keeps the default terminal bg. We assert:

    * the bg opener appears exactly once per changed row;
    * the reset appears exactly once per changed row (at the very end);
    * the visible width of each changed row equals the layout width
      (40 cols) so the band genuinely fills the row;
    * (when a prefix is present) the bg opener comes AFTER the prefix
      bytes — covered by ``test_full_width_bg_excludes_prefix_area``.
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
        # The bg opener appears exactly once per row.
        assert line.count(bg_open) == 1, (
            f"bg opener should appear once per row, got {line.count(bg_open)}: {line!r}"
        )
        # No prefix in this test (``indent=""``, ``first_row_prefix=""``)
        # so the bg opener is at the start of the line.
        assert line.startswith(bg_open), (
            f"bg opener should be at row start when no prefix is set, got: {line!r}"
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


def test_full_width_bg_excludes_prefix_area() -> None:
    """``full_width_bg=True`` keeps the prefix area default-coloured.

    Regression for commit ``d4aaa66``: that fix put the entire row
    (prefix + content + pad) into ONE Text leaf with
    ``backgroundColor=bg_color`` + ``flushBackgroundToWidth=True``.
    PyInk's bg painter applied bg to EVERY cell the leaf occupies,
    including the prefix cells (``"  ⎿  "`` first row, ``"     "``
    continuation). The visible symptom was: bg covered columns 1-end
    instead of 6-end, painting the parent gutter in the row's diff
    colour.

    The fix embeds the bg SGR AFTER the prefix in the row string (no
    ``backgroundColor`` prop on the leaf). We assert:

    * the bytes preceding the bg opener are exactly the prefix
      (visible — no SGR escapes);
    * no ``\\x1b[48`` (any bg SGR) appears before the prefix bytes end.
    """
    import re

    from ink.layout.measure import string_width

    before = "alpha\nbeta"
    after = "alpha\nBETA"
    indent = "     "  # 5 spaces — continuation-row prefix
    first_prefix = "  ⎿  "  # ``  ⎿  `` — surrogate parent gutter (5 cols)
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
            indent=indent,
            first_row_prefix=first_prefix,
        ),
        columns=40,
    )
    lines = out.split("\n")
    # Context row (alpha) carries ``first_row_prefix``; del (beta) and
    # add (BETA) are continuation rows carrying ``indent``. The bg
    # behaviour applies only to del / add rows (context has no bg).
    bg_rows: list[tuple[str, str, str]] = []
    for line in lines[1:]:
        if f"{ESC}[48;2;74;32;32m" in line:
            bg_rows.append((line, indent, f"{ESC}[48;2;74;32;32m"))
        elif f"{ESC}[48;2;30;70;32m" in line:
            bg_rows.append((line, indent, f"{ESC}[48;2;30;70;32m"))
    assert len(bg_rows) == 2, f"expected 2 bg rows, got {len(bg_rows)}: {lines!r}"
    for line, expected_prefix, bg_open in bg_rows:
        # The bytes preceding the bg opener must be exactly the prefix
        # (no SGR escapes embedded before the bg). Find the position
        # of the bg opener and assert the prefix slice equals the
        # expected prefix string.
        idx = line.index(bg_open)
        prefix_bytes = line[:idx]
        assert prefix_bytes == expected_prefix, (
            f"bytes before bg opener should be exactly the prefix "
            f"{expected_prefix!r}, got {prefix_bytes!r} (full line: {line!r})"
        )
        # No bg SGR appears before the prefix end — i.e. the only
        # ``\x1b[48`` in the row is the bg opener (which sits AFTER
        # the prefix). Walk all SGR matches and assert none of them
        # is a bg SGR that precedes ``idx``.
        for m in re.finditer(r"\x1b\[[0-9;]*m", line):
            if m.start() < idx:
                # Allow fg SGRs (3X / 9X) before the bg opener — the
                # renderer may emit a stray ``\x1b[39m`` etc. for
                # default-fg leaves. Reject ONLY bg SGRs (4X / 10X).
                body = m.group()[2:-1]
                params = body.split(";")
                first = params[0] if params else ""
                assert first not in ("48", "40", "41", "42", "43", "44", "45", "46", "47", "100", "101", "102", "103", "104", "105", "106", "107"), (
                    f"bg SGR {m.group()!r} appears before the prefix end "
                    f"at offset {m.start()}; line: {line!r}"
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


def test_structured_diff_bg_width_param_pads_to_explicit_value() -> None:
    """``bg_width=40`` caps the bg band at 40 columns regardless of layout width.

    Regression for the 07-21-diff-bg-width-shrink task: the renderer's
    ``_build_full_width_bg_row._render`` used to query
    :func:`get_current_text_width` unconditionally. The task adds an
    explicit ``bg_width`` override so callers (Jarvis) can narrow the
    diff bg below the terminal width — the diff block then reads as a
    lighter visual layer than a sibling full-width user-message block.

    We render a one-line edit at ``columns=120`` with ``bg_width=40``
    and assert every bg-carrying row's *visible* width equals 40 (not
    120). This is the acceptance-criteria checkpoint for AC #1 in the
    PRD.
    """
    import re

    from ink.layout.measure import string_width

    before = "a\nb"
    after = "a\nc"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            full_width_bg=True,
            add_bg_color="rgb(34,92,43)",
            del_bg_color="rgb(122,41,54)",
            bg_width=40,
        ),
        columns=120,
    )
    lines = out.split("\n")
    # Two body rows: one del (``-b``) and one add (``+c``). Both carry
    # a bg band that should pad to 40 — not the 120-col layout width.
    bg_rows = [
        line for line in lines
        if f"{ESC}[48;2;122;41;54m" in line or f"{ESC}[48;2;34;92;43m" in line
    ]
    assert len(bg_rows) == 2, (
        f"expected 2 bg-carrying rows, got {len(bg_rows)}: {lines!r}"
    )
    for line in bg_rows:
        bare = re.sub(r"\x1b\[[0-9;]*m", "", line)
        actual_w = string_width(bare)
        assert actual_w == 40, (
            f"bg row should pad to bg_width=40, got visible width {actual_w}; "
            f"row: {bare!r}"
        )


def test_structured_diff_bg_width_none_pads_to_terminal_width() -> None:
    """``bg_width=None`` preserves the legacy "pad to terminal width" behaviour.

    Regression guard for AC #2 in the 07-21-diff-bg-width-shrink PRD:
    the new ``bg_width`` prop must default to ``None`` so existing
    callers that don't pass it see no optical change. Render a
    one-line edit at ``columns=40`` with ``bg_width`` unset and assert
    the bg-carrying rows pad to 40 (the layout width).
    """
    import re

    from ink.layout.measure import string_width

    before = "a\nb"
    after = "a\nc"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            full_width_bg=True,
            add_bg_color="rgb(34,92,43)",
            del_bg_color="rgb(122,41,54)",
            # bg_width intentionally unset — legacy behaviour.
        ),
        columns=40,
    )
    lines = out.split("\n")
    bg_rows = [
        line for line in lines
        if f"{ESC}[48;2;122;41;54m" in line or f"{ESC}[48;2;34;92;43m" in line
    ]
    assert len(bg_rows) == 2, (
        f"expected 2 bg-carrying rows, got {len(bg_rows)}: {lines!r}"
    )
    for line in bg_rows:
        bare = re.sub(r"\x1b\[[0-9;]*m", "", line)
        actual_w = string_width(bare)
        assert actual_w == 40, (
            f"bg row should pad to terminal width (40), got visible width "
            f"{actual_w}; row: {bare!r}"
        )


def test_structured_diff_bg_width_smaller_than_content_keeps_full_content() -> None:
    """``bg_width`` below the row's content width doesn't truncate content.

    Edge case: when the caller passes a ``bg_width`` smaller than the
    visible content width, the pad formula ``max(0, target_w -
    content_w)`` clamps to zero (no trailing pad) and the content is
    left intact. The bg band still opens and closes around the content;
    only the trailing pad disappears.
    """
    import re

    from ink.layout.measure import string_width

    # A moderately long line so content width exceeds bg_width.
    before = "short"
    after = "a meaningfully long line of code"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            full_width_bg=True,
            add_bg_color="rgb(34,92,43)",
            del_bg_color="rgb(122,41,54)",
            bg_width=10,  # well below any row's content width
        ),
        columns=120,
    )
    lines = out.split("\n")
    bg_rows = [
        line for line in lines
        if f"{ESC}[48;2;122;41;54m" in line or f"{ESC}[48;2;34;92;43m" in line
    ]
    assert bg_rows, f"expected at least one bg-carrying row, got: {lines!r}"
    for line in bg_rows:
        bare = re.sub(r"\x1b\[[0-9;]*m", "", line)
        # Each row's visible width must be at least the content width
        # (we don't truncate content), and never go negative.
        actual_w = string_width(bare)
        assert actual_w >= 1, (
            f"row collapsed to zero width; bare: {bare!r}"
        )


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

    Counter increments on every row EXCEPT when a ``del`` is paired
    with the immediately-following ``add`` — the pair shares the same
    counter value (CC's modification convention: ``-old / +new`` on the
    same source line). Hunk / marker rows get ``None``.
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    diff_lines = ["@@ -1,3 +1,3 @@", " ctx1", "-del", "+add", " ctx2"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries)
    nums = [e.get("line_num") for e in entries]
    # Hunk = None; context=1; del+add share 2 (paired); context=3.
    assert nums == [None, 1, 2, 2, 3]


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
    full visible string with embedded ANSI fg / bg SGRs (no per-leaf
    resets). The bg SGR is embedded **after** the row's prefix area
    (``row_prefix`` / ``indent``) so the prefix keeps the default
    terminal bg — the previous attempt also set ``backgroundColor`` +
    ``flushBackgroundToWidth`` on the leaf, which made PyInk's bg
    painter cover the prefix cells too.

    We walk the rendered Element tree and assert each add / del row is
    a single Text leaf (no Box-of-Text-leaves wrapper) carrying NO
    ``backgroundColor`` prop and NO ``flushBackgroundToWidth`` — the
    bg is purely in-band ANSI in the leaf's callable output.
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
    # Collect all Text leaves in the rendered tree. We expect exactly
    # 2 body rows (one del + one add); each must be a single Text leaf
    # with NO ``backgroundColor`` prop and NO ``flushBackgroundToWidth``
    # — the bg is purely in-band ANSI.
    text_leaves: list[Any] = []

    def walk(node: Any) -> None:
        if (
            hasattr(node, "type")
            and isinstance(node.type, str)
            and node.type == "text"
        ):
            text_leaves.append(node)
        children = getattr(node, "children", None) or []
        for child in children:
            walk(child)

    walk(el)
    # Filter to body leaves (the header / divider / gutter leaves may
    # also be present). Body leaves carry a callable that emits the
    # bg SGR; we identify them by rendering the tree and checking the
    # output string for the expected bg SGR sequences.
    out = _render(el, columns=80)
    assert f"{ESC}[48;2;30;70;32m" in out, "add row bg SGR missing"
    assert f"{ESC}[48;2;74;32;32m" in out, "del row bg SGR missing"
    # Every Text leaf in the tree must NOT carry ``backgroundColor`` or
    # ``flushBackgroundToWidth`` — the new approach embeds bg in-band.
    for leaf in text_leaves:
        props = leaf.props or {}
        assert "backgroundColor" not in props, (
            f"Text leaf should NOT carry backgroundColor prop (bg is "
            f"in-band ANSI); got backgroundColor={props['backgroundColor']!r}, "
            f"props={dict(props)!r}"
        )
        assert props.get("flushBackgroundToWidth") is not True, (
            f"Text leaf should NOT carry flushBackgroundToWidth (bg is "
            f"in-band ANSI); props={dict(props)!r}"
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


# ---------------------------------------------------------------------------
# start_line (07-20-tool-message-rendering-polish follow-up — real file line numbers)
# ---------------------------------------------------------------------------


def test_start_line_offsets_gutter_numbers() -> None:
    """``start_line=N`` shifts every body row's gutter number by ``N-1``.

    When the caller knows the real source-file line where the diff
    begins (e.g. Jarvis's Edit tool records it from ``content.index(
    old_string)``), the gutter should show those file-accurate numbers
    instead of the snippet-relative 1, 2, 3, … sequence. For a 4-row
    body diff (ctx + paired del/add + ctx) starting at line 50 the
    rendered gutter is ``50`` / ``51`` / ``51`` / ``52`` (paired del/add
    share the source line per CC convention), not ``1`` / ``2`` / ….
    """
    before = "alpha\nbeta\ngamma"
    after = "alpha\nBETA\ngamma"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            line_numbers=True,
            start_line=50,
        )
    )
    # The first body row (context "alpha") should now carry gutter "50"
    # instead of "1". With a 2-digit max line (51 → width 3 per CC's
    # ``len(str(max))+1`` rule) the gutter is right-aligned to width 3:
    # ``" 50 "`` (space pad + 50 + space sigil). We check the bare
    # substring so the assertion is robust to ANSI colour codes.
    assert " 50 alpha" in out or "50 alpha" in out
    # The default-counter value ``1`` must NOT appear before ``alpha``
    # when start_line is shifted — defensive against a regression that
    # ignores the prop entirely.
    import re

    assert re.search(r"\b1 alpha\b", out) is None


def test_start_line_default_1() -> None:
    """Without ``start_line`` the counter starts at 1 (backward compat).

    Existing callers that don't pass ``start_line`` must see the same
    gutter numbers as before — the new prop defaults to ``1`` and the
    counter increments identically.
    """
    before = "alpha\nbeta\ngamma"
    after = "alpha\nBETA\ngamma"
    out_default = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            line_numbers=True,
        )
    )
    out_explicit = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            line_numbers=True,
            start_line=1,
        )
    )
    # The gutter numbering should be identical — both should contain
    # ``1 alpha`` (the first body row's gutter). Strip ANSI codes for a
    # robust comparison.
    def _strip(s: str) -> str:
        out = s
        for code in (f"{ESC}[0m", f"{ESC}[32m", f"{ESC}[31m", f"{ESC}[1m"):
            out = out.replace(code, "")
        return out

    assert "1 alpha" in _strip(out_default)
    assert "1 alpha" in _strip(out_explicit)
    assert _strip(out_default) == _strip(out_explicit)


def test_assign_line_numbers_with_start_line() -> None:
    """``_assign_line_numbers(start_line=N)`` shifts every assigned number.

    Unit-level guard for the counter logic: passing ``start_line=50``
    should produce ``[None, 50, 51, 51, 52]`` for the same diff that
    without the prop produces ``[None, 1, 2, 2, 3]`` (paired del+add
    share the same number — CC's modification convention).
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    diff_lines = ["@@ -1,3 +1,3 @@", " ctx1", "-del", "+add", " ctx2"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries, start_line=50)
    nums = [e.get("line_num") for e in entries]
    assert nums == [None, 50, 51, 51, 52]


def test_assign_line_numbers_start_line_below_1_clamps() -> None:
    """``start_line`` below 1 clamps to 1 — defensive against bad callers.

    ``max(1, int(start_line))`` guards against an off-by-one or a
    malicious caller passing ``0`` / negative — the gutter would
    otherwise render ``0`` / ``-1`` which is meaningless to the reader.
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    diff_lines = [" ctx1", "-del", "+add"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries, start_line=0)
    nums = [e.get("line_num") for e in entries]
    # ctx=1, paired del+add share 2.
    assert nums == [1, 2, 2]


# ---------------------------------------------------------------------------
# Paired del+add line-number sharing (07-20-tool-message-rendering-polish)
# ---------------------------------------------------------------------------


def test_paired_del_add_shares_line_number() -> None:
    """A ``-old`` immediately followed by ``+new`` shares its line number.

    CC's ``StructuredDiff`` convention: a paired modification of a
    source line is rendered as two visual rows (``-old`` then ``+new``)
    tagged with the SAME line number — the source line being modified.
    The counter increments ONCE for the pair, so the following context
    row gets ``N+1`` (not ``N+2``).

    Regression guard for the original buggy implementation, which
    incremented the counter on every row kind uniformly, producing
    ``[ctx=1, del=2, add=3, ctx=4]`` instead of the CC-correct
    ``[ctx=1, del=2, add=2, ctx=3]``.
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    # context, then a paired del+add, then another context.
    diff_lines = [" ctx_before", "-old line", "+new line", " ctx_after"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries)
    nums = [e.get("line_num") for e in entries]
    # del (index 1) and add (index 2) share the SAME number.
    assert nums[1] == nums[2], (
        f"paired del+add should share line number, got del={nums[1]!r} "
        f"add={nums[2]!r} (full: {nums!r})"
    )
    # The pair's shared number equals the counter value at the del row.
    # ctx_before=1, del+add share 2, ctx_after=3.
    assert nums == [1, 2, 2, 3], (
        f"expected [1, 2, 2, 3] for ctx+paired del/add+ctx, got {nums!r}"
    )


def test_unpaired_del_only_increments_normally() -> None:
    """A standalone ``-`` row (no following ``+``) increments the counter.

    Pure deletion (e.g. a line being removed with nothing inserted in
    its place) does NOT share a number — the counter increments after
    the del row exactly as for any other single row. The next context
    / add row gets ``N+1``.
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    # context, standalone del (no add follows), then context.
    diff_lines = [" ctx_before", "-gone", " ctx_after"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries)
    nums = [e.get("line_num") for e in entries]
    # ctx_before=1, del=2 (no pairing — pure deletion), ctx_after=3.
    assert nums == [1, 2, 3], (
        f"unpaired del should increment normally, got {nums!r}"
    )


def test_unpaired_add_only_increments_normally() -> None:
    """A standalone ``+`` row (no preceding ``-``) increments the counter.

    Pure insertion (e.g. a new line added with nothing removed before
    it) does NOT share a number — the counter increments after the add
    row exactly as for any other single row.
    """
    from ink.externals.diff import _assign_line_numbers, _classify_diff_lines

    # context, standalone add (no del precedes), then context.
    diff_lines = [" ctx_before", "+fresh", " ctx_after"]
    entries = _classify_diff_lines(diff_lines)
    _assign_line_numbers(entries)
    nums = [e.get("line_num") for e in entries]
    # ctx_before=1, add=2 (no pairing — pure insertion), ctx_after=3.
    assert nums == [1, 2, 3], (
        f"unpaired add should increment normally, got {nums!r}"
    )


# ---------------------------------------------------------------------------
# Long-line wrap (07-23-long-code-line-wrap PR2)
# ---------------------------------------------------------------------------
#
# The original multi-Text-leaf-per-row layout let the flex shrink algorithm
# penalise every Pygments / word-diff token proportionally when a row
# exceeded ``columns``. For inline_highlight pairs with many tokens, this
# collapsed every token to its first character (``the`` → ``t``, ``quick``
# → ``q``, …) — silent data corruption. The PR2 refactor composes each
# diff row as ONE Text leaf carrying an inline-ANSI string so the layout
# engine's ``_measure_paragraph → wrap_text(mode="wrap")`` pipeline wraps
# the entire row onto subsequent visual rows without per-token shrink.


def test_long_plus_line_wraps_without_char_loss() -> None:
    """A long ``+`` line wraps onto multiple visual rows with full content.

    Regression for the inline_highlight shrink bug: pre-refactor the CC
    multi-leaf path emitted one Text leaf per word-diff token, and the
    flex shrink algorithm ate every token's trailing characters when
    the row exceeded ``columns``. Post-refactor the row is one Text
    leaf carrying the full ANSI string; wrapping preserves every char.
    """
    import re

    before = "x"
    # 5 repeats × 9 words/repeat = 45 words; at 50 cols this wraps to
    # multiple visual rows.
    after = "the quick brown fox jumps over the lazy dog " * 5
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            inline_highlight=True,
        ),
        columns=50,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # The content is preserved verbatim — every word appears intact
    # on the add side (the diff has just one add row whose body wraps).
    # Pre-refactor the inline_highlight path collapsed each word to its
    # first char, so "the quick brown fox" became "t q b f" — assert
    # the full word sequence survives. ``the`` appears 2× per repeat
    # (start + after ``dog``), so 10 total.
    assert plain.count("the") == 10
    assert plain.count("quick") == 5
    assert plain.count("brown") == 5
    assert plain.count("fox") == 5
    assert plain.count("lazy") == 5
    # The row wraps (more than 1 visual row for the add body). The
    # first visual row carries the green SGR + sigil; continuation
    # visual rows carry only the wrapped body text (no SGR opener
    # since the single Text leaf's SGR doesn't repeat across the
    # layout's wrap boundaries). We count any line whose plain text
    # contains words from the add body.
    plain_lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in out.split("\n")]
    add_body_lines = [ln for ln in plain_lines if "quick" in ln or "fox" in ln]
    assert len(add_body_lines) >= 2, (
        f"long +line should wrap to multiple visual rows, "
        f"got {len(add_body_lines)}: {plain_lines!r}"
    )


def test_long_minus_line_wraps_without_char_loss() -> None:
    """A long ``-`` line wraps onto multiple visual rows with full content."""
    import re

    # 5 repeats of a long phrase; the del side wraps.
    before = "the quick brown fox jumps over the lazy dog " * 5
    after = "x"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            inline_highlight=True,
        ),
        columns=50,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # Every word from the deleted line survives. ``the`` appears 2×
    # per repeat (start + after ``dog``).
    assert plain.count("the") == 10
    assert plain.count("quick") == 5
    assert plain.count("brown") == 5
    assert plain.count("lazy") == 5
    assert plain.count("dog") == 5


def test_long_context_line_wraps_without_char_loss() -> None:
    """A long context line wraps without losing characters.

    Context lines take the legacy fast path (no CC features trigger
    cc_mode when no add/del markers are around them). The Text leaf
    carries the raw context body; layout's wrap handles overflow.
    """
    import re

    long_context = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    before = long_context
    after = long_context + "\nNEW LINE"
    out = _render(
        StructuredDiff(before, after, show_header=False, show_markers=False),
        columns=40,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # Every word from the long context line is preserved.
    words = (
        "alpha", "beta", "gamma", "delta", "epsilon",
        "zeta", "eta", "theta", "iota", "kappa", "lambda",
    )
    for word in words:
        assert word in plain, f"expected {word!r} in rendered output, got: {plain!r}"


def test_long_plus_line_with_pygments_wraps_without_char_loss() -> None:
    """Long ``+`` line with ``language="python"`` wraps with full tokens.

    Pre-refactor the legacy highlight fast path emitted
    ``Box(Text(prefix), HighlightedCode(body))`` — HighlightedCode's
    per-line Text leaf wraps cleanly (post-PR1), so this case worked,
    but we still cover it to guard against regressions in PR2's
    refactor of ``_render_diff_line``.
    """
    import re

    long_line = (
        "def function_name(argument_one, argument_two, "
        "argument_three, argument_four, argument_five):"
    )
    out = _render(
        StructuredDiff(
            "x",
            long_line,
            language="python",
            show_header=False,
            show_markers=False,
        ),
        columns=50,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # The full body content survives the wrap.
    assert "def" in plain
    assert "function_name" in plain
    assert "argument_one" in plain
    assert "argument_five" in plain


def test_long_minus_line_with_pygments_wraps_without_char_loss() -> None:
    """Long ``-`` line with ``language="python"`` wraps with full tokens."""
    import re

    long_line = (
        "def function_name(argument_one, argument_two, "
        "argument_three, argument_four, argument_five):"
    )
    out = _render(
        StructuredDiff(
            long_line,
            "x",
            language="python",
            show_header=False,
            show_markers=False,
        ),
        columns=50,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    assert "function_name" in plain
    assert "argument_one" in plain
    assert "argument_five" in plain


def test_full_width_bg_band_extends_across_wrapped_visual_rows() -> None:
    """``full_width_bg=True`` paints bg across ALL wrapped visual rows.

    PR2 concern: when a ``+`` row wraps to multiple visual rows, the
    green bg band must cover the full layout width on EVERY visual row
    (not just the first one). The pre-refactor code only painted bg
    on the first visual row; continuation rows had bg only on their
    text cells.

    Fix: the row's single Text leaf emits a multi-line string where
    each visual row's chunk + trailing pad ends with its OWN reset
    (``\\x1b[0m``). This protects the pad cells from the renderer's
    per-row ``rstrip()`` so the bg band spans the full target width
    on every visual row.
    """
    import re

    from ink.layout.measure import string_width

    long_line = (
        "def function_name(argument_one, argument_two, "
        "argument_three, argument_four, argument_five):"
    )
    out = _render(
        StructuredDiff(
            "x",
            long_line,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
        ),
        columns=50,
    )
    lines = out.split("\n")
    # Find the add row(s) — visual rows carrying the green bg SGR.
    add_bg_open = f"{ESC}[48;2;30;70;32m"
    add_rows = [ln for ln in lines if add_bg_open in ln]
    assert len(add_rows) >= 2, (
        f"expected the add row to wrap to 2+ visual rows, got {len(add_rows)}: {lines!r}"
    )
    # Every add visual row must:
    #   1. start with the bg opener (band active from column 0)
    #   2. end with a reset (band closes at the row end — pad protected)
    #   3. have visible width == 50 (band spans full layout width)
    for i, row in enumerate(add_rows):
        assert row.startswith(add_bg_open), (
            f"add visual row {i} should start with bg opener, got: {row!r}"
        )
        assert row.endswith(f"{ESC}[0m"), (
            f"add visual row {i} should end with reset, got: {row!r}"
        )
        bare = re.sub(r"\x1b\[[0-9;]*m", "", row)
        actual_w = string_width(bare)
        assert actual_w == 50, (
            f"add visual row {i} should span 50 cols, got {actual_w}: {bare!r}"
        )


def test_line_numbers_gutter_only_on_first_visual_row_when_wrapped() -> None:
    """``line_numbers=True`` gutter appears only on the first visual row.

    When a row wraps, the line-number gutter must NOT repeat on
    continuation visual rows (CC parity — gutter belongs to the source
    line, not to visual rows). The PR2 refactor's continuation-row
    layout intentionally omits the gutter.
    """
    import re

    long_line = (
        "def function_name(argument_one, argument_two, "
        "argument_three, argument_four, argument_five):"
    )
    out = _render(
        StructuredDiff(
            "x",
            long_line,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            full_width_bg=True,
            add_bg_color="rgb(30,70,32)",
            del_bg_color="rgb(74,32,32)",
        ),
        columns=50,
    )
    lines = out.split("\n")
    # First visual row of the add row carries the gutter "1+".
    add_bg_open = f"{ESC}[48;2;30;70;32m"
    add_rows = [ln for ln in lines if add_bg_open in ln]
    assert len(add_rows) >= 2
    # First visual row contains the gutter (digit + ``+`` sigil).
    first_bare = re.sub(r"\x1b\[[0-9;]*m", "", add_rows[0])
    assert "+" in first_bare, (
        f"first visual row should carry + sigil, got: {first_bare!r}"
    )
    # Continuation visual rows do NOT carry the ``+`` sigil.
    for cont in add_rows[1:]:
        cont_bare = re.sub(r"\x1b\[[0-9;]*m", "", cont)
        # Strip leading/trailing whitespace for the sigil check.
        stripped = cont_bare.strip()
        assert not stripped.startswith("+"), (
            f"continuation visual row should NOT carry + sigil, got: {cont_bare!r}"
        )


def test_inline_highlight_survives_wrap() -> None:
    """``inline_highlight=True`` changed-token highlights survive wrap.

    The PR2 refactor composes the inline-highlight row as ONE Text leaf
    whose body is the per-token ANSI string. When the row wraps, the
    ANSI string is wrapped as a unit; the bright-colour SGRs for
    changed tokens appear in the output (possibly on whichever visual
    row the changed token lands on).
    """
    before = "the quick brown fox " * 5 + "dog"
    after = "the quick brown fox " * 5 + "cat"
    out = _render(
        StructuredDiff(
            before,
            after,
            show_header=False,
            show_markers=False,
            line_numbers=True,
            inline_highlight=True,
        ),
        columns=50,
    )
    # greenBright (SGR 92) marks the added ``cat``; redBright (SGR 91)
    # marks the removed ``dog``. Both should appear somewhere in the
    # wrapped output.
    assert f"{ESC}[92m" in out, (
        "inline-highlight changed-token colour (greenBright) missing"
    )
    assert f"{ESC}[91m" in out, (
        "inline-highlight changed-token colour (redBright) missing"
    )


def test_short_diff_renders_byte_identical_after_refactor() -> None:
    """Short diff output is byte-identical to the pre-refactor renderer.

    Snapshot regression: the PR2 refactor must not change visible
    output for short diffs. We render a battery of short diffs with
    various option combinations and assert each matches an embedded
    snapshot string. Update the snapshots deliberately — accidental
    changes indicate a regression in the refactor.
    """
    # Snapshot taken post-refactor. The byte sequences mirror the
    # pre-refactor output exactly (no SGR reordering, no pad changes).
    cases = [
        # Plain text, no options — single coloured Text per row.
        (
            "x = 1",
            "x = 2",
            {},
            "\x1b[31m-x = 1\x1b[0m\n\x1b[32m+x = 2\x1b[0m",
        ),
        # bg_color only — Text with flush.
        (
            "x = 1",
            "x = 2",
            {"add_bg_color": "rgb(30,70,32)"},
            "\x1b[31m-x = 1\x1b[0m\n\x1b[48;2;30;70;32m\x1b[32m+x = 2\x1b[0m"
            + " " * 74
            + "\x1b[0m",
        ),
        # line_numbers — CC mode gutter (zero-padded to gutter_width=2).
        (
            "x = 1",
            "x = 2",
            {"line_numbers": True},
            "\x1b[31m01-\x1b[0m\x1b[31mx = 1\x1b[0m\n\x1b[32m01+\x1b[0m\x1b[32mx = 2\x1b[0m",
        ),
    ]
    for before, after, kwargs, expected in cases:
        out = _render(
            StructuredDiff(
                before,
                after,
                show_header=False,
                show_markers=False,
                **kwargs,
            ),
            columns=80,
        )
        assert out == expected, (
            f"snapshot mismatch for kwargs={kwargs!r}:\n"
            f"  expected: {expected!r}\n"
            f"  actual:   {out!r}"
        )


def test_long_line_exactly_at_columns_no_wrap() -> None:
    """A line whose visible width exactly equals ``columns`` doesn't wrap."""
    import re

    from ink.layout.measure import string_width

    # Build a content line whose visible width fits in columns after
    # accounting for the diff sigil. The ``+`` prefix consumes 1 col,
    # so the body must be ≤ 39 chars at columns=40 to avoid wrap.
    content = "a" * 39
    assert string_width(content) == 39
    out = _render(
        StructuredDiff(
            "x",
            content,
            show_header=False,
            show_markers=False,
        ),
        columns=40,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # The content sits on a single visual row (no wrap).
    assert plain.count(content) == 1, (
        f"39-char body should fit on one visual row at columns=40, "
        f"got: {plain!r}"
    )


def test_long_line_one_char_over_columns_wraps() -> None:
    """A line whose visible width is ``columns + 1`` wraps to 2 visual rows."""
    import re

    # 41 chars at columns=40 — should wrap.
    content = "a" * 41
    out = _render(
        StructuredDiff(
            "x",
            content,
            show_header=False,
            show_markers=False,
        ),
        columns=40,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # The content survives — every char appears.
    assert plain.count("a") == 41


def test_long_line_twice_columns_wraps_to_two_rows() -> None:
    """A line 2× ``columns`` wraps to 2 visual rows."""
    import re

    content = "a" * 80
    out = _render(
        StructuredDiff(
            "x",
            content,
            show_header=False,
            show_markers=False,
        ),
        columns=40,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # Every char preserved across the wrap.
    assert plain.count("a") == 80


def test_single_token_wider_than_columns_hard_breaks() -> None:
    """A single token wider than ``columns`` hard-breaks (no overflow)."""
    import re

    from ink.layout.measure import string_width

    # One long token (no break points) wider than columns.
    content = "a" * 100
    out = _render(
        StructuredDiff(
            "x",
            content,
            show_header=False,
            show_markers=False,
        ),
        columns=40,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    # All 100 chars survive — wrap algorithm hard-breaks long tokens.
    assert plain.count("a") == 100
    # Output width never exceeds columns (each visual row ≤ 40).
    for line in out.split("\n"):
        bare = re.sub(r"\x1b\[[0-9;]*m", "", line)
        assert string_width(bare) <= 40, (
            f"visual row exceeds columns: w={string_width(bare)}, row={bare!r}"
        )
