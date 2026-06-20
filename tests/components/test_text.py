"""Tests for :func:`pyink.components.Text` (PR4).

Covers:

* Element creation.
* Colour specs: named / hex / rgb / ansi256.
* Background colour (own + inherited from parent Box).
* Style toggles: bold / italic / underline / strikethrough / inverse /
  dimColor, alone and in combination.
* Wrap modes (the layout engine already covers most cases; PR4 adds a
  few style-overlay checks).
* Nested Text with colour inheritance / override.

Expected ANSI strings follow chalk's level-3 output: a single
``\\x1b[0m`` reset terminates each run.
"""

from __future__ import annotations

from pyink import Box, Text, render_to_string
from pyink.components.text import Text as TextDirect
from pyink.core.element import Element

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Element construction
# ---------------------------------------------------------------------------


def test_text_creates_host_element() -> None:
    el = Text("hello")
    assert isinstance(el, Element)
    assert el.type == "text"
    assert el.children == ("hello",)


def test_text_direct_import_matches_public() -> None:
    assert TextDirect is Text


def test_text_accepts_callable_child() -> None:
    el = Text(lambda: "lazy")
    assert callable(el.children[0])


def test_text_multiple_string_children() -> None:
    el = Text("Hello", " ", "World")
    assert el.children == ("Hello", " ", "World")


def test_text_filters_none_and_bool() -> None:
    el = Text("a", None, True, False, "b")
    assert el.children == ("a", "b")


def test_text_no_children() -> None:
    el = Text()
    assert el.children == ()


def test_text_props_passthrough() -> None:
    el = Text("x", color="red", bold=True, wrap="truncate")
    assert el.props["color"] == "red"
    assert el.props["bold"] is True
    assert el.props["wrap"] == "truncate"


# ---------------------------------------------------------------------------
# Plain rendering
# ---------------------------------------------------------------------------


def test_text_plain_renders_verbatim() -> None:
    assert render_to_string(Text("Hello World")) == "Hello World"


def test_text_callable_evaluated_once() -> None:
    calls = {"n": 0}

    def lazy() -> str:
        calls["n"] += 1
        return "computed"

    assert render_to_string(Text(lazy)) == "computed"
    assert calls["n"] == 1


def test_text_empty_string_renders_empty() -> None:
    assert render_to_string(Text("")) == ""


def test_text_multiple_string_children_concat() -> None:
    assert render_to_string(Text("Hello", " ", "World")) == "Hello World"


# ---------------------------------------------------------------------------
# Colours — ported from ink text.tsx
# ---------------------------------------------------------------------------


def test_text_color_named() -> None:
    out = render_to_string(Text("Test", color="green"))
    assert out == f"{ESC}[32mTest{ESC}[0m"


def test_text_color_red() -> None:
    assert render_to_string(Text("Test", color="red")) == f"{ESC}[31mTest{ESC}[0m"


def test_text_color_hex() -> None:
    out = render_to_string(Text("Test", color="#FF8800"))
    assert out == f"{ESC}[38;2;255;136;0mTest{ESC}[0m"


def test_text_color_hex_short() -> None:
    out = render_to_string(Text("Test", color="#f00"))
    assert out == f"{ESC}[38;2;255;0;0mTest{ESC}[0m"


def test_text_color_rgb() -> None:
    out = render_to_string(Text("Test", color="rgb(255, 136, 0)"))
    assert out == f"{ESC}[38;2;255;136;0mTest{ESC}[0m"


def test_text_color_ansi256() -> None:
    out = render_to_string(Text("Test", color="ansi256(194)"))
    assert out == f"{ESC}[38;5;194mTest{ESC}[0m"


def test_text_background_color_named() -> None:
    assert render_to_string(Text("Test", backgroundColor="green")) == f"{ESC}[42mTest{ESC}[0m"


def test_text_background_color_hex() -> None:
    out = render_to_string(Text("Test", backgroundColor="#FF8800"))
    assert out == f"{ESC}[48;2;255;136;0mTest{ESC}[0m"


def test_text_background_color_rgb() -> None:
    out = render_to_string(Text("Test", backgroundColor="rgb(255, 136, 0)"))
    assert out == f"{ESC}[48;2;255;136;0mTest{ESC}[0m"


def test_text_background_color_ansi256() -> None:
    out = render_to_string(Text("Test", backgroundColor="ansi256(194)"))
    assert out == f"{ESC}[48;5;194mTest{ESC}[0m"


# ---------------------------------------------------------------------------
# Style toggles
# ---------------------------------------------------------------------------


def test_text_bold() -> None:
    assert render_to_string(Text("X", bold=True)) == f"{ESC}[1mX{ESC}[0m"


