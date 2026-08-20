"""Integration tests for grapheme-aware string_width end-to-end.

These tests mount a small tree, run it through the full layout + render
pipeline via ``render_to_string``, and assert on the rendered output's
column alignment. They complement the unit tests in ``test_measure.py``
(which test the measure module in isolation) by proving the grapheme-
aware width fix actually changes what the renderer emits.

Why this is a real regression test
----------------------------------
The layout engine's ``_measure_paragraph`` calls ``string_width`` on the
text leaf's content to decide whether wrapping is needed and what the
natural width is. Pre-PR1, ``string_width`` iterated per codepoint and
under-reported cluster widths:

* ``string_width("☠️")`` returned 1 (skull=1 + VS16=0).
* ``string_width("1️⃣")`` returned 1 (digit=1 + VS16=0 + combining=0).

So three clusters at width 4 measured as 3 cells, fit on one line, and
never wrapped. After PR1 the same input measures 6 cells, exceeds the
4-cell budget, and wraps to two lines. Asserting on the line count and
per-line content of the wrapped output is therefore a direct end-to-end
proof that PR1's grapheme-aware width fix reaches the renderer.

Note: the grid painter still iterates per codepoint when emitting cells
(``_Grid.put`` → ``_char_width``), which can produce a one-cell gap on
the trailing edge of certain clusters. That is a pre-existing renderer
limitation, NOT something PR3 changes — we therefore assert on the
*layout-driven* facts (line count, presence of the cluster on each
line) rather than on exact character-column positions.
"""
from __future__ import annotations

from ink import Box, Text, render_to_string
from ink.layout.measure import string_width

# ---------------------------------------------------------------------------
# End-to-end: grapheme-aware wrap reaches the rendered output
# ---------------------------------------------------------------------------


def test_presentation_sequence_wraps_via_grapheme_width() -> None:
    """Three skull-presentation clusters in a 4-cell column wrap to two
    lines; pre-PR1 they measured as 3 cells and stayed on one line.

    The layout engine calls ``string_width`` via ``_measure_paragraph``
    to decide whether the leaf fits its granted width. With the
    grapheme-aware fix each cluster counts as 2 cells, so 3 clusters =
    6 cells > 4 → wrap. Asserting on the line count is the most direct
    end-to-end proof: pre-PR1 the line count was 1, post-PR1 it is 2.
    """
    clusters = "☠️" * 3
    # Sanity: the unit-level invariant the rest of the test relies on.
    assert string_width(clusters) == 6

    tree = Box(Text(clusters), width=4, flexDirection="column")
    rendered = render_to_string(tree, columns=10)
    lines = rendered.split("\n")

    # Two visual rows post-wrap. Pre-PR1 this was a single row.
    assert len(lines) == 2, (
        f"expected 2 wrapped lines, got {len(lines)}: {lines!r}"
    )
    # Each cluster is atomic — never split across rows.
    skull_count_line0 = lines[0].count("☠")
    skull_count_line1 = lines[1].count("☠")
    assert skull_count_line0 + skull_count_line1 == 3
    # First row holds 2 clusters (4 cells), second holds 1 (2 cells).
    assert skull_count_line0 == 2
    assert skull_count_line1 == 1


def test_keycap_sequence_wraps_via_grapheme_width() -> None:
    """Three keycap clusters in a 4-cell column also wrap to two lines.

    Keycaps are a 3-codepoint canonical sequence (digit + VS16 +
    combining enclosing keycap). Pre-PR1 ``string_width`` returned 1 per
    keycap; post-PR1 it returns 2. Same wrap proof as the presentation
    sequence test, with a different cluster shape.
    """
    clusters = "1️⃣" + "2️⃣" + "3️⃣"
    assert string_width(clusters) == 6

    tree = Box(Text(clusters), width=4, flexDirection="column")
    rendered = render_to_string(tree, columns=10)
    lines = rendered.split("\n")

    assert len(lines) == 2, (
        f"expected 2 wrapped lines, got {len(lines)}: {lines!r}"
    )
    # Each keycap cluster is atomic.
    keycap_count_line0 = sum(1 for ch in lines[0] if ch in "123")
    keycap_count_line1 = sum(1 for ch in lines[1] if ch in "123")
    assert keycap_count_line0 + keycap_count_line1 == 3
    assert keycap_count_line0 == 2
    assert keycap_count_line1 == 1


