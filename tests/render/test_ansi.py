"""Tests for :mod:`pyink.render.ansi` (PR4).

Verifies colour parsing, style application and border rendering
byte-for-byte against the equivalent ``chalk`` output ink's test suite
expects.
"""

from __future__ import annotations

from pyink.render.ansi import (
    BORDER_STYLES,
    apply_style,
    parse_color,
    render_border,
    resolve_border_chars,
    style_segment,
)

# ---------------------------------------------------------------------------
# parse_color
# ---------------------------------------------------------------------------

ESC = "\x1b"


def test_parse_color_named_foreground() -> None:
    assert parse_color("red", type_="foreground") == "31"
    assert parse_color("green", type_="foreground") == "32"
    assert parse_color("blue", type_="foreground") == "34"


def test_parse_color_named_background() -> None:
    assert parse_color("red", type_="background") == "41"
    assert parse_color("white", type_="background") == "47"


def test_parse_color_named_bright() -> None:
    assert parse_color("redBright", type_="foreground") == "91"
    assert parse_color("cyanBright", type_="background") == "106"
    assert parse_color("gray", type_="foreground") == "90"


def test_parse_color_hex_six() -> None:
    assert parse_color("#ff0000", type_="foreground") == "38;2;255;0;0"
    assert parse_color("#ff8800", type_="background") == "48;2;255;136;0"


def test_parse_color_hex_three() -> None:
    assert parse_color("#f00", type_="foreground") == "38;2;255;0;0"
    assert parse_color("#abc", type_="foreground") == "38;2;170;187;204"


def test_parse_color_rgb() -> None:
    assert parse_color("rgb(255, 0, 0)", type_="foreground") == "38;2;255;0;0"
    assert parse_color("rgb(255,0,0)", type_="background") == "48;2;255;0;0"
    # Whitespace tolerance inside the parens.
    assert parse_color("rgb( 1 , 2 , 3 )", type_="foreground") == "38;2;1;2;3"


def test_parse_color_ansi256() -> None:
    assert parse_color("ansi256(9)", type_="foreground") == "38;5;9"
    assert parse_color("ansi256( 194 )", type_="background") == "48;5;194"


def test_parse_color_unknown_returns_none() -> None:
    assert parse_color("notacolor") is None
    assert parse_color("") is None
    assert parse_color("#xyz") is None


# ---------------------------------------------------------------------------
# apply_style
# ---------------------------------------------------------------------------


def test_apply_style_no_op_returns_text_unchanged() -> None:
    assert apply_style("hello") == "hello"


def test_apply_style_empty_text() -> None:
    assert apply_style("") == ""


def test_apply_style_color_named() -> None:
    # chalk.green('Test') -> \x1b[32mTest\x1b[0m
    assert apply_style("Test", color="green") == f"{ESC}[32mTest{ESC}[0m"


def test_apply_style_background_named() -> None:
    assert apply_style("Test", backgroundColor="green") == f"{ESC}[42mTest{ESC}[0m"


def test_apply_style_hex_color() -> None:
    assert apply_style("Test", color="#FF8800") == f"{ESC}[38;2;255;136;0mTest{ESC}[0m"


def test_apply_style_rgb_color() -> None:
    assert apply_style("Test", color="rgb(255, 136, 0)") == (
        f"{ESC}[38;2;255;136;0mTest{ESC}[0m"
    )


def test_apply_style_bold() -> None:
    assert apply_style("X", bold=True) == f"{ESC}[1mX{ESC}[0m"


def test_apply_style_dim_then_color_then_bold() -> None:
    # ink order: dim, fg, bg, bold, italic, underline, strikethrough, inverse.
    out = apply_style("X", dimColor=True, color="green", bold=True)
    assert out == f"{ESC}[2m{ESC}[32m{ESC}[1mX{ESC}[0m"


