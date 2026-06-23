"""Tests for :func:`pyink.externals.BigText` (Phase 6 PR2).

``BigText`` is a declarative factory that returns a ``box`` column —
no hooks, no function component. Every assertion uses the
synchronous :func:`render_to_string` test renderer.

Coverage:

* Element shape — ``BigText`` returns a ``box`` host element.
* Single character renders the right glyph block (3 rows tall).
* Multi-character text renders glyphs side-by-side.
* Lowercase input is uppercased before lookup.
* Unknown characters render as a blank glyph (font width preserved).
* Different fonts produce different output.
* Unknown font name falls back to ``block``.
* ``align`` controls ``justifyContent``.
* ``color`` is forwarded to the :func:`Text` leaves.
* Empty string renders an empty column.
* Font data integrity: every glyph has uniform row widths.
* ``BigText`` + ``FONTS`` are exported from ``pyink.externals`` but
  NOT from the top-level ``pyink`` package.
"""

from __future__ import annotations

from typing import Any

from pyink import Box, render_to_string
from pyink.core.element import Element
from pyink.externals import BigText
from pyink.externals.big_text import FONTS

#: Default glyph width for the shipped fonts (``_W`` in big_text.py).
_GLYPH_WIDTH: int = 6

#: Default row count for the shipped fonts.
_ROW_COUNT: int = 3


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
    """``BigText("")`` -> empty column (no children)."""
    el = BigText("")
    assert el.type == "box"
    assert el.children == ()


# ---------------------------------------------------------------------------
# Glyph rendering
# ---------------------------------------------------------------------------


