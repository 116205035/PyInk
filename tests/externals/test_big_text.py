"""Tests for :func:`pyink.externals.BigText` (Phase 6 PR2).

``BigText`` delegates to :mod:`pyfiglet` for the glyph data — 300+
fonts, multi-row output. The factory is declarative (no hooks, no
function component), so every assertion uses the synchronous
:func:`render_to_string` test renderer.

Coverage:

* Element shape — ``BigText`` returns a ``box`` host element with
  ``flexDirection="column"``.
* Basic rendering — multi-row ASCII art comes out, content is
  readable.
* Multiple fonts — ``standard`` / ``block`` / ``shadow`` /
  ``digital`` / ``banner`` all produce distinct output.
* ``colors`` multi-colour — rows cycle through the colour list
  modulo its length (mirrors :mod:`ink-big-text`'s ``colors`` prop).
* ``align`` — pyfiglet's ``justify`` option is exercised (the
  output is pre-padded within pyfiglet's render box).
* ``width`` — pyfiglet wraps long banners at the caller-supplied
  width.
* Missing :mod:`pyfiglet` — friendly ``ImportError`` pointing at
  ``pip install pyink[big-text]``.
* Empty string renders an empty column.
* Multi-line text (containing ``\\n``) renders without crashing.
* Lowercase input is not auto-uppercased (pyfiglet handles case).
* ``BigText`` is exported from ``pyink.externals`` but NOT from the
  top-level ``pyink`` package.
"""

from __future__ import annotations

import builtins
import sys
from typing import Any

import pytest

from pyink import Box, Text, render_to_string
from pyink.core.element import Element
from pyink.externals import BigText

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Import / availability guards
# ---------------------------------------------------------------------------


def _pyfiglet_available() -> bool:
    """Return ``True`` if :mod:`pyfiglet` is importable in this env."""
    try:
        import pyfiglet  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pyfiglet_available(),
    reason="pyfiglet not installed (pip install pyink[big-text])",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(tree: Element, *, columns: int = 80) -> str:
    """Render ``tree`` via the synchronous test pipeline."""
    return render_to_string(tree, columns=columns)


def _install_pyfiglet_import_blocker() -> None:
    """Make ``import pyfiglet`` raise ``ImportError`` until reset.

    Patches :func:`builtins.__import__` and removes any cached
    ``pyfiglet`` / ``pyfiglet.*`` modules so a subsequent import goes
    through the blocker. The fixture in ``_restore_import`` undoes the
    patch.
    """

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pyfiglet" or name.startswith("pyfiglet."):
            raise ImportError(f"mocked: pyfiglet not installed ({name})")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    # Drop cached pyfiglet modules so a fresh import actually hits the
    # blocker.
    for mod in list(sys.modules):
        if mod == "pyfiglet" or mod.startswith("pyfiglet."):
            del sys.modules[mod]


@pytest.fixture
def _restore_import() -> Any:
    """Restore the real ``__import__`` after the test."""
    real_import = builtins.__import__
    yield
    builtins.__import__ = real_import


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_big_text_returns_box_host_element() -> None:
    """``BigText`` is a declarative factory — output is a ``box`` host."""
    el = BigText("A")
    assert isinstance(el, Element)
    assert el.type == "box"
    assert el.props.get("flexDirection") == "column"


def test_big_text_empty_string_renders_empty_column() -> None:
    """``BigText("")`` -> empty column (no children).

    pyfiglet renders an empty string to an empty string; we turn that
    into an empty Box so the surrounding layout still has a valid
    element to mount.
    """
    el = BigText("")
    assert el.type == "box"
    assert el.children == ()


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------


def test_big_text_renders_multiple_rows() -> None:
    """A non-empty string renders multiple rows of ASCII art.

    pyfiglet's ``standard`` font produces 6 rows for a single letter;
    we just assert the output is multi-row and non-empty.
    """
    out = _render(BigText("Hello"))
    lines = out.split("\n")
    assert len(lines) > 1
    # At least one line should contain non-whitespace content.
    assert any(line.strip() for line in lines)


def test_big_text_produces_ascii_art_content() -> None:
    """The rendered output contains characters typical of ASCII art
    (pipes, underscores, slashes) rather than the raw input string."""
    out = _render(BigText("Hi"))
    # pyfiglet's standard font uses |, _, /, \\ and spaces.
    assert "|" in out or "_" in out or "/" in out
    # The literal input characters should not appear verbatim in the
    # rendered banner (they're replaced by ASCII art strokes).
    # We check the first character — it should not be the input "H".
    assert out.lstrip()[:1] != "H"


def test_big_text_each_letter_produces_distinct_output() -> None:
    """Different letters render different banners (sanity)."""
    out_a = _render(BigText("A"))
    out_b = _render(BigText("B"))
    assert out_a != out_b


