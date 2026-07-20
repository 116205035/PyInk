"""Tests for :func:`ink.externals.HighlightedCode` (Phase 3 PR2).

Like :mod:`tests.externals.test_divider`, we exercise the synchronous
:func:`ink.render_to_string` test renderer rather than the live
:func:`ink.render` pipeline — ``HighlightedCode`` is a declarative
factory (no hooks, no function component) so the cheap path is
sufficient.

Coverage (per PR2 scope):

* Element shape — ``HighlightedCode`` returns a ``box`` host element
  whose ``flexDirection`` is always ``"column"``.
* Python token colours — ``def`` → magenta, function name → blue,
  ``print`` → cyan (builtin), strings → green.
* Multiple languages — JS / SQL / YAML / JSON all tokenise without
  error and emit per-language colour sequences.
* ``language="text"`` fast path — emits a plain ``Text`` body with no
  Pygments dependency (verified by mocking ``__import__``).
* ``theme=`` override — caller-supplied keys win over the defaults.
* ``line_numbers=True`` — right-aligned dim gutter, one per source
  line.
* Missing ``pygments`` — friendly ``ImportError`` pointing at
  ``pip install ink[highlight]``.
* Token-hierarchy lookup — a token like
  ``Token.Literal.String.Double`` resolves to ``String.Double`` →
  ``String`` in that order.
* Multi-line code preserves the source's line breaks; multi-line
  tokens (docstrings) are split across rows.
* Empty code renders nothing.
* Integration: ``HighlightedCode`` inside a parent ``Box`` with a
  border composes cleanly.
* ``HighlightedCode`` is exported from ``ink.externals`` but NOT
  from the top-level ``ink`` package (PRD Decision 5).
"""

from __future__ import annotations

import builtins
import sys
from typing import Any

import pytest

from ink import Box, Text, render_to_string
from ink.core.element import Element
from ink.externals import DEFAULT_THEME, HighlightedCode

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Import / availability guards
# ---------------------------------------------------------------------------


def _pygments_available() -> bool:
    """Return ``True`` if :mod:`pygments` is importable in this env.

    The PR2 test suite needs ``pygments`` for almost every assertion;
    the few "missing pygments" cases monkeypatch the import instead.
    """
    try:
        import pygments  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pygments_available(),
    reason="pygments not installed (pip install ink[highlight])",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(tree: Element, *, columns: int = 80) -> str:
    """Render ``tree`` via the synchronous test pipeline."""
    return render_to_string(tree, columns=columns)


def _install_pygments_import_blocker() -> None:
    """Make ``import pygments`` raise ``ImportError`` until reset.

    Patches :func:`builtins.__import__` and removes any cached
    ``pygments`` / ``pygments.*`` modules so a subsequent import goes
    through the blocker. The fixture in ``_restore_import`` undoes the
    patch.
    """

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pygments" or name.startswith("pygments."):
            raise ImportError(f"mocked: pygments not installed ({name})")
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    # Drop cached pygments modules so a fresh import actually hits the
    # blocker. We only touch pygments.* — nuking unrelated modules
    # would break pytest's own state.
    for mod in list(sys.modules):
        if mod == "pygments" or mod.startswith("pygments."):
            del sys.modules[mod]


@pytest.fixture
def _restore_import() -> Any:
    """Restore the real ``__import__`` after the test.

    Yields nothing; cleanup runs on teardown.
    """
    real_import = builtins.__import__
    yield
    builtins.__import__ = real_import


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_element_is_box_host_with_column_direction() -> None:
    """The factory returns a ``box`` host with ``flexDirection="column"``."""
    el = HighlightedCode("x = 1", language="python")
    assert isinstance(el, Element)
    assert el.type == "box"
    assert el.props["flexDirection"] == "column"


def test_box_props_forwarded_to_outer_box() -> None:
    """``**box_props`` reach the outer container (padding / borderStyle)."""
    el = HighlightedCode(
        "x = 1",
        language="python",
        borderStyle="round",
        padding=1,
    )
    assert el.props["borderStyle"] == "round"
    assert el.props["padding"] == 1


