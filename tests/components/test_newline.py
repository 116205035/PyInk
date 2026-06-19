"""Tests for :func:`pyink.components.Newline` (PR4)."""

from __future__ import annotations

from pyink import Box, Newline, Text, render_to_string
from pyink.components.newline import Newline as NewlineDirect
from pyink.core.element import Element

ESC = "\x1b"


def test_newline_creates_text_element() -> None:
    el = Newline()
    assert isinstance(el, Element)
    assert el.type == "text"
    assert el.children == ("\n",)


def test_newline_direct_import_matches_public() -> None:
    assert NewlineDirect is Newline


def test_newline_default_count_is_one() -> None:
    assert Newline().children == ("\n",)


def test_newline_count_two() -> None:
    assert Newline(2).children == ("\n\n",)


def test_newline_count_zero_yields_empty() -> None:
    el = Newline(0)
    assert el.children == ("",)


def test_newline_negative_count_clamped_to_zero() -> None:
    el = Newline(-3)
    assert el.children == ("",)


def test_newline_renders_break_in_text() -> None:
    """A Newline inside Text inserts a line break."""
    out = render_to_string(Text("Hello", Newline(), "World"))
    assert out == "Hello\nWorld"


def test_newline_count_three_inserts_three_breaks() -> None:
    out = render_to_string(Text("A", Newline(3), "B"))
    assert out == "A\n\n\nB"


def test_newline_in_column_layout() -> None:
    """Newline as a column child adds blank rows.

    ``Newline(2)`` produces a text body of ``"\\n\\n"``; laid out as a
    column child it occupies three rows (the wrapped body has three
    segments: ``["", "", ""]``). Combined with the ``A`` and ``B``
    siblings the total height is five rows.
    """
    out = render_to_string(
        Box(
            Text("A"),
            Newline(2),
            Text("B"),
            flexDirection="column",
            alignItems="flex-start",
        )
    )
    assert out == "A\n\n\n\nB"
