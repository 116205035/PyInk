"""Tests for :mod:`ink.layout.measure` (PR3)."""

from __future__ import annotations

import pytest

from ink.layout.measure import (
    string_width,
    wrap_text,
)

# ---------------------------------------------------------------------------
# string_width
# ---------------------------------------------------------------------------


def test_string_width_ascii() -> None:
    assert string_width("abc") == 3
    assert string_width("") == 0
    assert string_width("a b") == 3


def test_string_width_cjk() -> None:
    # Chinese / Japanese / Korean characters are double-width.
    assert string_width("你好") == 4
    assert string_width("a你") == 3
    assert string_width("日本語") == 6


def test_string_width_emoji() -> None:
    # Most emoji take 2 cells; wcwidth reports 2 for "🎉".
    assert string_width("🎉") == 2


def test_string_width_ansi_escape_zero_width() -> None:
    # SGR colour codes should not contribute to display width.
    assert string_width("\x1b[31mred\x1b[0m") == 3
    assert string_width("\x1b[1;32mok\x1b[0m") == 2
    assert string_width("\x1b[0m") == 0


def test_string_width_combining_marks() -> None:
    # "é" can be a single composed codepoint (width 1) or a base + combining
    # acute accent. The composed form must report 1.
    assert string_width("é") == 1
    # Base "e" + combining acute (U+0301) → still width 1 visually.
    assert string_width("é") == 1


def test_string_width_mixed() -> None:
    # 'a' (1) + 'b' (1) + ANSI (0) + '你' (2) + ANSI (0) + 'c' (1) = 5.
    assert string_width("ab\x1b[31m你\x1b[0mc") == 1 + 1 + 2 + 1


# ---------------------------------------------------------------------------
# string_width — grapheme clusters (PR1)
# ---------------------------------------------------------------------------


def test_string_width_emoji_presentation_sequence() -> None:
    # Skull-and-crossbones + VS16 (U+2620 U+FE0F) — base codepoint is
    # narrow text; VS16 forces emoji presentation → 2 cells.
    assert string_width("☠️") == 2
    # Snowflake + VS16.
    assert string_width("❄️") == 2
    # Arrow + VS16.
    assert string_width("➡️") == 2
    # Heart + VS16.
    assert string_width("❤️") == 2


def test_string_width_emoji_keycap_sequence() -> None:
    # Digit + VS16 + combining enclosing keycap (U+20E3) → one cluster, 2 cells.
    assert string_width("1️⃣") == 2
    # Asterisk keycap.
    assert string_width("*️⃣") == 2
    # Hash keycap.
    assert string_width("#️⃣") == 2


def test_string_width_emoji_zwj_family() -> None:
    # Man + ZWJ + Woman + ZWJ + Girl — UAX #29 clusters into a single
    # grapheme → 2 cells.
    assert string_width("\U0001f468‍\U0001f469‍\U0001f467") == 2
    # Couple with heart: Woman + ZWJ + heavy black heart + VS16 + ZWJ + Man.
    assert string_width("\U0001f469‍❤️‍\U0001f468") == 2


def test_string_width_regional_flag() -> None:
    # Two regional indicator letters form a single flag grapheme → 2 cells.
    assert string_width("\U0001f1e8\U0001f1f3") == 2  # China
    assert string_width("\U0001f1fa\U0001f1f8") == 2  # United States
    assert string_width("\U0001f1ea\U0001f1fa") == 2  # European Union


def test_string_width_skin_tone_modifier() -> None:
    # Thumbs up + medium skin tone (U+1F3FD) → one cluster, 2 cells.
    assert string_width("\U0001f44d\U0001f3fd") == 2
    # Handshake + medium skin tone.
    assert string_width("\U0001f91d\U0001f3fd") == 2


def test_string_width_vs15_text_presentation() -> None:
    # Heart + VS15 (U+FE0E) → text presentation, narrow width 1.
    assert string_width("❤︎") == 1
    # NOTE: 🎉︎ (party popper U+1F389 + VS15) is a known wcwidth 0.8.1 bug
    # where it returns 2 instead of 1 (default-emoji codepoint ignores
    # VS15). We accept upstream behaviour for MVP — no assertion here.


def test_string_width_mixed_clusters_ascii() -> None:
    # 'a' (1) + skull-presentation (2) + 'b' (1) = 4.
    assert string_width("a☠️b") == 4