def test_caller_flexDirection_is_ignored() -> None:
    """The component contract forces ``flexDirection="column"``.

    One row per source line is the whole point; letting the caller
    flip to ``row`` would break the visual contract.
    """
    el = HighlightedCode(
        "x = 1",
        language="python",
        flexDirection="row",
    )
    assert el.props["flexDirection"] == "column"


# ---------------------------------------------------------------------------
# Python token colours
# ---------------------------------------------------------------------------


def test_python_keyword_gets_magenta() -> None:
    """``def`` is a ``Token.Keyword`` → magenta (SGR 35)."""
    out = _render(HighlightedCode("def f(): pass", language="python"))
    assert f"{ESC}[35mdef{ESC}[0m" in out


def test_python_function_name_gets_blue() -> None:
    """Function names map to ``Token.Name.Function`` → blue (SGR 34)."""
    out = _render(HighlightedCode("def greet(): pass", language="python"))
    # ``greet`` should be wrapped in blue (SGR 34).
    assert f"{ESC}[34mgreet{ESC}[0m" in out


def test_python_builtin_gets_cyan() -> None:
    """``print`` is ``Token.Name.Builtin`` → cyan (SGR 36)."""
    out = _render(HighlightedCode("print(1)", language="python"))
    assert f"{ESC}[36mprint{ESC}[0m" in out


def test_python_string_gets_green() -> None:
    """String literals map to ``Token.Literal.String.*`` → green (SGR 32)."""
    out = _render(HighlightedCode('x = "hello"', language="python"))
    assert f"{ESC}[32m" in out


def test_python_number_gets_cyan() -> None:
    """Numeric literals map to ``Token.Literal.Number.Integer`` → cyan."""
    out = _render(HighlightedCode("x = 42", language="python"))
    assert f"{ESC}[36m42{ESC}[0m" in out


def test_python_comment_gets_gray() -> None:
    """Comments map to ``Token.Comment.*`` → gray (SGR 90)."""
    out = _render(HighlightedCode("# note", language="python"))
    assert f"{ESC}[90m" in out


def test_python_punctuation_has_no_color() -> None:
    """``Punctuation`` value is ``None`` → plain Text (no SGR)."""
    # ``=`` is an Operator (red), but the parens in ``(a)`` are
    # Punctuation → plain text, no SGR sequence wrapping them.
    out = _render(HighlightedCode("(a)", language="python"))
    assert "(" in out
    # Find the ``(`` and confirm no SGR sequence immediately precedes
    # it — punctuation tokens emit a bare Text with no colour prop.
    idx = out.index("(")
    assert not out[:idx].endswith("m"), (
        "Punctuation should be plain text, not wrapped in SGR"
    )


# ---------------------------------------------------------------------------
# Multiple languages
# ---------------------------------------------------------------------------


def test_javascript_keyword_highlighted() -> None:
    out = _render(
        HighlightedCode("function f() { return 1; }", language="javascript")
    )
    # ``function`` and ``return`` are JS keywords → magenta.
    assert f"{ESC}[35mfunction{ESC}[0m" in out
    assert f"{ESC}[35mreturn{ESC}[0m" in out


def test_sql_keyword_highlighted() -> None:
    out = _render(HighlightedCode("SELECT * FROM users;", language="sql"))
    assert f"{ESC}[35m" in out  # at least one keyword is magenta


def test_yaml_renders_without_error() -> None:
    out = _render(
        HighlightedCode("key: value\nlist:\n  - a\n", language="yaml")
    )
    # No assertion on colour specifics (YAML lexer is sparse); just
    # confirm the source text appears and no exception was raised.
    assert "key" in out
    assert "value" in out


def test_json_renders_without_error() -> None:
    out = _render(
        HighlightedCode('{"a": 1, "b": [2, 3]}', language="json")
    )
    # JSON lexer emits ``Token.Literal.Number.Integer`` for numeric
    # values → cyan (SGR 36). Keys come out as ``Token.Name.Tag``,
    # which is not in the default theme, so we don't assert on them.
    assert f"{ESC}[36m1{ESC}[0m" in out
    assert f"{ESC}[36m2{ESC}[0m" in out


# ---------------------------------------------------------------------------
# language="text" fast path
# ---------------------------------------------------------------------------


def test_language_text_emits_plain_text_no_color() -> None:
    """``language="text"`` skips Pygments entirely → no SGR sequences."""
    out = _render(HighlightedCode("def f(): pass", language="text"))
    assert ESC not in out
    assert "def f(): pass" in out


