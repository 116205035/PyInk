"""Tests for :mod:`pyink.layout.measure` (PR3)."""

from __future__ import annotations

import pytest

from pyink.layout.measure import (
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