def test_string_width_mixed_clusters_cjk() -> None:
    # '你' (2) + skull-presentation (2) + '好' (2) = 6.
    assert string_width("你☠️好") == 6


def test_string_width_cluster_with_ansi() -> None:
    # Skull-presentation cluster wrapped in SGR colour codes → 2 cells.
    assert string_width("\x1b[31m☠️\x1b[0m") == 2


# ---------------------------------------------------------------------------
# wrap_text — modes
# ---------------------------------------------------------------------------


def test_wrap_text_word_mode_basic() -> None:
    lines = wrap_text("hello world", 5, mode="wrap")
    assert lines == ["hello", "world"]


def test_wrap_text_word_mode_preserves_long_word() -> None:
    # A word longer than the width is hard-split.
    lines = wrap_text("abcdefgh", 3, mode="wrap")
    assert lines == ["abc", "def", "gh"]


def test_wrap_text_word_mode_multiple_spaces() -> None:
    # Word wrap collapses whitespace at line boundaries (trailing trimmed).
    lines = wrap_text("aa bb cc", 4, mode="wrap")
    # 'aa bb' has width 5 > 4, so first line is 'aa', rest wraps.
    assert lines[0] == "aa"
    assert "".join(lines).replace("\n", "") == "aabbcc"


def test_wrap_text_hard_mode() -> None:
    lines = wrap_text("abcdef", 2, mode="hard")
    assert lines == ["ab", "cd", "ef"]


def test_wrap_text_truncate_end_default() -> None:
    # width 8: 7 chars + ellipsis = 8 visible cells.
    assert wrap_text("hello world", 8, mode="truncate") == ["hello w…"]
    assert wrap_text("hello world", 8, mode="truncate-end") == ["hello w…"]


def test_wrap_text_truncate_start() -> None:
    # width 8: ellipsis + 7 chars from tail.
    assert wrap_text("hello world", 8, mode="truncate-start") == ["…o world"]


def test_wrap_text_truncate_middle() -> None:
    # head // 2 = 3 ("hel") + ellipsis + tail (8 - 1 - 3 = 4 → "orld")
    assert wrap_text("hello world", 8, mode="truncate-middle") == ["hel…orld"]


def test_wrap_text_truncate_no_change_when_fits() -> None:
    assert wrap_text("hi", 8, mode="truncate") == ["hi"]


def test_wrap_text_truncate_too_small_for_ellipsis() -> None:
    # width 1 means no room for "…" + content; clamp to ellipsis only.
    assert wrap_text("hello", 1, mode="truncate") == ["…"]


def test_wrap_text_preserves_newlines() -> None:
    lines = wrap_text("a\nb\nc", 10, mode="wrap")
    assert lines == ["a", "b", "c"]


def test_wrap_text_ansi_aware_word_wrap() -> None:
    # ANSI sequences survive but don't count toward width.
    s = "\x1b[31mhello world\x1b[0m"
    lines = wrap_text(s, 5, mode="wrap")
    # The visible content "hello" should land on the first line.
    assert "hello" in lines[0]
    assert "world" in lines[1]
    # And both lines retain the escape somewhere.
    joined = "\n".join(lines)
    assert "\x1b[31m" in joined
    assert "\x1b[0m" in joined


def test_wrap_text_ansi_aware_truncate() -> None:
    s = "\x1b[32mhello world\x1b[0m"
    out = wrap_text(s, 8, mode="truncate")
    assert out == ["\x1b[32mhello w…\x1b[0m"]


def test_wrap_text_empty_input() -> None:
    assert wrap_text("", 5) == [""]


def test_wrap_text_zero_width_returns_input_verbatim() -> None:
    # Defensive: a 0-width target short-circuits.
    assert wrap_text("abc", 0) == ["abc"]


def test_wrap_text_cjk_wraps_by_display_width() -> None:
    # Each CJK char is 2 cells; width 4 holds 2 chars per line.
    lines = wrap_text("你好世界", 4, mode="hard")
    assert lines == ["你好", "世界"]


@pytest.mark.parametrize(
    ("text", "width", "mode", "expected"),
    [
        ("hello world", 5, "wrap", ["hello", "world"]),
        ("hello world", 11, "wrap", ["hello world"]),
        ("hello world", 8, "truncate", ["hello w…"]),
        ("hello world", 8, "truncate-start", ["…o world"]),
        ("abcdefghij", 4, "hard", ["abcd", "efgh", "ij"]),
    ],
)
def test_wrap_text_parametrised(
    text: str, width: int, mode: str, expected: list[str]
) -> None:
    assert wrap_text(text, width, mode=mode) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# truncate-end with embedded newlines (Bug 4 regression)