def test_text_italic() -> None:
    assert render_to_string(Text("X", italic=True)) == f"{ESC}[3mX{ESC}[0m"


def test_text_underline() -> None:
    assert render_to_string(Text("X", underline=True)) == f"{ESC}[4mX{ESC}[0m"


def test_text_strikethrough() -> None:
    assert render_to_string(Text("X", strikethrough=True)) == f"{ESC}[9mX{ESC}[0m"


def test_text_inverse() -> None:
    assert render_to_string(Text("X", inverse=True)) == f"{ESC}[7mX{ESC}[0m"


def test_text_dim_color() -> None:
    assert render_to_string(Text("X", dimColor=True)) == f"{ESC}[2mX{ESC}[0m"


def test_text_dim_then_color_order() -> None:
    # ink order: dim, fg, bg, bold, ...
    out = render_to_string(Text("X", dimColor=True, color="green"))
    assert out == f"{ESC}[2m{ESC}[32mX{ESC}[0m"


def test_text_dim_color_then_bold() -> None:
    out = render_to_string(Text("X", dimColor=True, bold=True))
    assert out == f"{ESC}[2m{ESC}[1mX{ESC}[0m"


def test_text_color_dim_combined() -> None:
    """chalk.green.dim('Test') — dim first then color (ink Text order)."""
    out = render_to_string(Text("Test", color="green", dimColor=True))
    assert out == f"{ESC}[2m{ESC}[32mTest{ESC}[0m"


def test_text_color_and_background() -> None:
    out = render_to_string(Text("X", color="red", backgroundColor="blue"))
    assert out == f"{ESC}[31m{ESC}[44mX{ESC}[0m"


def test_text_all_styles_combined() -> None:
    out = render_to_string(
        Text(
            "X",
            color="red",
            backgroundColor="blue",
            bold=True,
            italic=True,
            underline=True,
            strikethrough=True,
            inverse=True,
            dimColor=True,
        )
    )
    # Order: dim, fg, bg, bold, italic, underline, strikethrough, inverse.
    expected = (
        f"{ESC}[2m{ESC}[31m{ESC}[44m{ESC}[1m{ESC}[3m{ESC}[4m{ESC}[9m{ESC}[7mX{ESC}[0m"
    )
    assert out == expected


# ---------------------------------------------------------------------------
# Wrap modes — ported from ink components.tsx
# ---------------------------------------------------------------------------


def test_text_wrap_default() -> None:
    """Default wrap mode word-wraps when width is constrained."""
    out = render_to_string(Box(Text("Hello World"), width=7))
    assert out == "Hello\nWorld"


def test_text_wrap_explicit() -> None:
    out = render_to_string(Box(Text("Hello World", wrap="wrap"), width=7))
    assert out == "Hello\nWorld"


def test_text_wrap_hard() -> None:
    """Hard wrap splits mid-word."""
    out = render_to_string(Box(Text("Hello World", wrap="hard"), width=7))
    assert out == "Hello W\norld"


def test_text_wrap_truncate_end() -> None:
    out = render_to_string(Box(Text("Hello World", wrap="truncate"), width=7))
    assert out == "Hello …"


def test_text_wrap_truncate_middle() -> None:
    out = render_to_string(Box(Text("Hello World", wrap="truncate-middle"), width=7))
    assert out == "Hel…rld"


def test_text_wrap_truncate_start() -> None:
    out = render_to_string(Box(Text("Hello World", wrap="truncate-start"), width=7))
    assert out == "… World"


def test_text_wrapped_with_style_resets_each_line() -> None:
    """Regression: a styled Text that wraps to multiple lines must emit a
    balanced SGR open/reset pair on *each* output line.

    Before the fix, ``apply_style`` wrapped the whole multi-line string,
    placing the opener on the first line and the lone reset on the last
    line. ``_paint_text`` then split that styled string on ``\\n`` and
    wrote each line into its own grid row, so:

    * The first row carried the opener with no reset — letting the
      foreground colour / dim / bold run leak past the text into
      adjacent cells (border edges, background fill, padding) on that
      row (the "background colour overflow" reported against the
      nested-layout example).
    * Middle rows carried neither opener nor reset, so they lost the
      style entirely.
    """
    out = render_to_string(Box(Text("Hello World Foo", color="green"), width=7))
    lines = out.split("\n")
    assert len(lines) == 3, f"expected 3 wrapped lines, got {lines!r}"
    green_open = f"{ESC}[32m"
    reset = f"{ESC}[0m"
    for line in lines:
        assert line.startswith(green_open), (
            f"line {line!r} missing the green opener — style did not re-open per line"
        )
        assert line.endswith(reset), (
            f"line {line!r} missing the SGR reset — style leaks past the text"
        )