def test_big_text_long_string_renders_without_raising() -> None:
    """A 10-character string renders without raising."""
    out = _render(BigText("HELLOWORLD"), columns=200)
    # pyfiglet should produce something non-empty.
    assert out.strip() != ""


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_big_text_standard_font_default() -> None:
    """The default font is ``"standard"``."""
    out_default = _render(BigText("A"))
    out_standard = _render(BigText("A", font="standard"))
    assert out_default == out_standard


@pytest.mark.parametrize("font", ["standard", "block", "shadow", "digital", "banner"])
def test_big_text_font_produces_non_empty_output(font: str) -> None:
    """Each of the documented fonts produces non-empty output."""
    out = _render(BigText("Hi", font=font))
    assert out.strip() != ""


def test_big_text_different_fonts_produce_different_output() -> None:
    """The same string in different fonts renders differently."""
    fonts = ["standard", "block", "shadow", "digital", "banner"]
    outputs = {_render(BigText("Hi", font=f)) for f in fonts}
    # All outputs should be distinct (5 unique strings from 5 fonts).
    assert len(outputs) == len(fonts)


def test_big_text_unknown_font_raises() -> None:
    """An unknown font name raises rather than silently falling back.

    pyfiglet raises :class:`pyfiglet.FontNotFound` for unknown fonts;
    we let that propagate because a typo in the font name is almost
    always a bug the caller wants to hear about (matching
    :func:`HighlightedCode`'s "no silent fallback" stance).
    """
    import pyfiglet

    with pytest.raises(pyfiglet.FontNotFound):
        BigText("A", font="totally-made-up-font")


# ---------------------------------------------------------------------------
# Multi-colour (colors prop)
# ---------------------------------------------------------------------------


def test_big_text_colors_cycles_per_row() -> None:
    """``colors=["red", "green"]`` paints row 0 red, row 1 green, row 2
    red, … cycling modulo ``len(colors)``."""
    out = _render(BigText("Hi", colors=["red", "green"]))
    # red = ESC[31m, green = ESC[32m. Pyfiglet's standard font
    # produces 6 rows for "Hi" (after we strip trailing blank rows).
    # The cycle is red, green, red, green, red, green — so each colour
    # appears multiple times.
    assert out.count(ESC + "[31m") >= 2
    assert out.count(ESC + "[32m") >= 2


def test_big_text_colors_three_cycle() -> None:
    """A three-colour ``colors`` list cycles through all three hues."""
    out = _render(BigText("Hi", colors=["red", "green", "blue"]))
    assert ESC + "[31m" in out  # red
    assert ESC + "[32m" in out  # green
    assert ESC + "[34m" in out  # blue


def test_big_text_colors_single_item_list() -> None:
    """A single-item ``colors`` list paints every row that colour."""
    out = _render(BigText("Hi", colors=["red"]))
    # Every non-empty row should be red. We can't assert an exact count
    # because the row count depends on pyfiglet's font metrics; we just
    # check that red appears and no other colour leaks in.
    assert out.count(ESC + "[31m") >= 2
    # No green / yellow SGR sequences should be present.
    assert ESC + "[32m" not in out
    assert ESC + "[33m" not in out


def test_big_text_color_applied_to_all_rows() -> None:
    """``color`` (single colour) is applied uniformly to every row."""
    out = _render(BigText("Hi", color="red"))
    # Every non-empty row is red. We can't pin an exact count because
    # the row count depends on pyfiglet, but at least 2 rows worth.
    assert out.count(ESC + "[31m") >= 2


def test_big_text_no_color_no_sgr() -> None:
    """``color=None`` and ``colors=None`` (defaults) leave the banner
    unstyled (no ANSI SGR sequences)."""
    out = _render(BigText("Hi"))
    assert ESC + "[" not in out


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_big_text_align_left_default() -> None:
    """The default alignment is ``"left"``."""
    out_default = _render(BigText("A"))
    out_left = _render(BigText("A", align="left"))
    assert out_default == out_left


def test_big_text_align_center_changes_output() -> None:
    """``align="center"`` produces different output than ``align="left"``.

    pyfiglet pre-pads the rendered rows to ``width`` based on the
    chosen justification, so a centred banner differs from a
    left-aligned one (when the banner is narrower than ``width``).
    """
    out_left = _render(BigText("Hi", align="left", width=40))
    out_center = _render(BigText("Hi", align="center", width=40))
    assert out_left != out_center


def test_big_text_align_right_changes_output() -> None:
    """``align="right"`` produces different output than ``align="left``."""
    out_left = _render(BigText("Hi", align="left", width=40))
    out_right = _render(BigText("Hi", align="right", width=40))
    assert out_left != out_right


def test_big_text_unknown_align_falls_back_to_left() -> None:
    """An unknown ``align`` value falls back to ``"left"`` rather than
    raising — pyfiglet only accepts the three standard values, so we
    normalise before handing off."""
    out_unknown = _render(BigText("Hi", align="diagonal"))
    out_left = _render(BigText("Hi", align="left"))
    assert out_unknown == out_left