# ---------------------------------------------------------------------------


def test_truncate_end_produces_single_row_for_long_text() -> None:
    """Long text under ``truncate-end`` yields exactly one truncated row.

    Regression: ``wrap_text(mode="truncate-end")`` used to fan a long
    single-paragraph string into multiple wrapped rows (the mode was
    treated as ``"wrap"`` for measurement). The fix splits the input
    on embedded newlines first, then truncates each paragraph to one
    row — so a single long line stays one row.
    """
    long_text = "x" * 100
    out = wrap_text(long_text, 20, mode="truncate-end")
    assert len(out) == 1, f"expected 1 row, got {len(out)}: {out!r}"
    # The visible width fits within 20 cells.
    assert string_width(out[0]) <= 20


def test_truncate_end_preserves_newline_split_for_multi_line() -> None:
    """Multi-paragraph input under ``truncate-end`` keeps one row per paragraph."""
    out = wrap_text("short\n" + "x" * 50 + "\nend", 20, mode="truncate-end")
    assert len(out) == 3
    assert out[0] == "short"
    assert string_width(out[1]) <= 20
    assert out[2] == "end"


# ---------------------------------------------------------------------------
# string_width — wrap atomicity on grapheme clusters (PR2)
# ---------------------------------------------------------------------------

_SKULL = "☠️"  # skull + VS16 → presentation sequence, width 2
_KEYCAP = "1️⃣"  # digit + VS16 + combining keycap, width 2
_ZWJ_FAMILY = "\U0001f468‍\U0001f469‍\U0001f467"  # man+woman+girl, width 2
_FLAG_CN = "\U0001f1e8\U0001f1f3"
_FLAG_US = "\U0001f1fa\U0001f1f8"


def test_wrap_never_splits_presentation_sequence() -> None:
    lines = wrap_text(_SKULL * 3, 4, mode="wrap")
    assert lines == [_SKULL * 2, _SKULL]
    for line in lines:
        assert line == _SKULL * (len(line) // len(_SKULL))


def test_wrap_never_splits_keycap() -> None:
    lines = wrap_text(_KEYCAP * 3, 4, mode="wrap")
    assert lines == [_KEYCAP * 2, _KEYCAP]


def test_wrap_never_splits_zwj_family() -> None:
    lines = wrap_text(_ZWJ_FAMILY + "!", 2, mode="hard")
    assert lines == [_ZWJ_FAMILY, "!"]


def test_wrap_never_splits_regional_flag() -> None:
    lines = wrap_text(_FLAG_CN + _FLAG_US, 2, mode="hard")
    assert lines == [_FLAG_CN, _FLAG_US]


def test_wrap_hard_mode_preserves_cluster_boundary() -> None:
    # Clusters: ('a',1), ('☠️',2), ('b',1) at width 2.
    # 'a' fills line 1 (1 cell); 'a' + '☠️' would be 3 > 2 so ☠️ breaks
    # to its own line 2; 'b' breaks to line 3.
    lines = wrap_text("a" + _SKULL + "b", 2, mode="hard")
    assert lines == ["a", _SKULL, "b"]


def test_wrap_hard_mode_cluster_wider_than_width_overflows() -> None:
    # A 2-cell ZWJ family in a 1-cell column must not loop or split.
    lines = wrap_text(_ZWJ_FAMILY, 1, mode="hard")
    assert lines == [_ZWJ_FAMILY]


def test_wrap_truncate_end_keeps_cluster() -> None:
    out = wrap_text("a" + _SKULL + "b", 3, mode="truncate")
    assert len(out) == 1
    assert string_width(out[0]) <= 3
    # Cluster atomic: either both halves of ☠️ present or neither.
    assert out[0].count("☠") == out[0].count("️")


def test_wrap_truncate_start_keeps_cluster() -> None:
    out = wrap_text("a" + _SKULL + "b", 3, mode="truncate-start")
    assert len(out) == 1
    assert string_width(out[0]) <= 3
    # Cluster atomic: either both halves of ☠️ present or neither.
    assert out[0].count("☠") == out[0].count("️")