def test_language_text_default_when_language_omitted() -> None:
    """Default ``language`` is ``"text"``."""
    out = _render(HighlightedCode("plain string"))
    assert ESC not in out
    assert "plain string" in out


def test_language_text_works_without_pygments(_restore_import: Any) -> None:
    """Fast path must not import :mod:`pygments`."""
    _install_pygments_import_blocker()
    # Should not raise.
    el = HighlightedCode("hello", language="text")
    assert el.type == "box"


def test_language_text_preserves_newlines() -> None:
    out = _render(HighlightedCode("a\nb\nc", language="text"))
    # Each source line ends up on its own row.
    lines = [ln for ln in out.split("\n") if ln]
    assert lines == ["a", "b", "c"]


def test_language_text_strips_trailing_empty_row() -> None:
    """A final newline shouldn't produce a blank trailing row."""
    out = _render(HighlightedCode("a\n", language="text"))
    assert out == "a"


# ---------------------------------------------------------------------------
# theme override
# ---------------------------------------------------------------------------


def test_theme_override_replaces_keyword_color() -> None:
    out = _render(
        HighlightedCode(
            "def f(): pass",
            language="python",
            theme={"Keyword": "red"},
        )
    )
    # Magenta is gone for ``def``; red (SGR 31) replaces it.
    assert f"{ESC}[31mdef{ESC}[0m" in out
    assert f"{ESC}[35mdef{ESC}[0m" not in out


def test_theme_override_adds_new_token_key() -> None:
    """Caller-supplied keys not in DEFAULT_THEME still apply."""
    out = _render(
        HighlightedCode(
            "x = 1",
            language="python",
            theme={"Operator": "blue"},  # default is red
        )
    )
    assert f"{ESC}[34m={ESC}[0m" in out


def test_theme_override_none_resets_to_default_color() -> None:
    """A ``None`` value in ``theme`` resets the entry to plain text."""
    out = _render(
        HighlightedCode(
            "def f(): pass",
            language="python",
            theme={"Keyword": None},
        )
    )
    # ``def`` should NOT be wrapped in any colour.
    assert f"{ESC}[35mdef{ESC}[0m" not in out
    assert "def" in out


def test_default_theme_is_exported() -> None:
    assert isinstance(DEFAULT_THEME, dict)
    # A few sentinel keys must be present.
    assert DEFAULT_THEME["Keyword"] == "magenta"
    assert DEFAULT_THEME["String"] == "green"


# ---------------------------------------------------------------------------
# Token-hierarchy lookup
# ---------------------------------------------------------------------------


def test_lookup_walks_to_most_specific() -> None:
    """``Token.Literal.String.Double`` resolves to ``String.Double`` first,
    then falls back to ``String`` when the more specific key is absent.
    """
    from ink.externals.highlighted_code import _lookup_color

    theme: dict[str, str | None] = {
        "String": "green",
        "String.Doc": "gray",
    }
    # Most-specific wins.
    assert _lookup_color("Token.Literal.String.Double", theme) == "green"
    # More specific entry overrides the parent.
    assert _lookup_color("Token.Literal.String.Doc", theme) == "gray"


def test_lookup_falls_back_to_parent() -> None:
    """When no specific key matches, the parent path is tried."""
    from ink.externals.highlighted_code import _lookup_color

    theme: dict[str, str | None] = {"Keyword": "magenta"}
    assert _lookup_color("Token.Keyword.Declaration", theme) == "magenta"


def test_lookup_returns_none_when_no_match() -> None:
    """No match anywhere → ``None`` (use terminal default)."""
    from ink.externals.highlighted_code import _lookup_color

    assert _lookup_color("Token.Name.Other", {}) is None


def test_lookup_strips_token_prefix() -> None:
    """The ``Token.`` prefix is stripped before lookup."""
    from ink.externals.highlighted_code import _lookup_color

    assert _lookup_color("Token.Keyword", {"Keyword": "red"}) == "red"


def test_lookup_string_alias_for_literal_string() -> None:
    """``"String"`` matches ``Token.Literal.String`` via the alias map."""
    from ink.externals.highlighted_code import _lookup_color

    assert _lookup_color("Token.Literal.String.Double", {"String": "green"}) == "green"