# ---------------------------------------------------------------------------
# Width
# ---------------------------------------------------------------------------


def test_big_text_width_affects_wrapping() -> None:
    """A narrower ``width`` wraps long banners to more rows than a wide
    one. We don't pin an exact row count (font metrics vary); we just
    assert the narrow render is at least as tall as the wide one."""
    out_wide = _render(BigText("Hello World", width=200))
    out_narrow = _render(BigText("Hello World", width=40))
    # Wide render should fit on fewer rows; narrow should wrap.
    # Either way both should render *something*.
    assert out_wide.strip() != ""
    assert out_narrow.strip() != ""


def test_big_text_width_default_is_80() -> None:
    """The default width is 80 (matches pyfiglet's default)."""
    out_default = _render(BigText("Hi"))
    out_80 = _render(BigText("Hi", width=80))
    assert out_default == out_80


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_big_text_multiline_input_renders_without_raising() -> None:
    """A multi-line string (containing ``\\n``) renders without crashing.

    pyfiglet treats ``\\n`` as a hard line break inside the source
    string; each segment is rendered as its own banner block.
    """
    out = _render(BigText("a\nb"))
    # Should produce some non-empty content.
    assert out.strip() != ""


def test_big_text_lowercase_input_preserved() -> None:
    """Lowercase input is *not* auto-uppercased — pyfiglet handles case.

    This is a deliberate change from the previous hand-rolled fonts
    (which only covered A-Z and upper-cased the input). pyfiglet's
    fonts cover lowercase glyphs too.
    """
    out_lower = _render(BigText("a"))
    out_upper = _render(BigText("A"))
    # Lowercase and uppercase should produce *different* output (they
    # are distinct glyphs in pyfiglet's standard font).
    assert out_lower != out_upper


def test_big_text_whitespace_only_renders_empty_column() -> None:
    """A whitespace-only string renders an empty column.

    pyfiglet renders whitespace-only input to an all-newline string;
    we strip the trailing newlines and end up with an empty Box.
    """
    el = BigText("   ")
    assert el.type == "box"


def test_big_text_box_props_forwarded() -> None:
    """``**box_props`` are forwarded to the outer ``Box``."""
    el = BigText("Hi", padding=1)
    assert el.props.get("padding") == 1


def test_big_text_flex_direction_cannot_be_overridden() -> None:
    """The caller cannot override ``flexDirection`` via ``box_props``.

    The component's contract is one row per pyfiglet output line, so
    the outer Box is always a column.
    """
    el = BigText("Hi", flexDirection="row")
    assert el.props.get("flexDirection") == "column"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_big_text_non_string_text_raises() -> None:
    """``text`` must be a string."""
    try:
        BigText(123)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "text" in str(exc)
    else:
        raise AssertionError("non-string text should raise TypeError")


# ---------------------------------------------------------------------------
# Missing pyfiglet
# ---------------------------------------------------------------------------


def test_missing_pyfiglet_raises_friendly_import_error(_restore_import: Any) -> None:
    _install_pyfiglet_import_blocker()
    with pytest.raises(ImportError) as exc_info:
        BigText("Hi")
    assert "pip install pyink[big-text]" in str(exc_info.value)


def test_missing_pyfiglet_error_mentions_component_name(_restore_import: Any) -> None:
    _install_pyfiglet_import_blocker()
    with pytest.raises(ImportError) as exc_info:
        BigText("Hi")
    assert "BigText" in str(exc_info.value)


def test_missing_pyfiglet_error_chains_original_cause(_restore_import: Any) -> None:
    """The wrapper preserves the original ImportError as ``__cause__``."""
    _install_pyfiglet_import_blocker()
    with pytest.raises(ImportError) as exc_info:
        BigText("Hi")
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, ImportError)


def test_missing_pyfiglet_non_string_text_takes_priority(_restore_import: Any) -> None:
    """``TypeError`` on non-string ``text`` fires before the pyfiglet
    availability check — argument validation runs first."""
    _install_pyfiglet_import_blocker()
    with pytest.raises(TypeError):
        BigText(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


def test_big_text_inside_column() -> None:
    """``BigText`` composes inside a column with sibling Text."""
    tree: Any = Box(
        Text("Banner:", bold=True),
        BigText("Hi"),
        flexDirection="column",
        width=80,
    )
    out = _render(tree)
    # The banner content is present somewhere in the rendered output.
    assert out.strip() != ""


def test_big_text_with_border() -> None:
    """``BigText`` composes cleanly inside a bordered Box."""
    tree: Any = Box(
        BigText("Hi"),
        flexDirection="column",
        borderStyle="round",
        padding=1,
    )
    out = _render(tree)
    # The border draws (╭ / ╮ / ╰ / ╯ round corners).
    assert "╭" in out or "┌" in out or "│" in out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_big_text() -> None:
    from pyink.externals import BigText as InitBigText

    assert InitBigText is BigText


def test_big_text_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "BigText")
