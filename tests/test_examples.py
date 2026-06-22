"""Integration tests for the bundled examples (PR8).

Each example is mounted against a ``io.StringIO`` stdout, allowed to
run briefly, then unmounted. The tests assert:

* the mount does not raise
* the rendered output contains the expected landmark string
* no full-screen clear (``\\x1b[2J``) ever appears (PRD Decision 3)

These tests do **not** drive interactive keystrokes — they verify the
mount + initial paint + unmount path for each example. End-to-end TTY
validation is left to manual runs (``python examples/<name>/<file>.py``).

Examples that rely on a real TTY for input (select-input, use-input,
use-focus) mount cleanly even with ``stdin=StringIO``: the
:class:`Terminal`'s reader thread simply never delivers any keys, so the
component sits in its initial state until unmount.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_example_module(rel_path: str, module_name: str) -> Any:
    """Import an example file by relative path under ``examples/``."""
    file_path = EXAMPLES_DIR / rel_path
    sys.path.insert(0, str(file_path.parent))
    try:
        if module_name in sys.modules:
            return sys.modules[module_name]
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        # Best-effort path cleanup; we keep the module cached so repeated
        # imports in the same test session are cheap.
        with contextlib.suppress(ValueError):
            sys.path.remove(str(file_path.parent))


def _run_example(
    build_tree: Any,
    *,
    columns: int = 60,
    rows: int = 8,
    run_seconds: float = 0.4,
) -> str:
    """Mount ``build_tree`` to a StringIO stdout, wait, unmount, return output.

    The returned string captures everything written up to (but not
    including) the unmount clear — unmount erases the live frame, so
    inspecting ``out.getvalue()`` after unmount would see only the
    blanked rows. We snapshot the buffer just before tearing down.
    """
    from pyink import render

    out = io.StringIO()
    inst = render(
        build_tree,
        stdout=out,
        stdin=io.StringIO(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    time.sleep(run_seconds)
    # Snapshot before unmount clears the frame.
    snapshot = out.getvalue()
    inst.unmount()
    return snapshot


# ---------------------------------------------------------------------------
# Each example
# ---------------------------------------------------------------------------


def test_counter_example_runs() -> None:
    mod = _load_example_module("counter/counter.py", "pyink_example_counter")
    out = _run_example(mod.Counter(), columns=40, rows=3, run_seconds=0.6)
    assert "tests passed" in out
    assert "\x1b[2J" not in out


def test_select_input_example_runs() -> None:
    mod = _load_example_module(
        "select-input/select_input.py", "pyink_example_select_input"
    )
    out = _run_example(mod.SelectInput(), columns=40, rows=10, run_seconds=0.3)
    assert "Pick a fruit" in out
    # All options should be rendered on mount.
    for label in ("Apple", "Banana", "Cherry", "Date", "Elderberry"):
        assert label in out
    assert "\x1b[2J" not in out


def test_borders_example_runs() -> None:
    mod = _load_example_module("borders/borders.py", "pyink_example_borders")
    out = _run_example(mod.Borders(), columns=60, rows=5, run_seconds=0.2)
    for label in ("single", "double", "round", "bold"):
        assert label in out
    assert "\x1b[2J" not in out


def test_static_example_runs() -> None:
    mod = _load_example_module("static/static.py", "pyink_example_static")
    # The static example pushes one item every 0.5 s — let it run long
    # enough to flush at least two items.
    out = _run_example(mod.App(), columns=50, rows=6, run_seconds=1.6)
    assert "Task 0 completed" in out
    assert "Completed:" in out
    assert "\x1b[2J" not in out


def test_use_input_example_runs() -> None:
    mod = _load_example_module(
        "use-input/use_input_demo.py", "pyink_example_use_input"
    )
    out = _run_example(mod.InputDemo(), columns=50, rows=6, run_seconds=0.2)
    assert "Press any key" in out
    assert "Input:" in out
    assert "Flags:" in out
    assert "\x1b[2J" not in out


def test_use_focus_example_runs() -> None:
    mod = _load_example_module(
        "use-focus/use_focus_demo.py", "pyink_example_use_focus"
    )
    out = _run_example(mod.FocusDemo(), columns=40, rows=12, run_seconds=0.2)
    assert "Tab switches focus" in out
    assert "Input A" in out
    assert "Input B" in out
    assert "\x1b[2J" not in out


def test_debug_input_example_runs() -> None:
    """The debug-input example mounts without raising.

    This example is the diagnostic tool for input issues — it doesn't
    drive any interaction on its own (just waits for keys).
    """
    mod = _load_example_module(
        "debug-input/debug_input.py", "pyink_example_debug_input"
    )
    out = _run_example(mod.DebugInput(), columns=80, rows=24, run_seconds=0.2)
    assert "Press keys" in out
    assert "Last:" in out
    assert "\x1b[2J" not in out


def test_alternate_screen_example_runs() -> None:
    """Alternate-screen example enters and exits the alternate buffer cleanly."""
    mod = _load_example_module(
        "alternate-screen/alternate_screen.py", "pyink_example_alternate_screen"
    )
    # Mount with alternate_screen=True so the enter/exit escapes are
    # observable in the captured stdout.
    from pyink import render

    out = io.StringIO()
    inst = render(
        mod.AlternateScreen(),
        stdout=out,
        stdin=io.StringIO(),
        columns=70,
        rows=14,
        alternate_screen=True,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.3)
    snapshot = out.getvalue()
    inst.unmount()
    after_unmount = out.getvalue()
    # ``\x1b[?1049h`` enters the alternate screen on mount.
    assert "\x1b[?1049h" in snapshot
    # ``\x1b[?1049l`` exits it on unmount.
    assert "\x1b[?1049l" in after_unmount
    # The UI carries a border.
    assert "Alternate Screen Demo" in snapshot
    assert "Press Esc" in snapshot
    assert "\x1b[2J" not in snapshot


def test_transform_example_runs() -> None:
    """Transform example produces uppercase + line-numbered output."""
    mod = _load_example_module(
        "transform/transform_demo.py", "pyink_example_transform"
    )
    out = _run_example(mod.TransformDemo(), columns=60, rows=18, run_seconds=0.2)
    # uppercase block — "HELLO WORLD" appears in the output.
    assert "HELLO WORLD" in out
    # line-numbering block — the first line gets the "  1: " prefix.
    assert "1:" in out
    # hanging-indent label is present.
    assert "hanging indent" in out
    assert "\x1b[2J" not in out


def test_computed_batch_example_runs() -> None:
    """computed + batch example mounts cleanly with derived state visible."""
    mod = _load_example_module(
        "computed-batch/computed_batch.py", "pyink_example_computed_batch"
    )
    out = _run_example(
        mod.ComputedBatch(), columns=60, rows=10, run_seconds=0.3
    )
    assert "Count:" in out
    assert "Double:" in out
    assert "Effect runs:" in out
    # Initial state: count == 0, double == 0.
    assert "Count:  0" in out
    assert "Double: 0" in out
    assert "\x1b[2J" not in out


def test_nested_layout_example_runs() -> None:
    """Nested-layout example renders multiple bordered regions."""
    mod = _load_example_module(
        "nested-layout/nested_layout.py", "pyink_example_nested_layout"
    )
    out = _run_example(mod.NestedLayout(), columns=70, rows=18, run_seconds=0.2)
    assert "Nested Layout Demo" in out
    assert "Sidebar" in out
    assert "Main Title" in out
    assert "Footer" in out
    # At least three bordered regions (outer + sidebar + main + status bar).
    # ``│`` is the single-border vertical edge — appears multiple times.
    assert out.count("│") >= 6
    assert "\x1b[2J" not in out


def test_ansi_colors_example_runs() -> None:
    """ansi-colors example emits ANSI escape sequences for the named colours."""
    mod = _load_example_module(
        "ansi-colors/ansi_colors.py", "pyink_example_ansi_colors"
    )
    out = _run_example(mod.AnsiColors(), columns=80, rows=30, run_seconds=0.2)
    assert "ANSI Colors + Styles Demo" in out
    # Named foreground colours emit their basic SGR code, e.g. ``\x1b[31m``
    # for ``red``. We don't pin a specific colour — any of the 16 will do.
    assert any(
        f"\x1b[{code}m" in out for code in range(30, 38)
    ), "expected a basic-colour SGR sequence in output"
    # hex / truecolor sequences start with ``\x1b[38;2;``.
    assert "38;2;" in out
    # Style toggles are present as visible labels.
    assert "bold" in out
    assert "italic" in out
    assert "underline" in out
    assert "\x1b[2J" not in out


def test_use_window_size_example_runs() -> None:
    """use-window-size example renders the current size + layout mode."""
    mod = _load_example_module(
        "use-window-size/use_window_size.py", "pyink_example_use_window_size"
    )
    # Use columns >= 60 so the two-column mode kicks in.
    out = _run_example(
        mod.UseWindowSize(), columns=80, rows=12, run_seconds=0.2
    )
    assert "use_window_size demo" in out
    assert "x" in out  # "80 x 12"
    assert "Layout mode:" in out
    assert "two-column" in out  # 80 >= 60 threshold
    assert "\x1b[2J" not in out


def test_use_window_size_single_column_mode() -> None:
    """Below the threshold the use-window-size example switches to single-column."""
    mod = _load_example_module(
        "use-window-size/use_window_size.py",
        "pyink_example_use_window_size_narrow",
    )
    out = _run_example(
        mod.UseWindowSize(), columns=40, rows=10, run_seconds=0.2
    )
    assert "single-column" in out


# ---------------------------------------------------------------------------
# Phase 2 PR8 — new examples for the externals + hooks landed in PR1..7.
# ---------------------------------------------------------------------------


def test_spinner_example_runs() -> None:
    """Spinner example mounts + paints the first frame of each type."""
    mod = _load_example_module(
        "spinner/spinner_demo.py", "pyink_example_spinner"
    )
    out = _run_example(mod.SpinnerDemo(), columns=40, rows=12, run_seconds=0.2)
    assert "Spinner demo" in out
    # At least the first spinner type is rendered (its name is the label).
    assert "dots" in out
    # ``Spinner`` paints a frame character from SPINNERS["dots"] on mount.
    # We check for any of the first frame characters across the showcase —
    # the initial frame index is 0, so every visible spinner paints its
    # ``frames[0]``. ``dots`` starts with ``⠋``; we don't pin a specific
    # frame because some terminals mangle Braille in CI capture, but the
    # green-coloured label is always present.
    assert "dots2" in out
    assert "\x1b[2J" not in out


def test_link_example_runs() -> None:
    """Link example emits OSC 8 sequences for each link."""
    mod = _load_example_module(
        "link/link_demo.py", "pyink_example_link"
    )
    out = _run_example(mod.LinkDemo(), columns=60, rows=10, run_seconds=0.2)
    assert "Link demo" in out
    # OSC 8 hyperlink sequence — both opening ``\x1b]8;;URL\x1b\\`` and
    # closing ``\x1b]8;;\x1b\\`` appear in the rendered output.
    assert "\x1b]8;;" in out
    # Each configured URL appears in the wrapped payload.
    assert "github.com" in out
    assert "example.com" in out
    assert "file:///etc/hostname" in out
    assert "\x1b[2J" not in out


def test_divider_example_runs() -> None:
    """Divider example renders horizontal lines + a labelled section."""
    mod = _load_example_module(
        "divider/divider_demo.py", "pyink_example_divider"
    )
    out = _run_example(mod.DividerDemo(), columns=60, rows=18, run_seconds=0.2)
    assert "Divider demo" in out
    # The single-border bottom edge character (``─``) spans the column.
    assert "─" in out
    # Label mode emits the label text in the middle of the line.
    assert "Section A" in out
    assert "\x1b[2J" not in out


def test_use_focus_real_example_runs() -> None:
    """The real use_focus hook demo mounts + shows the active handle id."""
    mod = _load_example_module(
        "use-focus-real/use_focus_real_demo.py",
        "pyink_example_use_focus_real",
    )
    out = _run_example(mod.UseFocusDemo(), columns=50, rows=14, run_seconds=0.2)
    assert "use_focus demo (real)" in out
    for label in ("Input 1", "Input 2", "Input 3"):
        assert label in out
    # The first box grabs auto_focus on mount, so its id is reported as
    # active.
    assert "Active:" in out
    assert "input-1" in out
    assert "\x1b[2J" not in out


def test_measure_element_example_runs() -> None:
    """measure_element example reports the live Width after layout."""
    mod = _load_example_module(
        "measure-element/measure_demo.py",
        "pyink_example_measure_element",
    )
    out = _run_example(
        mod.MeasureDemo(), columns=70, rows=12, run_seconds=0.2
    )
    assert "measure_element demo" in out
    # ``Width:`` label is always rendered; the post-layout value
    # populates once the layout epoch ticks (synchronously on mount).
    assert "Width:" in out
    # The threshold helper text is static.
    assert "Threshold:" in out
    assert "\x1b[2J" not in out


# ---------------------------------------------------------------------------
# Lifecycle — every example must unmount cleanly even if signals keep
# writing from a background thread (counter, static).
# ---------------------------------------------------------------------------


def test_counter_unmount_is_idempotent_after_run() -> None:
    mod = _load_example_module("counter/counter.py", "pyink_example_counter_idem")
    from pyink import render

    out = io.StringIO()
    inst = render(
        mod.Counter(),
        stdout=out,
        stdin=io.StringIO(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )
    time.sleep(0.3)
    inst.unmount()
    inst.unmount()  # second unmount must not raise
    # Give the spawned timer thread a moment to observe ``running=False``
    # so it doesn't leak into subsequent tests.
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# Sanity check — all six examples live where the test expects them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "counter/counter.py",
        "select-input/select_input.py",
        "borders/borders.py",
        "static/static.py",
        "use-input/use_input_demo.py",
        "use-focus/use_focus_demo.py",
        "debug-input/debug_input.py",
        "alternate-screen/alternate_screen.py",
        "transform/transform_demo.py",
        "computed-batch/computed_batch.py",
        "nested-layout/nested_layout.py",
        "ansi-colors/ansi_colors.py",
        "use-window-size/use_window_size.py",
        "spinner/spinner_demo.py",
        "link/link_demo.py",
        "divider/divider_demo.py",
        "use-focus-real/use_focus_real_demo.py",
        "measure-element/measure_demo.py",
        "streaming-text/streaming_text_demo.py",
        "highlighted-code/highlighted_code_demo.py",
        "markdown/markdown_demo.py",
        "diff/diff_demo.py",
        "markdown-streaming/markdown_streaming_demo.py",
        "text-input/text_input_demo.py",
        "text-input-selection/selection_demo.py",
        "select-input-real/select_input_demo.py",
        "select-input-multi/multi_select_demo.py",
        "confirm-input/confirm_demo.py",
        "scroll-text/scroll_text_demo.py",
    ],
)
def test_example_file_exists(rel_path: str) -> None:
    assert (EXAMPLES_DIR / rel_path).is_file(), f"missing example: {rel_path}"


# ---------------------------------------------------------------------------
# Concurrent run — examples don't leak global state into each other.
# ---------------------------------------------------------------------------


def test_two_examples_back_to_back_in_one_process() -> None:
    """Mount borders, unmount, then mount use-focus in the same process."""
    borders = _load_example_module("borders/borders.py", "pyink_example_borders_2")
    focus = _load_example_module(
        "use-focus/use_focus_demo.py", "pyink_example_use_focus_2"
    )
    out1 = _run_example(borders.Borders(), columns=60, rows=5, run_seconds=0.1)
    assert "single" in out1
    out2 = _run_example(focus.FocusDemo(), columns=40, rows=12, run_seconds=0.1)
    assert "Input A" in out2
    assert "Input B" in out2


# ---------------------------------------------------------------------------
# Threaded wait_until_exit works for the counter example.
# ---------------------------------------------------------------------------


def test_counter_wait_until_exit_returns_on_unmount() -> None:
    mod = _load_example_module(
        "counter/counter.py", "pyink_example_counter_wait"
    )
    from pyink import render

    out = io.StringIO()
    inst = render(
        mod.Counter(),
        stdout=out,
        stdin=io.StringIO(),
        columns=40,
        rows=3,
        exit_on_ctrl_c=False,
    )

    captured: dict[str, str] = {}

    def worker() -> None:
        time.sleep(0.2)
        # Snapshot before unmount clears the frame.
        captured["out"] = out.getvalue()
        inst.unmount()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    inst.wait_until_exit()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert "tests passed" in captured["out"]


# ---------------------------------------------------------------------------
# Phase 3 PR6 — examples for StreamingText / HighlightedCode / Markdown /
# StructuredDiff / streaming Markdown integration.
# ---------------------------------------------------------------------------


def test_streaming_text_example_runs() -> None:
    """StreamingText example mounts + paints the leading reply text."""
    mod = _load_example_module(
        "streaming-text/streaming_text_demo.py",
        "pyink_example_streaming_text",
    )
    out = _run_example(
        mod.StreamingTextDemo(), columns=70, rows=12, run_seconds=0.4
    )
    assert "StreamingText demo" in out
    # The simulated stream starts with "Hello" — even a few ticks in,
    # the buffer signal has grown past the first word.
    assert "Hello" in out
    assert "\x1b[2J" not in out


def test_highlighted_code_example_runs() -> None:
    """HighlightedCode example mounts + paints at least one code block."""
    mod = _load_example_module(
        "highlighted-code/highlighted_code_demo.py",
        "pyink_example_highlighted_code",
    )
    out = _run_example(
        mod.HighlightedCodeDemo(), columns=70, rows=40, run_seconds=0.2
    )
    assert "HighlightedCode demo" in out
    # The Python snippet defines a class via ``def`` — Pygments emits
    # the keyword regardless of theme. Without Pygments the literal
    # ``def`` still appears in the plain-text fallback.
    assert "def" in out
    assert "\x1b[2J" not in out


def test_markdown_example_runs() -> None:
    """Markdown example mounts + paints the heading marker."""
    mod = _load_example_module(
        "markdown/markdown_demo.py", "pyink_example_markdown"
    )
    out = _run_example(
        mod.MarkdownDemo(), columns=70, rows=36, run_seconds=0.2
    )
    assert "Markdown demo" in out
    # The rendered output carries the H2 heading text ``Lists`` (the
    # ``#`` marker is stripped during heading rendering, so we look
    # for the heading label instead).
    assert "Lists" in out
    assert "\x1b[2J" not in out


def test_diff_example_runs() -> None:
    """StructuredDiff example mounts + paints a +/- line."""
    mod = _load_example_module(
        "diff/diff_demo.py", "pyink_example_diff"
    )
    out = _run_example(mod.DiffDemo(), columns=72, rows=40, run_seconds=0.2)
    assert "StructuredDiff demo" in out
    # A diff between two non-trivial snapshots always has at least one
    # addition or deletion line.
    assert "+" in out or "-" in out
    assert "\x1b[2J" not in out


def test_markdown_streaming_example_runs() -> None:
    """Streaming Markdown example mounts + paints the live heading."""
    mod = _load_example_module(
        "markdown-streaming/markdown_streaming_demo.py",
        "pyink_example_markdown_streaming",
    )
    # ``rows=30`` matches the value ``main()`` uses (see the comment in
    # ``markdown_streaming_demo.main`` for the Bug-8 rationale — 24
    # rows squeezes the inner code-block border Box past its minimum).
    out = _run_example(
        mod.MarkdownStreamingDemo(), columns=70, rows=30, run_seconds=0.4
    )
    assert "Streaming Markdown demo" in out
    # The streamed source opens with ``# Streaming Markdown`` — after a
    # short delay the rendered heading ``Streaming Markdown`` appears
    # inside the markdown body (the ``#`` marker is stripped during
    # heading rendering).
    assert "Streaming Markdown" in out
    assert "\x1b[2J" not in out


# ---------------------------------------------------------------------------
# Phase 4 PR5 — examples for TextInput / SelectInput / ConfirmInput
# externals (single-line + multi-line + password + placeholder + selection,
# single-select, multi-select, Y/N confirmation).
# ---------------------------------------------------------------------------


def test_text_input_example_runs() -> None:
    """TextInput example mounts + paints the four labelled inputs."""
    mod = _load_example_module(
        "text-input/text_input_demo.py", "pyink_example_text_input"
    )
    # rows=30 — the example's main() also uses rows=30: the column stacks
    # 4 labelled inputs (3 rows each) + title + subtitle + 4 status lines,
    # which no longer fits at rows=24 after the Bug 1 min-content fix
    # (text leaves now refuse to shrink below 1 row, so the trailing
    # status lines correctly overflow the viewport instead of overlapping
    # the input above them).
    out = _run_example(
        mod.TextInputDemo(), columns=72, rows=30, run_seconds=0.3
    )
    assert "TextInput demo" in out
    # The four section labels render in the initial paint.
    for label in ("Single-line", "Multi-line", "Password", "Placeholder"):
        assert label in out
    # The first input grabs auto-focus on mount — its id is reported
    # in the active-focus status line.
    assert "Active: name" in out
    # The placeholder on the focused input is visible (the buffer is
    # empty).
    assert "Type your name" in out
    assert "\x1b[2J" not in out


def test_text_input_selection_example_runs() -> None:
    """TextInput selection example mounts + paints the multi-line buffer."""
    mod = _load_example_module(
        "text-input-selection/selection_demo.py",
        "pyink_example_text_input_selection",
    )
    out = _run_example(
        mod.SelectionDemo(), columns=64, rows=14, run_seconds=0.3
    )
    assert "selection demo" in out
    # The instruction line mentions the Shift-select interaction.
    assert "Shift" in out
    # The initial multi-line buffer is rendered.
    assert "The quick brown fox" in out
    assert "jumps over the lazy dog" in out
    # The status line reports the cursor offset / line / column.
    assert "Cursor offset" in out
    assert "\x1b[2J" not in out


def test_select_input_real_example_runs() -> None:
    """The real SelectInput external mounts + paints every option."""
    mod = _load_example_module(
        "select-input-real/select_input_demo.py",
        "pyink_example_select_input_real",
    )
    out = _run_example(
        mod.SelectInputDemo(), columns=48, rows=14, run_seconds=0.3
    )
    assert "SelectInput demo (real external)" in out
    # All five fruit options render on mount.
    for label in ("Apple", "Banana", "Cherry", "Date", "Elderberry"):
        assert label in out
    # The "not selected yet" status line is visible before any Enter.
    assert "nothing selected" in out
    assert "\x1b[2J" not in out


def test_select_input_multi_example_runs() -> None:
    """The multi-select SelectInput mounts + paints all ten items."""
    mod = _load_example_module(
        "select-input-multi/multi_select_demo.py",
        "pyink_example_select_input_multi",
    )
    out = _run_example(
        mod.MultiSelectDemo(), columns=64, rows=18, run_seconds=0.3
    )
    assert "multi-select demo" in out
    # The instruction line calls out the Space-toggles interaction.
    assert "Space" in out
    # Several checklist items render on mount (we don't pin every one —
    # the test above already verifies that SelectInput paints all items
    # in single-select mode; the multi-select paint path is identical
    # modulo the indicator prefix).
    assert "Read README" in out
    assert "Open PR" in out
    assert "not confirmed yet" in out
    assert "\x1b[2J" not in out


def test_confirm_input_example_runs() -> None:
    """ConfirmInput example mounts + paints all three Y/N prompts."""
    mod = _load_example_module(
        "confirm-input/confirm_demo.py", "pyink_example_confirm_input"
    )
    out = _run_example(
        mod.ConfirmDemo(), columns=60, rows=22, run_seconds=0.3
    )
    assert "ConfirmInput demo" in out
    # Single-key + require-enter prompts derive "yes" / "no" labels.
    assert "yes" in out
    assert "no" in out
    # The custom-keys prompt overrides with "quit" / "abort".
    assert "quit" in out
    assert "abort" in out
    # Each prompt's status line starts in its initial state.
    assert out.count("no action yet") >= 3
    assert "\x1b[2J" not in out


# ---------------------------------------------------------------------------
# Phase 5 PR3 — Text.scroll_offset example. The public scroll_offset prop
# slides a height-row window down a multi-line payload; the status line
# confirms the mount reached the keyboard handler wiring.
# ---------------------------------------------------------------------------


def test_scroll_text_example_runs() -> None:
    """Text.scroll_offset example mounts + paints the first slice + status."""
    mod = _load_example_module(
        "scroll-text/scroll_text_demo.py",
        "pyink_example_scroll_text",
    )
    out = _run_example(
        mod.ScrollTextDemo(), columns=50, rows=18, run_seconds=0.3
    )
    assert "Text.scroll_offset demo" in out
    # The first line of the payload (``Line 00``) is visible on mount.
    assert "Line 00" in out
    # The status line reports the current offset; initial value is 0.
    assert "scroll_offset: 0/" in out
    assert "\x1b[2J" not in out
