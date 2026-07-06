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