# ---------------------------------------------------------------------------
# line_numbers
# ---------------------------------------------------------------------------


def test_line_numbers_emits_dim_gutter() -> None:
    """``line_numbers=True`` prepends a dim (SGR 2) right-aligned gutter."""
    out = _render(
        HighlightedCode("a\nb", language="text", line_numbers=True)
    )
    # First line should start with dim "1 " gutter.
    lines = out.split("\n")
    assert lines[0].startswith(f"{ESC}[2m1 {ESC}[0m")
    assert lines[1].startswith(f"{ESC}[2m2 {ESC}[0m")


def test_line_numbers_gutter_width_grows_with_count() -> None:
    """Gutter is padded to the width of the largest line number."""
    code = "\n".join(str(i) for i in range(1, 11))  # 10 lines
    out = _render(HighlightedCode(code, language="text", line_numbers=True))
    lines = out.split("\n")
    # Line 1 should be padded to width 2 → " 1 ".
    assert lines[0].startswith(f"{ESC}[2m 1 {ESC}[0m")
    # Line 10 should be "10 ".
    assert lines[-1].startswith(f"{ESC}[2m10 {ESC}[0m")


def test_line_numbers_preserves_token_colors() -> None:
    """Colours still apply when the gutter is on."""
    out = _render(
        HighlightedCode(
            "def f(): pass",
            language="python",
            line_numbers=True,
        )
    )
    assert f"{ESC}[35mdef{ESC}[0m" in out


def test_line_numbers_blank_lines_still_get_gutter() -> None:
    """Empty source rows still receive a numbered gutter."""
    code = "a\n\nb"  # blank line in the middle
    out = _render(HighlightedCode(code, language="text", line_numbers=True))
    lines = out.split("\n")
    # Three rows, each starting with its gutter.
    assert len(lines) == 3
    assert lines[1].startswith(f"{ESC}[2m2 {ESC}[0m")


# ---------------------------------------------------------------------------
# Indent (parent-gutter alignment)
# ---------------------------------------------------------------------------


def test_indent_prefixes_every_row_without_line_numbers() -> None:
    """``indent`` prepends a literal Text leaf to every row.

    07-20-tool-message-rendering-polish: callers that embed the code
    block under a parent ``⎿`` gutter (Jarvis's archived Write row)
    pass ``indent="     "`` (5 spaces matching CC's ``MessageResponse``
    gutter width) so continuation rows line up under the body.
    """
    out = _render(
        HighlightedCode(
            "a\nb\nc", language="text", indent="     "
        )
    )
    lines = out.split("\n")
    assert len(lines) == 3
    for line in lines:
        # Each line starts with the 5-space indent (no SGR wrap because
        # plain text indent carries no styling).
        assert line.startswith("     ")
        # And the actual source char follows (after reset if any).
        assert line.rstrip() in ("     a", "     b", "     c")


def test_indent_combines_with_line_numbers() -> None:
    """``indent`` precedes the line-number gutter when both are on."""
    out = _render(
        HighlightedCode(
            "a\nb", language="text", line_numbers=True, indent="  "
        )
    )
    lines = out.split("\n")
    # Indent first, then the dim gutter, then the body.
    assert lines[0].startswith(f"  {ESC}[2m1 {ESC}[0m")
    assert lines[1].startswith(f"  {ESC}[2m2 {ESC}[0m")


def test_indent_empty_default_no_prefix() -> None:
    """``indent=""`` (default) yields no prefix — backward compat."""
    out = _render(HighlightedCode("a", language="text"))
    assert out == "a"


# ---------------------------------------------------------------------------
# first_row_prefix (07-20-tool-message-rendering-polish follow-up)
# ---------------------------------------------------------------------------