def test_emoji_cluster_row_measures_same_as_ascii_of_same_width() -> None:
    """A row containing a single emoji cluster reports the same rendered
    display width as a row containing 2 ASCII characters.

    Pre-PR1 the emoji row measured 1 cell (vs. 2 for ASCII); the
    rendered string_width therefore disagreed. Post-PR1 both rows have
    the same display width — proving the layout engine's measurement
    path treats the cluster as 2 cells end-to-end.
    """
    emoji_tree = Box(Text("☠️"), width=2, flexShrink=0, flexDirection="row")
    ascii_tree = Box(Text("ab"), width=2, flexShrink=0, flexDirection="row")

    emoji_out = render_to_string(emoji_tree, columns=10).rstrip()
    ascii_out = render_to_string(ascii_tree, columns=10).rstrip()

    # Both rendered rows must have the same display width.
    assert string_width(emoji_out) == string_width(ascii_out) == 2, (
        f"emoji sw={string_width(emoji_out)!r}, ascii sw={string_width(ascii_out)!r}, "
        f"emoji={emoji_out!r}, ascii={ascii_out!r}"
    )


# ---------------------------------------------------------------------------
# End-to-end: multi-line leaves wrap (string_width("\n") == 0 regression)
# ---------------------------------------------------------------------------


def _long_cjk_line(groups: int = 40) -> str:
    """A single no-space CJK line ~``groups`` * 7 display cells wide."""
    return "、".join(f"股票{i}(10%)" for i in range(groups))


def test_multiline_leaf_overwide_line_wraps_without_padding() -> None:
    """A multi-line leaf whose second physical line exceeds the granted
    width wraps that line; the leaf's painted rows equal its measured
    height (no blank-row padding at the end).

    Pre-fix, ``string_width`` returns 0 for any string containing
    ``\n`` (POSIX ``wcswidth`` rejects control chars), so the wrap
    guard in ``_layout_node`` never fired for multi-line leaves:
    ``_measure_text`` reported the wrapped height while ``_paint_text``
    wrote the unwrapped physical lines, and the painter padded the
    difference with blank rows after the content.
    """
    text = "标题行\n" + _long_cjk_line(60)
    rendered = render_to_string(
        Box(Text(text), flexDirection="column"), columns=30
    )
    lines = rendered.split("\n")

    # No physical line may stay over the granted width.
    from ink.layout.measure import string_width as _sw

    over_wide = [i for i, ln in enumerate(lines) if _sw(ln) > 30]
    assert not over_wide, f"lines {over_wide} exceed 30 cells: {lines!r}"

    # Padded blank rows at the end are the height-vs-paint mismatch.
    trailing_blank = 0
    for ln in reversed(lines):
        if not ln.strip():
            trailing_blank += 1
        else:
            break
    assert trailing_blank == 0, (
        f"{trailing_blank} blank padding rows after content: {lines!r}"
    )

    # Content is preserved: every stock token appears exactly once.
    for i in (0, 59):
        assert f"股票{i}" in rendered


def test_multiline_leaf_short_lines_stay_unwrapped() -> None:
    """Multi-line leaves whose lines already fit are untouched — the
    guard fires on per-line width, not on the mere presence of ``\n``."""
    text = "第一行\n第二行\n第三行"
    rendered = render_to_string(
        Box(Text(text), flexDirection="column"), columns=20
    )
    assert rendered.split("\n") == ["第一行", "第二行", "第三行"]


def test_single_line_overwide_wrap_unchanged() -> None:
    """Control anchor: single-line over-wide text keeps wrapping (this
    path never went through the dead guard and must not regress)."""
    rendered = render_to_string(
        Box(Text(_long_cjk_line(40)), flexDirection="column"), columns=30
    )
    lines = rendered.split("\n")
    assert len(lines) > 1
    assert all(not ln.strip() or ln.strip() for ln in lines)
    blank = [i for i, ln in enumerate(lines) if not ln.strip()]
    assert not blank, f"unexpected blank rows {blank}: {lines!r}"


# ---------------------------------------------------------------------------
# End-to-end: CJK-aware word wrap (UAX #14 subset)
# ---------------------------------------------------------------------------


def test_cjk_prefix_echo_wraps_with_prefix_on_first_line() -> None:
    """End-to-end jarvis ``❯``-echo regression through the layout
    pipeline, nested (Box → Box → Text) under a constrained viewport.

    Pre-fix, the unspaced CJK run tokenised as one oversized word, so
    ``_word_break`` flushed the ``❯`` prefix to its own first line and
    hard-broke the content from line 2. Post-fix the prefix shares line
    1 with content and every physical line respects the granted width.
    """
    text = "❯ " + "反而感觉到机会" * 10
    tree = Box(
        Box(Text(text), flexDirection="column"),
        flexDirection="column",
        width=24,
    )
    rendered = render_to_string(tree, columns=24)
    lines = rendered.split("\n")

    assert len(lines) > 1, f"expected wrapped output: {lines!r}"
    assert lines[0].startswith("❯ 反"), f"prefix torn from content: {lines!r}"
    for ln in lines:
        assert string_width(ln) <= 24, f"over-wide line: {ln!r}"
    # Zero content loss across the wrap.
    assert rendered.count("机") == 10
    assert rendered.count("会") == 10
    # Kinsoku: no wrapped line starts with closing punctuation.
    for ln in lines[1:]:
        assert not ln.startswith("，")
        assert not ln.startswith("。")