def test_text_wrapped_with_dim_does_not_styling_leak_into_sibling_border() -> None:
    """Regression: when a styled wrapped Text sits inside a bordered box,
    the trailing cells (padding, the right border edge) on the *first*
    wrapped row must not inherit the text's foreground style.

    Reproduces the nested-layout bug: dim text wrapped inside an inner
    round box painted its dim run across the inner right border ``│``
    on the first row because the reset lived on the last wrapped row.
    """
    long_text = "This is a long paragraph that wraps inside the box."
    out = render_to_string(
        Box(
            Box(Text(long_text, dimColor=True), padding=1, borderStyle="single"),
            width=20,
        )
    )
    # Find the first wrapped text line — it starts with the dim opener.
    dim_open = f"{ESC}[2m"
    reset = f"{ESC}[0m"
    text_lines = [ln for ln in out.split("\n") if dim_open in ln]
    assert text_lines, "expected at least one dim-styled wrapped row"
    first = text_lines[0]
    # The dim opener must be paired with a reset on the *same* line,
    # before the trailing " │" border edge — otherwise the dim run
    # leaks across the border on this row.
    open_idx = first.index(dim_open)
    reset_idx = first.index(reset)
    assert reset_idx > open_idx, "reset must follow the opener"
    # And nothing styled should appear after the reset on this row.
    assert reset not in first[reset_idx + len(reset) :], (
        "unexpected second SGR run after reset — style leaks past text"
    )


# ---------------------------------------------------------------------------
# Nested Text — colour overlay via sibling Text nodes
# ---------------------------------------------------------------------------
# PR4 does not implement ink's full nested-Text transform pipeline.
# Sibling Text nodes inside a Box each carry their own styling, which
# covers the realistic inheritance / override case.


def test_text_sibling_colors_overlay() -> None:
    """Two sibling Text nodes retain their independent colours."""
    out = render_to_string(
        Box(
            Text("green", color="green"),
            Text("red", color="red"),
            alignItems="flex-start",
        )
    )
    assert out == f"{ESC}[32mgreen{ESC}[0m{ESC}[31mred{ESC}[0m"


def test_text_inherits_box_background() -> None:
    """Text inside a Box with backgroundColor inherits the colour."""
    out = render_to_string(
        Box(
            Text("Hello"),
            backgroundColor="green",
            alignItems="flex-start",
        )
    )
    assert out == f"{ESC}[42mHello{ESC}[0m"


def test_text_explicit_background_overrides_inherited() -> None:
    """Text.backgroundColor wins over the inherited Box background."""
    out = render_to_string(
        Box(
            Text("Hello", backgroundColor="blue"),
            backgroundColor="red",
            alignItems="flex-start",
        )
    )
    assert out == f"{ESC}[44mHello{ESC}[0m"


# ---------------------------------------------------------------------------
# Edge cases — PR4 check list spot checks
# ---------------------------------------------------------------------------


def test_text_rgb_out_of_range_is_passed_through() -> None:
    """rgb(300, 0, 0) overflows the 0-255 range; we emit it verbatim.

    The terminal clamps on display; PyInk does not pre-clamp so the
    emitted sequence is a faithful mirror of the user's spec. This
    matches ``chalk`` behaviour.
    """
    out = render_to_string(Text("Hi", color="rgb(300, 0, 0)"))
    assert out == f"{ESC}[38;2;300;0;0mHi{ESC}[0m"


def test_text_nested_text_inner_style_dropped() -> None:
    """Nested Text styling is dropped (PR4 documented limitation).

    Inner ``Text(color="red")`` is flattened into its string body —
    ink's full nested-Text transform pipeline is not implemented in
    the MVP. The string content survives verbatim.
    """
    out = render_to_string(Text("outer", Text("inner", color="red")))
    assert out == "outerinner"


def test_text_newline_inside_text_inserts_break() -> None:
    """``Text("a", Newline(), "b")`` round-trips with a line break."""
    from pyink import Newline

    assert render_to_string(Text("a", Newline(), "b")) == "a\nb"


def test_text_three_level_nested_boxes_render_correctly() -> None:
    """Three layers of nested bordered boxes compose without corruption."""
    tree = Box(
        Box(
            Box(Text("deep"), borderStyle="single"),
            borderStyle="round",
            padding=1,
        ),
        alignSelf="flex-start",
    )
    out = render_to_string(tree)
    expected = "\n".join(
        [
            "╭────────╮",
            "│        │",
            "│ ┌────┐ │",
            "│ │deep│ │",
            "│ └────┘ │",
            "│        │",
            "╰────────╯",
        ]
    )
    assert out == expected
