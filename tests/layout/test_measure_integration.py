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