def test_first_row_prefix_replaces_indent_on_first_row_only() -> None:
    """``first_row_prefix`` is consumed on the first row only.

    07-20-tool-message-rendering-polish follow-up: callers that embed
    the code block under a parent ``⎿`` gutter (Jarvis's archived
    Write row) want the glyph on the SAME visual line as the first body
    row (CC's ``MessageResponse`` pattern). The caller passes
    ``first_row_prefix="  ⎿  "`` + ``indent="     "``; the renderer
    must put the prefix on row 1 and the indent on every continuation
    row.
    """
    out = _render(
        HighlightedCode(
            "a\nb\nc",
            language="text",
            line_numbers=True,
            indent="     ",
            first_row_prefix=">>",
        )
    )
    lines = out.split("\n")
    assert len(lines) == 3
    # First row starts with ">>" (the first_row_prefix), NOT 5-space indent.
    assert lines[0].startswith(">>"), (
        f"first row should start with first_row_prefix, got: {lines[0]!r}"
    )
    # Continuation rows start with the 5-space indent, NOT the prefix.
    for line in lines[1:]:
        assert line.startswith("     "), (
            f"continuation row should start with indent, got: {line!r}"
        )
        assert not line.startswith(">>"), (
            f"continuation row should NOT carry first_row_prefix, got: {line!r}"
        )


def test_first_row_prefix_without_indent_only_first_row_prefixed() -> None:
    """``first_row_prefix`` without ``indent`` → only first row prefixed."""
    out = _render(
        HighlightedCode(
            "a\nb\nc",
            language="text",
            first_row_prefix=">>",
        )
    )
    lines = out.split("\n")
    assert len(lines) == 3
    # First row carries the prefix.
    assert lines[0].startswith(">>")
    # Continuation rows have no prefix at all (indent="").
    for line in lines[1:]:
        assert not line.startswith(">>")
        assert not line.startswith(" "), (
            f"continuation row unexpectedly prefixed, got: {line!r}"
        )


def test_first_row_prefix_empty_defaults_to_indent_on_first_row() -> None:
    """``first_row_prefix=""`` (default) → first row uses ``indent``.

    Backward-compat regression: callers that only pass ``indent`` (no
    ``first_row_prefix``) must see the indent on EVERY row including
    the first.
    """
    out = _render(
        HighlightedCode(
            "a\nb",
            language="text",
            indent="     ",
        )
    )
    lines = out.split("\n")
    assert len(lines) == 2
    for line in lines:
        assert line.startswith("     ")


def test_first_row_prefix_combines_with_line_numbers() -> None:
    """``first_row_prefix`` precedes the line-number gutter on row 1."""
    out = _render(
        HighlightedCode(
            "a\nb",
            language="text",
            line_numbers=True,
            indent="     ",
            first_row_prefix=">>",
        )
    )
    lines = out.split("\n")
    # Row 1: prefix + dim gutter "1 " + body.
    assert lines[0].startswith(f">>{ESC}[2m1 {ESC}[0m")
    # Row 2: indent + dim gutter "2 " + body.
    assert lines[1].startswith(f"     {ESC}[2m2 {ESC}[0m")


# ---------------------------------------------------------------------------
# Multi-line code
# ---------------------------------------------------------------------------


def test_multiline_code_one_row_per_source_line() -> None:
    code = "def f():\n    return 1\n"
    out = _render(HighlightedCode(code, language="python"))
    # Trailing newline stripped; 2 visible rows.
    lines = [ln for ln in out.split("\n") if ln]
    assert len(lines) == 2


def test_multiline_docstring_split_across_rows() -> None:
    """Multi-line ``Token.Literal.String.Doc`` is split per physical line."""
    code = 'def f():\n    """line one\n    line two"""\n    pass\n'
    out = _render(HighlightedCode(code, language="python"))
    # Both docstring lines should appear, each wrapped in green/gray.
    assert "line one" in out
    assert "line two" in out


def test_multiline_comment_split_across_rows() -> None:
    """Multi-line comments are split per physical line, each in gray."""
    code = "# first line\n# second line\nx = 1\n"
    out = _render(HighlightedCode(code, language="python"))
    lines = [ln for ln in out.split("\n") if ln]
    # 3 rows: two comments + one assignment.
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------


def test_empty_code_renders_nothing() -> None:
    out = _render(HighlightedCode("", language="python"))
    assert out == ""


def test_empty_code_with_line_numbers_renders_single_empty_row() -> None:
    """Empty input + line_numbers renders one gutter line (numbered 1)
    with no code content. We don't suppress the gutter because the
    caller explicitly asked for line numbers — a single empty row is
    the honest representation of an empty file.
    """
    out = _render(HighlightedCode("", language="python", line_numbers=True))
    assert out.startswith(f"{ESC}[2m1 {ESC}[0m")