def test_apply_style_all_attributes() -> None:
    out = apply_style(
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
    # Must start with dim and end with reset, contain every opener.
    assert out.startswith(f"{ESC}[2m")
    assert out.endswith(f"{ESC}[0m")
    for opener in (
        f"{ESC}[31m",  # red fg
        f"{ESC}[44m",  # blue bg
        f"{ESC}[1m",  # bold
        f"{ESC}[3m",  # italic
        f"{ESC}[4m",  # underline
        f"{ESC}[9m",  # strikethrough
        f"{ESC}[7m",  # inverse
    ):
        assert opener in out


def test_apply_style_ansi256() -> None:
    assert apply_style("X", color="ansi256(9)") == f"{ESC}[38;5;9mX{ESC}[0m"


# ---------------------------------------------------------------------------
# style_segment (borders)
# ---------------------------------------------------------------------------


def test_style_segment_no_style_returns_segment() -> None:
    assert style_segment("─") == "─"


def test_style_segment_fg_color() -> None:
    # chalk.green('─') — open, char, reset.
    assert style_segment("─", fg="green") == f"{ESC}[32m─{ESC}[0m"


def test_style_segment_fg_then_bg_then_dim_order() -> None:
    # render-border.ts applies fg, then bg, then dim — we emit all
    # openers then the segment then a single reset.
    out = style_segment("─", fg="red", bg="blue", dim=True)
    assert out == f"{ESC}[31m{ESC}[44m{ESC}[2m─{ESC}[0m"


# ---------------------------------------------------------------------------
# resolve_border_chars / BORDER_STYLES
# ---------------------------------------------------------------------------


def test_resolve_border_chars_named() -> None:
    chars = resolve_border_chars("round")
    assert chars["topLeft"] == "╭"
    assert chars["topRight"] == "╮"
    assert chars["bottomLeft"] == "╰"
    assert chars["bottomRight"] == "╯"
    assert chars["top"] == "─"
    assert chars["bottom"] == "─"
    assert chars["left"] == "│"
    assert chars["right"] == "│"


def test_resolve_border_chars_all_styles_have_eight_keys() -> None:
    keys = {"topLeft", "top", "topRight", "right", "bottomRight", "bottom", "bottomLeft", "left"}
    for name, chars in BORDER_STYLES.items():
        assert set(chars.keys()) == keys, f"style {name!r} missing keys"


def test_resolve_border_chars_custom_dict_passes_through() -> None:
    custom = {
        "topLeft": "1",
        "top": "2",
        "topRight": "3",
        "right": "4",
        "bottomRight": "5",
        "bottom": "6",
        "bottomLeft": "7",
        "left": "8",
    }
    assert resolve_border_chars(custom) is custom


def test_resolve_border_chars_unknown_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        resolve_border_chars("unknown-style")


# ---------------------------------------------------------------------------
# render_border
# ---------------------------------------------------------------------------


def test_render_border_round_simple() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round"})
    assert out == [
        "╭───────╮",
        "│Content│",
        "╰───────╯",
    ]


def test_render_border_double() -> None:
    lines = ["Hi"]
    out = render_border(lines, {"borderStyle": "double"})
    assert out == [
        "╔══╗",
        "║Hi║",
        "╚══╝",
    ]


def test_render_border_hide_top() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round", "borderTop": False})
    assert out == [
        "│Content│",
        "╰───────╯",
    ]


def test_render_border_hide_bottom() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round", "borderBottom": False})
    assert out == [
        "╭───────╮",
        "│Content│",
    ]


def test_render_border_hide_left() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round", "borderLeft": False})
    assert out == [
        "───────╮",
        "Content│",
        "───────╯",
    ]


def test_render_border_hide_right() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round", "borderRight": False})
    assert out == [
        "╭───────",
        "│Content",
        "╰───────",
    ]


def test_render_border_hide_all() -> None:
    lines = ["Content"]
    out = render_border(
        lines,
        {
            "borderStyle": "round",
            "borderTop": False,
            "borderBottom": False,
            "borderLeft": False,
            "borderRight": False,
        },
    )
    assert out == ["Content"]


def test_render_border_multiline_content() -> None:
    lines = ["AAA", "BBB"]
    out = render_border(lines, {"borderStyle": "single"})
    assert out == [
        "┌───┐",
        "│AAA│",
        "│BBB│",
        "└───┘",
    ]


def test_render_border_top_color_only() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round", "borderTopColor": "green"})
    assert out[0] == f"{ESC}[32m╭───────╮{ESC}[0m"
    # Other edges should be unstyled.
    assert out[1] == "│Content│"
    assert out[2] == "╰───────╯"


def test_render_border_dim_top() -> None:
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": "round", "borderTopDimColor": True})
    assert out[0] == f"{ESC}[2m╭───────╮{ESC}[0m"
    assert out[1] == "│Content│"


def test_render_border_no_style_returns_input() -> None:
    lines = ["abc"]
    assert render_border(lines, {}) == ["abc"]


def test_render_border_custom_chars() -> None:
    custom = {
        "topLeft": "↘",
        "top": "↓",
        "topRight": "↙",
        "right": "←",
        "bottomRight": "↖",
        "bottom": "↑",
        "bottomLeft": "↗",
        "left": "→",
    }
    lines = ["Content"]
    out = render_border(lines, {"borderStyle": custom})
    assert out == [
        "↘↓↓↓↓↓↓↓↙",
        "→Content←",
        "↗↑↑↑↑↑↑↑↖",
    ]