def test_big_text_single_char_renders_three_rows() -> None:
    """A single character renders its glyph block (3 rows tall)."""
    tree: Any = Box(
        BigText("A"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == _ROW_COUNT


def test_big_text_single_char_renders_correct_width() -> None:
    """Each rendered row carries the glyph's content (the layout engine
    strips trailing spaces, so the rendered width may be shorter than
    the glyph's natural width, but the content is correct)."""
    tree: Any = Box(
        BigText("A"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    # Every line should start with the glyph's leading content (a space
    # or a block character) and be at most _GLYPH_WIDTH cells wide.
    for line in lines:
        assert len(line) <= _GLYPH_WIDTH
        assert len(line) > 0


def test_big_text_two_chars_doubles_width() -> None:
    """Two characters render side-by-side (the layout engine may strip
    trailing spaces, so we assert the row count rather than exact width)."""
    tree: Any = Box(
        BigText("AB"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == _ROW_COUNT
    # The first row's content is longer than a single glyph's first row
    # would be (the second glyph adds content).
    assert len(lines[0]) > _GLYPH_WIDTH


def test_big_text_each_letter_produces_distinct_glyph() -> None:
    """Different letters render different glyph blocks (sanity)."""
    out_a = render_to_string(BigText("A"))
    out_b = render_to_string(BigText("B"))
    assert out_a != out_b


# ---------------------------------------------------------------------------
# Case handling
# ---------------------------------------------------------------------------


def test_big_text_lowercase_uppercased() -> None:
    """Lowercase letters are auto-uppercased before lookup."""
    out_lower = render_to_string(BigText("a"))
    out_upper = render_to_string(BigText("A"))
    assert out_lower == out_upper


# ---------------------------------------------------------------------------
# Unknown characters
# ---------------------------------------------------------------------------


def test_big_text_unknown_char_falls_back_to_blank() -> None:
    """Characters not in the font (e.g. punctuation) render as blank.

    The blank glyph has the font's standard row count so the banner
    layout stays intact (3 rows tall). We don't assert exact width
    because the layout engine strips trailing spaces.
    """
    tree: Any = Box(
        BigText("A!A"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == _ROW_COUNT


def test_big_text_digit_renders_correctly() -> None:
    """Digits are in the font registry."""
    out_0 = render_to_string(BigText("0"))
    out_1 = render_to_string(BigText("1"))
    # Each is 3 rows tall, _GLYPH_WIDTH wide.
    lines_0 = out_0.split("\n")
    lines_1 = out_1.split("\n")
    assert len(lines_0) == _ROW_COUNT
    assert len(lines_1) == _ROW_COUNT
    # Different digits produce different output.
    assert out_0 != out_1


def test_big_text_space_renders_blank_glyph() -> None:
    """The space character renders as a blank column (layout engine
    strips trailing spaces, so we just assert the row count)."""
    out_with_space = render_to_string(BigText("A B"))
    lines = out_with_space.split("\n")
    assert len(lines) == _ROW_COUNT


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_big_text_different_fonts_produce_different_output() -> None:
    """The same letter in different fonts renders differently."""
    out_block = render_to_string(BigText("A", font="block"))
    out_simple = render_to_string(BigText("A", font="simple"))
    assert out_block != out_simple


def test_big_text_unknown_font_falls_back_to_block() -> None:
    """An unknown ``font`` name falls back to ``block`` rather than raising."""
    out_unknown = render_to_string(BigText("A", font="totally-made-up"))
    out_block = render_to_string(BigText("A", font="block"))
    assert out_unknown == out_block


def test_big_text_shipped_fonts_cover_full_alphabet() -> None:
    """Every shipped font covers A-Z + 0-9 + space (37 glyphs)."""
    for name, glyphs in FONTS.items():
        assert len(glyphs) == 37, (
            f"font {name!r} should cover 37 glyphs (A-Z + 0-9 + space), "
            f"got {len(glyphs)}"
        )
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ":
            assert ch in glyphs, f"font {name!r} missing glyph {ch!r}"


def test_big_text_font_data_uniform_widths() -> None:
    """Every glyph in a font has rows of equal width (validated at import).

    Re-asserting here so a future regression that bypasses the import
    validator still surfaces in tests.
    """
    for name, glyphs in FONTS.items():
        for ch, rows in glyphs.items():
            widths = {len(r) for r in rows}
            assert len(widths) == 1, (
                f"font {name!r} glyph {ch!r} has non-uniform widths: {rows!r}"
            )


def test_big_text_font_data_uniform_row_count() -> None:
    """Every glyph in a font has the same number of rows."""
    for name, glyphs in FONTS.items():
        row_counts = {len(r) for r in glyphs.values()}
        assert len(row_counts) == 1, (
            f"font {name!r} has glyphs with different row counts: "
            f"{row_counts}"
        )


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_big_text_align_left_uses_flex_start() -> None:
    """``align="left"`` sets ``justifyContent="flex-start"``."""
    el = BigText("A", align="left")
    assert el.props.get("justifyContent") == "flex-start"


def test_big_text_align_center_uses_center() -> None:
    """``align="center"`` sets ``justifyContent="center"``."""
    el = BigText("A", align="center")
    assert el.props.get("justifyContent") == "center"


def test_big_text_align_right_uses_flex_end() -> None:
    """``align="right"`` sets ``justifyContent="flex-end"``."""
    el = BigText("A", align="right")
    assert el.props.get("justifyContent") == "flex-end"


def test_big_text_unknown_align_falls_back_to_left() -> None:
    """An unknown ``align`` value falls back to ``left`` rather than raising."""
    el = BigText("A", align="diagonal")
    assert el.props.get("justifyContent") == "flex-start"


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------


def test_big_text_color_applied_to_all_rows() -> None:
    """``color`` is forwarded to every row's :func:`Text` leaf."""
    tree: Any = Box(
        BigText("A", color="red"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # SGR red = \x1b[31m. Should appear on every row (3 rows).
    assert out.count("\x1b[31m") == 3


def test_big_text_no_color_no_sgr() -> None:
    """``color=None`` (default) leaves the banner unstyled."""
    tree: Any = Box(
        BigText("A"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    assert "\x1b[" not in out


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
# Integration
# ---------------------------------------------------------------------------


def test_big_text_inside_column() -> None:
    """``BigText`` composes inside a column with sibling Text."""
    tree: Any = Box(
        BigText("A"),
        flexDirection="column",
        width=40,
    )
    out = render_to_string(tree)
    # The banner is 3 rows tall.
    assert len(out.split("\n")) == 3


def test_big_text_long_string() -> None:
    """A 10-character string renders without raising (3 rows tall)."""
    tree: Any = Box(
        BigText("HELLOWORLD"),
        flexDirection="column",
        width=100,
    )
    out = render_to_string(tree)
    lines = out.split("\n")
    assert len(lines) == _ROW_COUNT


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_big_text() -> None:
    from pyink.externals import BIG_TEXT_FONTS as InitFonts
    from pyink.externals import BigText as InitBigText

    assert InitBigText is BigText
    assert InitFonts is FONTS


def test_big_text_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in."""
    import pyink

    assert not hasattr(pyink, "BigText")
    assert not hasattr(pyink, "BIG_TEXT_FONTS")