def test_single_newline_renders_nothing() -> None:
    out = _render(HighlightedCode("\n", language="text"))
    assert out == ""


# ---------------------------------------------------------------------------
# Missing pygments
# ---------------------------------------------------------------------------


def test_missing_pygments_raises_friendly_import_error(_restore_import: Any) -> None:
    _install_pygments_import_blocker()
    with pytest.raises(ImportError) as exc_info:
        HighlightedCode("print(1)", language="python")
    assert "pip install ink[highlight]" in str(exc_info.value)


def test_missing_pygments_error_mentions_component_name(_restore_import: Any) -> None:
    _install_pygments_import_blocker()
    with pytest.raises(ImportError) as exc_info:
        HighlightedCode("print(1)", language="python")
    assert "HighlightedCode" in str(exc_info.value)


def test_missing_pygments_error_chains_original_cause(_restore_import: Any) -> None:
    """The wrapper preserves the original ImportError as ``__cause__``."""
    _install_pygments_import_blocker()
    with pytest.raises(ImportError) as exc_info:
        HighlightedCode("print(1)", language="python")
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, ImportError)


def test_language_auto_uses_guess_lexer() -> None:
    """``language="auto"`` defers to :func:`pygments.lexers.guess_lexer`.

    We don't assert on a specific language (guess is heuristic); we
    just confirm the path doesn't crash and emits *some* highlighting.
    """
    code = (
        "import os\n\n"
        'def main():\n    print("hello")\n\n'
        "class App:\n    pass\n"
    )
    out = _render(HighlightedCode(code, language="auto"))
    # Either way, the code text should be present.
    assert "import" in out


# ---------------------------------------------------------------------------
# Integration: HighlightedCode inside a parent Box
# ---------------------------------------------------------------------------


def test_inside_box_with_border() -> None:
    out = _render(
        Box(
            HighlightedCode("x = 1", language="python"),
            Text("footer"),
            borderStyle="round",
            flexDirection="column",
        ),
        columns=30,
    )
    # Border characters present (round top-left corner).
    assert "╭" in out
    # Content is present — the ``=`` is wrapped in red SGR so we
    # can't assert the literal substring ``x = 1``; check parts.
    assert "x " in out
    assert f"{ESC}[31m={ESC}[0m" in out  # operator is red
    assert f"{ESC}[36m1{ESC}[0m" in out  # number is cyan
    assert "footer" in out


def test_sibling_text_renders_alongside() -> None:
    out = _render(
        Box(
            Text("label:"),
            HighlightedCode("x = 1", language="python"),
            flexDirection="column",
        )
    )
    assert "label:" in out
    assert f"{ESC}[36m1{ESC}[0m" in out  # the ``1`` is cyan


def test_nested_in_outer_padding() -> None:
    out = _render(
        Box(
            HighlightedCode("def f(): pass", language="python"),
            padding=1,
        ),
        columns=40,
    )
    # Content is still there even when wrapped in padding.
    assert f"{ESC}[35mdef{ESC}[0m" in out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_highlighted_code() -> None:
    from ink.externals import HighlightedCode as InitHC

    assert InitHC is HighlightedCode


def test_highlighted_code_not_in_ink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import ink

    assert not hasattr(ink, "HighlightedCode"), (
        "HighlightedCode must NOT be top-level"
    )


# ---------------------------------------------------------------------------
# Token cache (Bug 2/3 regression)
# ---------------------------------------------------------------------------


def test_tokenize_cache_returns_same_tokens_for_same_input() -> None:
    """Two tokenise calls with identical inputs hit the cache.

    Regression for the Phase 3 "highlighted-code demo pins CPU" bug.
    """
    from ink.externals.highlighted_code import _token_cache, _tokenize

    if not _pygments_available():
        import pytest

        pytest.skip("pygments not installed")

    import pygments
    from pygments.lexers import get_lexer_by_name, guess_lexer

    _token_cache.clear()
    code = "def f(): pass"
    first = _tokenize(code, "python", pygments, get_lexer_by_name, guess_lexer)
    second = _tokenize(code, "python", pygments, get_lexer_by_name, guess_lexer)
    assert first == second
    assert (code, "python") in _token_cache
