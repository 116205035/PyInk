"""Tests for :func:`ink.components.Text` (PR4).

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

from ink import Box, Text, render_to_string
from ink.components.text import Text as TextDirect
from ink.core.element import Element

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
# flushBackgroundToWidth (Path-A fix) — bg spans full layout width
# ---------------------------------------------------------------------------


def test_text_flush_background_to_width_pads_to_layout_width() -> None:
    """``flushBackgroundToWidth=True`` pads the bg to the leaf's layout width.

    Regression test for the "user-message bg only covers the text" bug:
    legacy behaviour put the bg SGR around the visible text only, so on
    a 30-column layout the band stopped at column 2 (the text's width).
    Path-A fix registers a row-level bg that ``to_string`` pads to the
    leaf's full layout width.
    """
    out = render_to_string(
        Text("hi", backgroundColor="red", flushBackgroundToWidth=True)
    )
    # Layout width is the default 80 columns; the bg band now spans
    # the full row. Visible width is 2 chars + 78 spaces of padding.
    assert out.startswith(f"{ESC}[41mhi")
    assert out.endswith(f"{ESC}[0m")
    # The reset is anchored to the row's end — no second line, no leak.
    assert "\n" not in out


def test_text_flush_background_to_width_each_row_self_closes() -> None:
    """Multi-line wrapped text self-closes each row's bg SGR.

    Regression test for the "SGR leak onto sibling rows" bug: legacy
    behaviour wrapped the whole padded string with one open + reset;
    when the layout soft-wrapped that string the closing reset ended
    up on a trailing-whitespace cell that ``rstrip`` then dropped,
    leaving the bg SGR open for whatever the renderer painted next.
    Path-A fix gives each visible row its own open + reset pair.
    """
    long_text = "word " * 12  # 60 chars; wraps at 20 cols
    out = render_to_string(
        Text(long_text, backgroundColor="red", flushBackgroundToWidth=True),
        columns=20,
    )
    lines = out.split("\n")
    # Every line carries its own open + reset pair.
    for line in lines:
        assert line.startswith(f"{ESC}[41m")
        assert line.endswith(f"{ESC}[0m")


def test_text_flush_background_to_width_preserves_foreground_color() -> None:
    """Foreground colour survives the row-level bg wrap."""
    out = render_to_string(
        Text(
            "hi",
            color="blue",
            backgroundColor="red",
            flushBackgroundToWidth=True,
        )
    )
    # The open sequence runs the foreground colour first, then the bg.
    assert f"{ESC}[34m" in out  # blue foreground
    assert f"{ESC}[41m" in out  # red background
    assert "hi" in out


def test_box_flush_background_to_width_spans_interior() -> None:
    """Box with ``flushBackgroundToWidth`` paints the full interior width."""
    tree = Box(
        Text("child"),
        backgroundColor="red",
        width=10,
        alignSelf="flex-start",
        flushBackgroundToWidth=True,
    )
    out = render_to_string(tree)
    # 10 cells of bg. The row carries open + content + reset spanning
    # the box's interior width.
    assert out.startswith(f"{ESC}[41m")
    assert out.endswith(f"{ESC}[0m")
    # No mid-row reset before the trailing one.
    assert out.count(f"{ESC}[0m") == 1


# ---------------------------------------------------------------------------
# flushBackgroundToWidth — empty content must not paint a phantom stripe
# ---------------------------------------------------------------------------


def test_flush_bg_skipped_on_empty_content() -> None:
    """``flushBackgroundToWidth=True`` + empty content -> no background stripe.

    Regression test for the "phantom coloured stripe on idle rows" bug:
    when a Text leaf stays mounted (e.g. Jarvis' ``pending_user_row``)
    but its content is "" (idle state), the row-level bg painter would
    still register a row background — painting an empty coloured stripe
    the user sees as a ghost message. The fix skips
    :meth:`_Grid.mark_row_background` when the styled line has no
    visible characters.
    """
    out = render_to_string(
        Text("", backgroundColor="red", flushBackgroundToWidth=True)
    )
    # No background opener anywhere in the output.
    assert f"{ESC}[41m" not in out
    # The output is just an empty row (no phantom bg band).
    assert out == ""


def test_flush_bg_renders_with_content() -> None:
    """``flushBackgroundToWidth=True`` + content -> background stripe present.

    Sanity check that the empty-content guard did not regress the normal
    case: a row with visible text still paints the bg band spanning the
    layout width.
    """
    out = render_to_string(
        Text("hi", backgroundColor="red", flushBackgroundToWidth=True)
    )
    assert out.startswith(f"{ESC}[41m")
    assert out.endswith(f"{ESC}[0m")
    assert "hi" in out


def test_flush_bg_skipped_on_sgr_only_content() -> None:
    """``flushBackgroundToWidth=True`` + SGR-only content -> no background.

    Edge case: when ``_apply_text_style`` wraps an empty string the
    rendered line may carry ANSI SGR sequences (e.g. a bare ``\x1b[0m``)
    but no visible characters. The visible-content check must strip the
    SGR runs before deciding, otherwise the row would still paint a
    phantom stripe. We construct the scenario by rendering an empty Text
    with a foreground colour (which applies an SGR wrap even when the
    text body is empty).
    """
    out = render_to_string(
        Text(
            "",
            color="blue",
            backgroundColor="red",
            flushBackgroundToWidth=True,
        )
    )
    # No background opener — the row had no visible characters after
    # stripping SGR sequences.
    assert f"{ESC}[41m" not in out
    # Output is an empty row.
    assert out == ""


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
    from ink import Newline

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


# ---------------------------------------------------------------------------
# scroll_offset — Phase 5 public API for vertical viewport control
# ---------------------------------------------------------------------------
# When a Text leaf's content has more lines than the layout grants rows,
# ``scroll_offset`` slides a ``height``-tall window down by N rows. The
# prop accepts a plain ``int``, a ``Signal[int]`` (layout subscribes to
# changes), or a ``Callable[[], int]`` (same subscription semantics as
# other callable style props). ``None`` (default) keeps the leading rows.


def test_text_scroll_offset_none_shows_from_top() -> None:
    """Default (no scroll_offset) keeps the leading rows.

    Matches ink's ``<Box height={n}>`` truncation behaviour and the
    historic pre-Phase-5 semantics.
    """
    out = render_to_string(
        Box(
            Text("L0\nL1\nL2\nL3\nL4"),
            height=3,
        )
    )
    assert out == "L0\nL1\nL2"


def test_text_scroll_offset_int_shows_from_offset() -> None:
    """``scroll_offset=2`` shows rows ``[2, 2+height)`` from the text."""
    out = render_to_string(
        Box(
            Text("L0\nL1\nL2\nL3\nL4", scroll_offset=2),
            height=3,
        )
    )
    assert out == "L2\nL3\nL4"


def test_text_scroll_offset_zero_matches_default() -> None:
    """``scroll_offset=0`` is the same as unset (top-keeping)."""
    out = render_to_string(
        Box(
            Text("L0\nL1\nL2\nL3\nL4", scroll_offset=0),
            height=3,
        )
    )
    assert out == "L0\nL1\nL2"


def test_text_scroll_offset_clamps_at_max() -> None:
    """Offset past the end clamps to the last valid window.

    A 5-line text in a 3-row box has at most ``len-height = 2`` as a
    valid offset (window ``[2, 5)`` = last 3 lines). Anything larger
    pins to that same last window instead of over-scrolling past the
    content.
    """
    out = render_to_string(
        Box(
            Text("L0\nL1\nL2\nL3\nL4", scroll_offset=99),
            height=3,
        )
    )
    assert out == "L2\nL3\nL4"


def test_text_scroll_offset_callable_dynamic() -> None:
    """``Callable[[], int]`` is evaluated at layout time (matches other props)."""
    out = render_to_string(
        Box(
            Text("L0\nL1\nL2\nL3\nL4", scroll_offset=lambda: 2),
            height=3,
        )
    )
    assert out == "L2\nL3\nL4"


def test_text_scroll_offset_with_height_zero_no_scroll() -> None:
    """``height=0`` grants no rows so no content is painted at all.

    Establishes that scroll_offset does not invent rows when the layout
    grants zero — both the offset and the clip cooperate to produce an
    empty snapshot.
    """
    out = render_to_string(
        Box(
            Text("L0\nL1\nL2\nL3\nL4", scroll_offset=2),
            height=0,
        )
    )
    assert out == ""


def test_text_scroll_offset_signal_dynamic() -> None:
    """``Signal[int]`` is read at layout time and subscribes the render loop.

    The full reactive-pipeline check: the prop accepts a bare
    :class:`ink.Signal` (no ``lambda`` wrapper needed), the layout
    reads ``.value`` inside the render-loop effect so a subsequent
    write triggers a re-paint that reflects the new offset.
    """
    import io
    import time

    from ink import render
    from ink.core.signal import Signal, signal
    from ink.render.instance import Instance

    offset_sig: Signal[int] = signal(0)

    def App() -> Element:
        return Box(
            Text(
                "L0\nL1\nL2\nL3\nL4",
                scroll_offset=offset_sig,
            ),
            height=3,
        )

    out = io.StringIO()
    inst: Instance = render(
        App(),
        stdout=out,
        stdin=io.StringIO(),
        columns=10,
        rows=5,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.05)
    frame = inst.current_frame
    # Initial paint: offset=0 → leading rows.
    assert "L0" in frame and "L1" in frame and "L2" in frame
    assert "L3" not in frame and "L4" not in frame

    offset_sig.value = 2
    time.sleep(0.15)
    frame = inst.current_frame
    # Updated paint: offset=2 → window slides to the bottom.
    assert "L2" in frame and "L3" in frame and "L4" in frame
    assert "L0" not in frame and "L1" not in frame
    inst.unmount()


# ---------------------------------------------------------------------------
# collapseIfEmpty — idle Text leaf occupies 0 rows instead of 1
# ---------------------------------------------------------------------------
# A leaf that stays mounted but whose content is "" during idle still
# claims 1 row of layout space (PR2 Bug 1 floor). For Jarvis' live-frame
# surfaces (sub-agent blocks / todos / spinner row / task list / hint
# line / picker) that mount once and swap content via a callable, the
# idle frame leaks 6+ blank rows between the message stream and the
# input region. ``collapseIfEmpty=True`` opts into dropping the floor to
# 0 when content is empty — non-empty content keeps the floor at 1 so
# the PR2 Bug 1 overlap fix stays intact.


def test_collapseIfEmpty_empty_text_renders_zero_rows() -> None:
    """``Text("", collapseIfEmpty=True)`` produces no output row."""
    out = render_to_string(Text("", collapseIfEmpty=True), columns=80)
    # 0 rows → empty snapshot (no trailing newline either).
    assert out == ""


def test_collapseIfEmpty_box_with_empty_texts_renders_zero_rows() -> None:
    """A column of two empty collapseIfEmpty Texts produces no rows."""
    out = render_to_string(
        Box(
            Text("", collapseIfEmpty=True),
            Text("", collapseIfEmpty=True),
            flexDirection="column",
        ),
        columns=80,
    )
    assert out == ""


def test_collapseIfEmpty_callable_empty_renders_zero_rows() -> None:
    """A callable leaf returning ``""`` + collapseIfEmpty → 0 rows."""
    out = render_to_string(Text(lambda: "", collapseIfEmpty=True), columns=80)
    assert out == ""


def test_collapseIfEmpty_default_keeps_one_row_for_empty() -> None:
    """``Text("")`` without the prop keeps the legacy 1-row behaviour
    (back-compat: callers that did not opt in see no change)."""
    out = render_to_string(Text(""), columns=80)
    # Legacy behaviour: empty Text renders an empty string (one row of
    # zero visible width, which the trailing-whitespace strip elides to
    # the empty snapshot). The point is that the layout still reserves
    # the row — verified separately via the column-stacking test below.
    assert out == ""


def test_collapseIfEmpty_default_empty_text_in_column_still_occupies_a_row() -> None:
    """Back-compat: ``Text("")`` (no prop) inside a column still claims
    a row, so a sibling below it is offset. ``collapseIfEmpty=True``
    sibling below it is NOT offset (no row claimed)."""
    # Without prop: empty Text claims its 1 row → the "below" Text
    # lands on row 1, snapshot has 2 rows.
    legacy = render_to_string(
        Box(
            Text(""),
            Text("below"),
            flexDirection="column",
        ),
        columns=80,
    )
    assert legacy == "\nbelow"

    # With prop: empty Text claims 0 rows → "below" lands on row 0.
    collapsed = render_to_string(
        Box(
            Text("", collapseIfEmpty=True),
            Text("below"),
            flexDirection="column",
        ),
        columns=80,
    )
    assert collapsed == "below"


def test_collapseIfEmpty_non_empty_content_renders_normally() -> None:
    """``Text("hello", collapseIfEmpty=True)`` renders the text."""
    out = render_to_string(Text("hello", collapseIfEmpty=True), columns=80)
    assert out == "hello"


def test_collapseIfEmpty_non_empty_styled_content_renders_normally() -> None:
    """Non-empty content with style + collapseIfEmpty renders normally."""
    out = render_to_string(
        Text("hi", color="red", collapseIfEmpty=True), columns=80
    )
    assert out == f"{ESC}[31mhi{ESC}[0m"


def test_collapseIfEmpty_interleaved_with_non_empty_in_column() -> None:
    """A column with a mix of empty (collapsed) and non-empty Texts
    packs tightly — only the non-empty rows survive in the snapshot."""
    out = render_to_string(
        Box(
            Text("", collapseIfEmpty=True),
            Text("first"),
            Text("", collapseIfEmpty=True),
            Text("second"),
            Text("", collapseIfEmpty=True),
            flexDirection="column",
        ),
        columns=80,
    )
    assert out == "first\nsecond"
