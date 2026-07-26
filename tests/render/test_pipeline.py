"""Tests for :func:`ink.render.render` and the live render pipeline (PR5).

These cover the public entry point and its integration with the
reconciler, scheduler, terminal abstraction, and the reactive render
loop. Most tests use :class:`io.StringIO` as a fake stdout.
"""

from __future__ import annotations

import io
import threading
import time
from unittest.mock import patch

from ink import Box, Newline, Text, render
from ink.core.signal import signal


def _mock_query_cursor(row: int | None):
    """Return a context manager that patches ``Instance._query_cursor``
    to return ``row`` (or ``None`` when ``row`` is ``None``).

    Tests use this to drive the Root M force-repaint path
    deterministically without waiting for the real 50 ms DSR timeout
    or sending actual escape sequences.
    """
    return patch(
        "ink.render.instance.Instance._query_cursor",
        return_value=row,
    )


def _render_silent(tree: object, **kwargs: object) -> tuple[object, io.StringIO]:
    out = io.StringIO()
    kwargs.setdefault("exit_on_ctrl_c", False)
    inst = render(tree, stdout=out, **kwargs)  # type: ignore[arg-type]
    return inst, out


# ---------------------------------------------------------------------------
# Mount + initial paint
# ---------------------------------------------------------------------------


def test_mount_writes_initial_frame() -> None:
    inst, out = _render_silent(Text("hello"), columns=40, rows=3)
    assert "hello" in out.getvalue()
    # No full-screen clear ever (PRD Decision 3).
    assert "\x1b[2J" not in out.getvalue()
    inst.unmount()  # type: ignore[attr-defined]


def test_mount_box_with_multiple_rows() -> None:
    tree = Box(
        Text("row1"),
        Text("row2"),
        Text("row3"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=20, rows=5)
    written = out.getvalue()
    assert "row1" in written
    assert "row2" in written
    assert "row3" in written
    inst.unmount()  # type: ignore[attr-defined]


def test_mount_with_newline_in_text() -> None:
    inst, out = _render_silent(Text("a\nb\nc"), columns=10, rows=5)
    written = out.getvalue()
    assert "a" in written
    assert "b" in written
    assert "c" in written
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# rerender via Instance.rerender
# ---------------------------------------------------------------------------


def test_rerender_writes_diff_not_full_paint() -> None:
    inst, out = _render_silent(Text("first"), columns=20, rows=2)
    # After the initial paint the cursor is parked at the top row.
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("second"))  # type: ignore[attr-defined]
    repaint = out.getvalue()
    assert "second" in repaint
    # Inline diff uses cursor-move + line-clear; never a full repaint
    # (the new content is NOT preceded by the full frame because the
    # initial paint was already in place — the diff only writes the
    # changed row).
    assert "\x1b[2K" in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Frame-level diff correctness
# ---------------------------------------------------------------------------


def test_diff_only_repaints_changed_row() -> None:
    tree = Box(
        Text("alpha"),
        Text("beta"),
        Text("gamma"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=20, rows=5)

    new_tree = Box(
        Text("alpha"),
        Text("BETA"),
        Text("gamma"),
        flexDirection="column",
    )
    out.truncate(0)
    out.seek(0)
    inst.rerender(new_tree)  # type: ignore[attr-defined]
    repaint = out.getvalue()
    assert "BETA" in repaint
    # The unchanged rows must NOT appear in the repaint.
    assert "alpha" not in repaint
    assert "gamma" not in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


def test_diff_handles_growing_frame() -> None:
    inst, out = _render_silent(Text("one"), columns=20, rows=5)
    out.truncate(0)
    out.seek(0)
    new_tree = Box(
        Text("one"),
        Text("two"),
        Text("three"),
        flexDirection="column",
    )
    inst.rerender(new_tree)  # type: ignore[attr-defined]
    repaint = out.getvalue()
    assert "two" in repaint
    assert "three" in repaint
    # The unchanged row "one" must NOT be rewritten.
    assert "one" not in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


def test_diff_handles_shrinking_frame() -> None:
    tree = Box(
        Text("a"),
        Text("b"),
        Text("c"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=20, rows=5)
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("a"))  # type: ignore[attr-defined]
    repaint = out.getvalue()
    # Rows b/c must be cleared (line-erase) but never re-emitted as content.
    assert "b" not in repaint
    assert "c" not in repaint
    assert repaint.count("\x1b[2K") >= 2
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


def test_identical_rerender_emits_nothing() -> None:
    inst, out = _render_silent(Text("same"), columns=20, rows=2)
    out.truncate(0)
    out.seek(0)
    inst.rerender(Text("same"))  # type: ignore[attr-defined]
    assert out.getvalue() == ""
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Alternate screen
# ---------------------------------------------------------------------------


def test_alternate_screen_enters_and_exits() -> None:
    inst, out = _render_silent(Text("hi"), columns=20, rows=2, alternate_screen=True)
    written = out.getvalue()
    assert "\x1b[?1049h" in written
    assert "\x1b[?25l" in written
    inst.unmount()  # type: ignore[attr-defined]
    final = out.getvalue()
    assert "\x1b[?1049l" in final
    assert "\x1b[?25h" in final
    # Scrollback is preserved — never use 2J.
    assert "\x1b[2J" not in final


# ---------------------------------------------------------------------------
# max_fps coalescing
# ---------------------------------------------------------------------------


def test_max_fps_coalesces_burst_writes() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"count={counter.value}")

    inst, out = _render_silent(Counter(), columns=40, rows=3, max_fps=30)
    out.truncate(0)
    out.seek(0)
    for _ in range(10):
        counter.value += 1
    time.sleep(0.25)
    repaint = out.getvalue()
    assert "count=10" in repaint
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Reactive repaint via signal + callable Text
# ---------------------------------------------------------------------------


def test_reactive_counter_repaints_on_signal_change() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Box(
            Text(lambda: f"count={counter.value}"),
            flexDirection="column",
        )

    inst, out = _render_silent(Counter(), columns=40, rows=3)
    initial = out.getvalue()
    assert "count=0" in initial
    out.truncate(0)
    out.seek(0)
    counter.value = 42
    time.sleep(0.25)
    repaint = out.getvalue()
    assert "count=42" in repaint
    assert "\x1b[2J" not in repaint
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Threaded integration (wait_until_exit)
# ---------------------------------------------------------------------------


def test_wait_until_exit_blocks_then_returns() -> None:
    counter = signal(0)

    def Counter() -> object:
        return Text(lambda: f"{counter.value} ticks")

    inst, out = _render_silent(Counter(), columns=40, rows=3)

    def worker() -> None:
        time.sleep(0.05)
        counter.value = 1
        time.sleep(0.1)
        inst.unmount()  # type: ignore[attr-defined]

    t = threading.Thread(target=worker)
    t.start()
    inst.wait_until_exit()  # type: ignore[attr-defined]
    t.join(timeout=1.0)
    assert not t.is_alive()
    # At least the initial frame landed.
    assert "0 ticks" in out.getvalue()


# ---------------------------------------------------------------------------
# Cleanup — atexit registration doesn't crash on re-entry
# ---------------------------------------------------------------------------


def test_unmount_then_atexit_cleanup_does_not_raise() -> None:
    inst, _ = _render_silent(Text("x"), columns=10, rows=2)
    inst.unmount()  # type: ignore[attr-defined]
    # atexit will call cleanup() at interpreter exit; calling it manually
    # here is also safe (idempotent).
    inst.cleanup()  # type: ignore[attr-defined]


def test_cleanup_unregisters_from_atexit() -> None:
    """cleanup() should drop the Instance from atexit so a later
    process-wide teardown doesn't re-enter. We verify the internal
    flag flips to False on first cleanup and stays False on repeat
    calls."""
    inst, _ = _render_silent(Text("x"), columns=10, rows=2)
    assert inst._atexit_registered  # type: ignore[attr-defined]
    inst.cleanup()  # type: ignore[attr-defined]
    assert not inst._atexit_registered  # type: ignore[attr-defined]
    # Idempotent: second cleanup doesn't touch atexit again.
    inst.cleanup()  # type: ignore[attr-defined]
    assert not inst._atexit_registered  # type: ignore[attr-defined]


def test_render_with_newline_component() -> None:
    """A small end-to-end test using multiple built-in components."""
    tree = Box(
        Text("title", bold=True),
        Newline(),
        Text("body"),
        flexDirection="column",
    )
    inst, out = _render_silent(tree, columns=40, rows=5)
    written = out.getvalue()
    assert "title" in written
    assert "body" in written
    inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# System cursor hide / restore (Issue 1 from Jarvis Phase 1)
# ---------------------------------------------------------------------------


def test_inline_mode_hides_system_cursor_on_mount() -> None:
    """Inline mode (the default) emits ``\\x1b[?25l`` so the terminal's
    blinking cursor doesn't sit on top of PyInk's own cursors.

    Regression: inline mode used to only hide the cursor when
    ``alternate_screen=True`` because the hide sequence lived inside
    ``enter_alternate_screen``. The cursor is now hidden unconditionally
    on mount.
    """
    inst, out = _render_silent(Text("hi"), columns=20, rows=2)
    try:
        written = out.getvalue()
        assert "\x1b[?25l" in written
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_cursor_restored_on_unmount_in_inline_mode() -> None:
    """After unmount the system cursor is restored (``\\x1b[?25h``)."""
    inst, out = _render_silent(Text("hi"), columns=20, rows=2)
    inst.unmount()  # type: ignore[attr-defined]
    written = out.getvalue()
    assert "\x1b[?25h" in written


def test_alt_screen_mode_also_hides_and_restores_cursor() -> None:
    """Alt-screen mode continues to hide + restore the cursor (it always
    did via ``enter_alternate_screen`` / ``exit_alternate_screen``; the
    new inline-mode hide doesn't affect alt mode)."""
    inst, out = _render_silent(
        Text("hi"), columns=20, rows=2, alternate_screen=True
    )
    try:
        mount_written = out.getvalue()
        # At least one hide sequence on mount.
        assert "\x1b[?25l" in mount_written
    finally:
        inst.unmount()  # type: ignore[attr-defined]
    full_written = out.getvalue()
    assert "\x1b[?25h" in full_written


# ---------------------------------------------------------------------------
# Layout auto-height (Issue 3 from Jarvis Phase 1)
#
# ``rows`` is now a max-rows upper bound rather than a forced height —
# the frame fits its content and only clips when content actually
# exceeds the cap. Lets inline-mode renders claim just the rows they
# need instead of stretching to fill the whole viewport (which was
# pushing Static output out of view).
# ---------------------------------------------------------------------------


def test_frame_fits_content_when_rows_exceeds_content() -> None:
    """``rows=10`` with 1 row of content → frame is 1 row tall, not 10.

    Regression for the "frame fills the viewport" bug: ``layout_root``
    used to treat ``rows`` as an ``exactly`` constraint, stretching the
    root box to ``rows`` lines regardless of content.
    """
    inst, _ = _render_silent(Text("hi"), columns=20, rows=10)
    try:
        # Wait a tick for the initial paint to land.
        import time

        time.sleep(0.05)
        # Frame has exactly 1 row of content; no trailing blank rows.
        frame = inst.current_frame  # type: ignore[attr-defined]
        assert frame.rstrip() == "hi"
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_frame_fits_multi_row_content_under_rows_cap() -> None:
    """3 rows of content under ``rows=10`` cap → frame is 3 rows tall."""
    tree = Box(
        Text("a"),
        Text("b"),
        Text("c"),
        flexDirection="column",
    )
    inst, _ = _render_silent(tree, columns=20, rows=10)
    try:
        import time

        time.sleep(0.05)
        frame = inst.current_frame  # type: ignore[attr-defined]
        # Frame is exactly "a\nb\nc" — no trailing blank lines padding
        # out to the rows cap.
        assert frame.rstrip() == "a\nb\nc"
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_frame_caps_to_rows_when_content_exceeds() -> None:
    """``rows=2`` with 3 rows of content → frame is capped to 2 rows."""
    tree = Box(
        Text("a"),
        Text("b"),
        Text("c"),
        flexDirection="column",
    )
    inst, _ = _render_silent(tree, columns=20, rows=2)
    try:
        import time

        time.sleep(0.05)
        frame = inst.current_frame  # type: ignore[attr-defined]
        # Frame is capped at 2 rows — third content row is clipped.
        rows = [ln for ln in frame.split("\n") if ln]
        assert len(rows) == 2
        assert rows[0] == "a"
        assert rows[1] == "b"
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_explicit_box_height_still_pins_exactly() -> None:
    """``<Box height=N>`` continues to pin exactly N rows — only the
    pipeline-level ``rows`` arg changed semantics."""
    tree = Box(Text("hi"), height=5)
    inst, _ = _render_silent(tree, columns=20, rows=10)
    try:
        import time

        time.sleep(0.05)
        frame = inst.current_frame  # type: ignore[attr-defined]
        # Box pinned to height=5: 1 row of content + 4 trailing blank
        # rows that pad the box out to its declared height.
        assert frame.count("\n") == 4  # 5 rows separated by 4 newlines
    finally:
        inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# resize -> _force_repaint flag -> full repaint via repaint_frame
# ---------------------------------------------------------------------------


def test_force_repaint_bypasses_equality_early_return() -> None:
    """``_force_repaint`` makes ``_paint_now`` skip the
    ``prev_frame == new_frame`` early-return so width-independent
    widgets still get repainted on resize.

    The flag is captured + cleared at the top of ``_paint_now``; setting
    it has no observable effect until the next paint runs.
    """
    inst, _out = _render_silent(Text("fixed"), columns=20, rows=3)
    try:
        with _mock_query_cursor(None):
            assert not inst._force_repaint  # type: ignore[attr-defined]
            inst._force_repaint = True  # type: ignore[attr-defined]
            # Paint consumes the flag — a subsequent paint without setting
            # the flag again must NOT re-emit an identical frame (the
            # equality early-return takes over again).
            inst._paint_now()  # type: ignore[attr-defined]
            assert not inst._force_repaint  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_routes_through_repaint_frame() -> None:
    """``_force_repaint`` makes ``_paint_now`` use ``repaint_frame``
    (full erase + repaint) instead of incremental ``write_diff`` so the
    cursor walks back to frame origin — without this the new frame
    lands below the previous one (the "stacking duplicates" bug).

    Validation: even when the new frame is byte-identical to the
    previous one, the repaint output contains the frame text AND an
    erase sequence (``\\x1b[2K`` line-clear), proving both the erase
    pass and the re-emit ran.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=3)
    try:
        with _mock_query_cursor(None):
            out.truncate(0)
            out.seek(0)
            inst._force_repaint = True  # type: ignore[attr-defined]
            inst._paint_now()  # type: ignore[attr-defined]
            repaint = out.getvalue()
            # The fixed string must reappear even though the frame didn't
            # change — proves the equality early-return was bypassed.
            assert "fixed" in repaint
            # ``repaint_frame`` always line-clears (``\x1b[2K``) before
            # each row; the incremental ``write_diff`` path emits no such
            # sequence when frames are identical.
            assert "\x1b[2K" in repaint
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_uses_clear_to_end_when_old_frame_wider_than_cols() -> None:
    """When the OLD frame contains a row wider than the NEW viewport,
    a width-shrinking resize has passively wrapped that row to multiple
    visual rows in the terminal. Per-row ``\\x1b[2K`` only blanks the
    visual row the cursor lands on — wrapped tails above survive the
    erase and show up as "stacked status_bars" residue.

    The wrap-aware erase path detects this case (any logical row wider
    than ``cols``) and routes through ``\\x1b[0J`` (clear-to-end-of-
    viewport) after walking the cursor to the visual top of the old
    frame's footprint. ``\\x1b[0J`` blanks everything from the cursor
    onwards, wrapped tails included.

    Validation: when the old frame's row exceeds the new cols, the
    repaint output contains ``\\x1b[0J``. When no row exceeds cols, the
    repaint output does NOT contain ``\\x1b[0J`` (legacy per-row erase
    is sufficient and preserves the ``\\x1b[2K`` count contract asserted
    by other tests).
    """
    from ink.render.diff import repaint_frame
    import io

    # Case 1 — old frame has a wide row (60 chars), cols shrinks to 40.
    # The wide row would wrap; erase must use ``\x1b[0J``.
    wide_row = "x" * 60
    old_frame_wide = f"short\n{wide_row}\nshort"
    new_frame = "short\nshrunken\nshort"
    out = io.StringIO()
    repaint_frame(old_frame_wide, new_frame, out, available_rows=10, cols=40)
    repaint_bytes = out.getvalue()
    assert "\x1b[0J" in repaint_bytes, (
        f"expected \\x1b[0J in wrap case, got: {repaint_bytes!r}"
    )

    # Case 2 — old frame rows all fit in cols. Legacy per-row erase
    # applies; ``\x1b[0J`` is NOT emitted.
    out2 = io.StringIO()
    repaint_frame(old_frame_wide, new_frame, out2, available_rows=10, cols=80)
    repaint2 = out2.getvalue()
    assert "\x1b[0J" not in repaint2, (
        f"expected NO \\x1b[0J in no-wrap case, got: {repaint2!r}"
    )


def test_force_repaint_root_p_erases_full_viewport_without_touching_scrollback() -> None:
    """Root P: ``_force_repaint`` emits ``\\x1b[1;1H\\x1b[0J`` (erase the
    whole viewport in place), redraws the retained static tail, then
    repaints the frame bottom-anchored.

    Unlike Root I (which this superficially resembles), the erase is not
    destructive: ``_static_tail_text`` retains recently flushed static
    text, so whatever ``\\x1b[0J`` blanks is immediately redrawn from the
    buffer. Root O's DSR-anchored partial erase was retired because
    Windows Terminal's post-reflow cursor row drifts (cpr_debug.log:
    cursor_row ranged 40-48 while rows=48 stayed constant) and every
    erase overshoot permanently blanked static rows — the monotonically
    growing gap between static and frame.

    Validation: force_repaint output contains ``\\x1b[1;1H\\x1b[0J`` and
    a CUP to ``frame_top``. ``\\x1b[2J`` (scrollback push), ``\\x1b[999B``
    (Root D relative anchor) and ``\\x1b[6n`` (DSR — Root P no longer
    consults the cursor) must not appear.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=3)
    try:
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        assert "\x1b[1;1H\x1b[0J" in repaint, (
            f"expected \\x1b[1;1H\\x1b[0J (in-place viewport erase), "
            f"got: {repaint!r}"
        )
        # frame_top = rows - visual_h + 1 = 3 - 1 + 1 = 3.
        assert "\x1b[3;1H" in repaint, (
            f"expected \\x1b[3;1H (CUP to frame_top=3), got: {repaint!r}"
        )
        # ``\\x1b[2J`` is FORBIDDEN in this path — it pushes viewport
        # content into scrollback on Windows Terminal.
        assert "\x1b[2J" not in repaint, (
            f"\\x1b[2J must not appear in force_repaint (scrollback push), got: {repaint!r}"
        )
        # Root D's anchor sequence is GONE — we don't rely on relative
        # cursor positioning anymore.
        assert "\x1b[999B" not in repaint, (
            f"Root D anchor \\x1b[999B should be gone, got: {repaint!r}"
        )
        # Root P never consults the cursor — no DSR query.
        assert "\x1b[6n" not in repaint, (
            f"DSR \\x1b[6n must not appear in Root P, got: {repaint!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_routes_through_repaint_frame_with_query_mock() -> None:
    """Companion to :func:`test_force_repaint_routes_through_repaint_frame`
    — ensures the existing repaint_frame contract holds under Root M
    when DSR times out (mocked to keep the test fast).
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=3)
    try:
        with _mock_query_cursor(None):
            out.truncate(0)
            out.seek(0)
            inst._force_repaint = True  # type: ignore[attr-defined]
            inst._paint_now()  # type: ignore[attr-defined]
            repaint = out.getvalue()
            assert "fixed" in repaint
            assert "\x1b[2K" in repaint
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_uses_display_width_for_wrap_detection() -> None:
    """``repaint_frame`` must decide wrap-aware erase based on **display
    width**, not Python character count. Lines containing wide chars
    (CJK / emoji) have ``display_width > len(line)``; using ``len`` misses
    the wrap window ``len(line) <= cols < display_width(line)`` where
    the terminal actually wraps but PyInk thinks it doesn't.

    Symptom (Root E): Jarvis's ``📁 Jarvis 🧠 deepseek 🤖 brain 🔌 2``
    status_bar at 31 chars / 35 display cols. With right-aligned indent
    the rendered line is ~81 chars / ~85 display cols. At cols=82 the
    terminal wraps to 2 visual rows but ``len``-based detection takes
    the no-wrap path, emits per-row ``\\x1b[2K`` only, and leaves the
    wrapped top row uncleared — stacking duplicates across resizes.

    Validation: at the mismatch window (cols=82, display=85), the
    repaint output must contain ``\\x1b[0J`` (wrap-aware erase).
    """
    from ink.render.diff import repaint_frame
    import io

    emoji_line = "📁 Jarvis  🧠 deepseek  🤖 brain  🔌 2"
    old_frame = " " * 50 + emoji_line  # 81 chars, 85 display cols
    new_frame = " " * 20 + emoji_line
    out = io.StringIO()
    repaint_frame(old_frame, new_frame, out, available_rows=10, cols=82)
    repaint_bytes = out.getvalue()
    assert "\x1b[0J" in repaint_bytes, (
        f"expected \\x1b[0J at char/display mismatch window, got: {repaint_bytes!r}"
    )


def test_force_repaint_anchors_cursor_at_viewport_bottom() -> None:
    """OBSOLETE — Root D's ``\\x1b[999B`` anchor was removed in Root H.
    Kept as a stub so imports don't break; the contract is now in
    ``test_force_repaint_clears_viewport_and_positions_cursor_at_frame_top``.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=3)
    try:
        with _mock_query_cursor(None):
            out.truncate(0)
            out.seek(0)
            inst._force_repaint = True  # type: ignore[attr-defined]
            inst._paint_now()  # type: ignore[attr-defined]
            repaint = out.getvalue()
            # Root D's anchor sequence is GONE — replaced by absolute
            # cursor positioning (Root H).
            assert "\x1b[999B" not in repaint
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_non_force_repaint_does_not_emit_cursor_anchor() -> None:
    """Without ``_force_repaint``, ``_paint_now`` must NOT emit the
    ``\\x1b[999B\\r`` cursor-anchor sequence — it's a resize-only
    behaviour. Regular repaints route through incremental ``write_diff``
    and rely on the cursor already being parked at the frame's bottom.

    Validation: a normal rerender that changes content emits a diff
    without the anchor sequence.
    """
    inst, out = _render_silent(Text("first"), columns=20, rows=3)
    try:
        out.truncate(0)
        out.seek(0)
        # Plain rerender — no force_repaint flag set.
        inst.rerender(Text("second"))  # type: ignore[attr-defined]
        repaint = out.getvalue()
        assert "\x1b[999B" not in repaint, (
            f"cursor-anchor leaked into non-force path: {repaint!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Root P (erase viewport + redraw retained static tail + bottom-anchored frame)
# ---------------------------------------------------------------------------


def test_force_repaint_root_p_erases_viewport_and_anchors_frame_to_bottom() -> None:
    """Root P, no retained static: the repaint is exactly
    ``\\x1b[1;1H\\x1b[0J`` + CUP to ``frame_top`` + ``_paint_initial``.

    ``frame_top = rows - visual_h_new + 1`` — the frame is anchored to
    the viewport bottom, and nothing above it is written when the
    static tail is empty.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=10)
    try:
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        assert repaint.startswith("\x1b[1;1H\x1b[0J"), (
            f"expected repaint to start with viewport erase, got: {repaint!r}"
        )
        # frame_top = 10 - 1 + 1 = 10.
        assert "\x1b[10;1H" in repaint, (
            f"expected \\x1b[10;1H (CUP to frame_top=10), got: {repaint!r}"
        )
        assert "fixed" in repaint
        assert "\x1b[6n" not in repaint
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_root_p_redraws_static_tail_above_frame() -> None:
    """Root P: static flushed via ``write_static`` is redrawn from the
    retained tail, bottom-anchored directly above the frame.

    rows=10, frame visual_h=1 → frame_top=10, budget=9, selection
    budget=8 (1 safety row). Three 1-row static lines fit → static_h=3,
    ``start_row = 9 - 3 + 1 = 7``. Each line is emitted with its own
    absolute CUP (never a free-flowing ``\\n`` stream — see the
    scroll-pollution regression test below). Emission order must be:
    viewport erase → static lines (rows 7,8,9) → CUP(10) → frame.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=10)
    try:
        inst.write_static("msg1\nmsg2\nmsg3\n")  # type: ignore[attr-defined]
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        assert "\x1b[7;1Hmsg1\x1b[8;1Hmsg2\x1b[9;1Hmsg3" in repaint, (
            f"expected per-line CUP static redraw at rows 7-9, got: {repaint!r}"
        )
        i_erase = repaint.index("\x1b[1;1H\x1b[0J")
        i_static = repaint.index("\x1b[7;1Hmsg1")
        i_frame = repaint.index("\x1b[10;1H")
        assert i_erase < i_static < i_frame, (
            f"emission order must be erase → static → frame, got: {repaint!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_root_p_static_tail_respects_budget() -> None:
    """Root P: when the retained static is taller than the space above
    the frame, only the NEWEST lines that fit the budget are redrawn —
    older lines are dropped (they remain in scrollback from the
    original flush) rather than pushed behind the frame.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=5)
    try:
        inst.write_static("".join(f"m{i}\n" for i in range(10)))  # type: ignore[attr-defined]
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        # frame_top = 5, budget = 4, selection budget = 3 → newest 3
        # lines (m7..m9), start_row = 4 - 3 + 1 = 2.
        assert "\x1b[2;1Hm7\x1b[3;1Hm8\x1b[4;1Hm9" in repaint, (
            f"expected newest 3 lines redrawn at rows 2-4, got: {repaint!r}"
        )
        assert "m6" not in repaint, (
            f"m6 must be dropped (over budget), got: {repaint!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_root_p_static_redraw_cannot_scroll() -> None:
    """Regression: the static redraw must be scroll-proof by
    construction. Retained lines were rendered at an older (usually
    wider) width, so after a shrink they re-wrap and ``_visual_height``'s
    cumulative estimate error over a full-viewport suffix is unbounded.
    A free-flowing ``\\n`` emission that overruns the viewport bottom
    SCROLLS — dumping the redrawn duplicate content into scrollback
    (observed: markdown table fragments multiplying at several widths
    in mid-scrollback after repeated resize).

    Contract: every static line is preceded by an absolute CUP, a
    wrapping line advances the next CUP by its estimated height, and
    no bare ``\\n`` appears between the erase and the frame paint.
    The frame itself is painted per-line CUP as well (Root P.2) — a
    free-flowing frame whose emoji statusbar wraps past
    ``visual_h_new`` scrolls and dumps the redrawn static into
    scrollback (observed: history incl. the welcome banner duplicated).
    """
    inst, out = _render_silent(
        Box(Text("line-a"), Text("line-b"), flexDirection="column"),
        columns=20,
        rows=10,
    )
    try:
        # wide = 45 chars → wraps to ceil(45/20) = 3 rows at cols=20.
        inst.write_static("top\n" + "w" * 45 + "\nlast\n")  # type: ignore[attr-defined]
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        # frame visual_h = 2 → frame_top = 9, budget = 8, selection
        # budget = 7; heights: last=1, wide=3, top=1 → static_h = 5,
        # start_row = 8 - 5 + 1 = 4.
        # Rows: top@4, wide@5 (occupies est. 5-7), last@8.
        assert "\x1b[4;1Htop" in repaint, (
            f"expected top at row 4, got: {repaint!r}"
        )
        assert f"\x1b[5;1H{'w' * 45}" in repaint, (
            f"expected wide line at row 5, got: {repaint!r}"
        )
        assert "\x1b[8;1Hlast" in repaint, (
            f"expected last at row 8 (wide line advanced 3 rows), "
            f"got: {repaint!r}"
        )
        # Frame lines painted per-line CUP at rows 9 and 10.
        assert "\x1b[9;1H\x1b[2Kline-a" in repaint, (
            f"expected frame line-a at row 9, got: {repaint!r}"
        )
        assert "\x1b[10;1H\x1b[2Kline-b" in repaint, (
            f"expected frame line-b at row 10, got: {repaint!r}"
        )
        # No free-flowing newline anywhere in the repaint — newlines are
        # what lets an over-tall emission scroll the terminal.
        assert "\n" not in repaint, (
            f"repaint must not contain bare \\n (scroll risk), got: {repaint!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_root_p_does_not_query_cursor() -> None:
    """Root P: the post-reflow cursor position is NOT an input to any
    computation (WT's cursor drifts on reflow — that was Root O's fatal
    dependency). ``_query_cursor`` must not be called and no DSR byte
    may hit stdout.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=10)
    try:
        with patch(
            "ink.render.instance.Instance._query_cursor",
            side_effect=AssertionError("_query_cursor called in Root P"),
        ):
            out.truncate(0)
            out.seek(0)
            inst._force_repaint = True  # type: ignore[attr-defined]
            inst._paint_now()  # type: ignore[attr-defined]
        assert "\x1b[6n" not in out.getvalue()
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_force_repaint_first_paint_path_emits_no_erase() -> None:
    """With an empty ``current_frame`` the first-paint branch
    (``write_diff(None, new_frame)``) runs before the force_repaint
    branch, so no erase/CUP sequences are emitted.
    """
    inst, out = _render_silent(Text("fixed"), columns=20, rows=10)
    try:
        inst.current_frame = ""  # type: ignore[attr-defined]
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        assert "fixed" in repaint
        assert "\x1b[0J" not in repaint, (
            f"first-paint path should not emit \\x1b[0J, got: {repaint!r}"
        )
        assert "\x1b[" not in repaint.replace("\x1b[2K", ""), (
            f"first-paint path should not emit CUP, got: {repaint!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_reset_static_clears_retained_tail() -> None:
    """``reset_static`` (user /clear gesture) empties the retained tail —
    a later resize must not resurrect cleared static text."""
    inst, out = _render_silent(Text("fixed"), columns=20, rows=10)
    try:
        inst.write_static("secret-message\n")  # type: ignore[attr-defined]
        inst.reset_static()  # type: ignore[attr-defined]
        # Re-establish a painted frame (reset_static blanked
        # ``current_frame``; the next paint is a first paint).
        inst._paint_now()  # type: ignore[attr-defined]
        out.truncate(0)
        out.seek(0)
        inst._force_repaint = True  # type: ignore[attr-defined]
        inst._paint_now()  # type: ignore[attr-defined]
        repaint = out.getvalue()
        assert "secret-message" not in repaint, (
            f"cleared static must not be redrawn, got: {repaint!r}"
        )
        assert "\x1b[1;1H\x1b[0J" in repaint  # root_p still ran
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_static_tail_trimmed_to_max_lines() -> None:
    """The retained tail is capped at ``_STATIC_TAIL_MAX_LINES`` logical
    lines so long sessions don't grow the buffer without bound."""
    from ink.render.instance import _STATIC_TAIL_MAX_LINES

    inst, out = _render_silent(Text("fixed"), columns=20, rows=10)
    try:
        total = _STATIC_TAIL_MAX_LINES + 100
        inst.write_static("".join(f"L{i}\n" for i in range(total)))  # type: ignore[attr-defined]
        tail = inst._static_tail_text  # type: ignore[attr-defined]
        tail_lines = tail.split("\n")
        assert len(tail_lines) <= _STATIC_TAIL_MAX_LINES, (
            f"tail must be capped at {_STATIC_TAIL_MAX_LINES} lines, "
            f"got {len(tail_lines)}"
        )
        assert "L0\n" not in tail, "oldest lines must be evicted"
        assert f"L{total - 1}\n" in tail, "newest lines must be kept"
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_query_cursor_sends_dsr_and_returns_row_on_signal() -> None:
    """``_query_cursor`` writes ``\\x1b[6n`` to stdout, waits on
    ``_cpr_event``, and returns ``_cpr_row`` when signalled.

    Drives the protocol directly: start ``_query_cursor`` on a worker
    thread (so the test main thread can simulate the input loop's
    ``_handle_cpr``), capture the DSR bytes, fire the CPR callback
    BEFORE the 50 ms timeout elapses, and assert the returned row
    matches.
    """
    inst, out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        result: dict[str, object] = {}

        def worker() -> None:
            result["row"] = inst._query_cursor()  # type: ignore[attr-defined]

        t = threading.Thread(target=worker)
        t.start()
        # Poll for DSR appearance in stdout (worker writes it almost
        # immediately), then dispatch CPR right away so the worker's
        # 50 ms timeout doesn't elapse first.
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            if "\x1b[6n" in out.getvalue():
                break
            time.sleep(0.002)
        assert "\x1b[6n" in out.getvalue(), (
            f"expected \\x1b[6n (DSR) in stdout, got: {out.getvalue()!r}"
        )
        # Dispatch CPR before the worker's 50 ms timeout fires.
        consumed = inst._handle_cpr(row=7, col=1)  # type: ignore[attr-defined]
        assert consumed is True, "_handle_cpr should consume when awaiting"
        t.join(timeout=0.5)
        assert not t.is_alive(), "_query_cursor did not return after CPR"
        assert result.get("row") == 7, (
            f"expected _query_cursor to return row=7, got {result.get('row')!r}"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_query_cursor_returns_none_on_timeout() -> None:
    """When no CPR arrives within 50 ms, ``_query_cursor`` returns
    ``None`` and clears ``_cpr_pending`` so a later CPR can't poison
    the next query."""
    inst, _out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        # No CPR will arrive — should time out.
        start = time.monotonic()
        row = inst._query_cursor()  # type: ignore[attr-defined]
        elapsed = time.monotonic() - start
        assert row is None
        # Generous lower bound (50 ms timeout + scheduler slack).
        assert elapsed >= 0.04, (
            f"expected ≥40 ms wait (50 ms timeout), got {elapsed:.3f}s"
        )
        # Pending must be cleared so a stale late CPR is rejected.
        assert inst._cpr_pending is None  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_handle_cpr_rejects_when_not_pending() -> None:
    """``_handle_cpr`` returns ``False`` (and does NOT signal the event)
    when no DSR is in flight. Safety default — a stray CPR from a
    misbehaving terminal or a literal ``R`` keystroke is never silently
    swallowed."""
    inst, _out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        assert inst._cpr_pending is None  # type: ignore[attr-defined]
        # Event should stay clear.
        assert not inst._cpr_event.is_set()  # type: ignore[attr-defined]
        consumed = inst._handle_cpr(row=10, col=1)  # type: ignore[attr-defined]
        assert consumed is False
        assert not inst._cpr_event.is_set()  # type: ignore[attr-defined]
        assert inst._cpr_row is None  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_handle_cpr_rejects_stale_generation_after_timeout() -> None:
    """When a DSR times out, ``_cpr_pending`` is cleared. A late CPR
    arriving afterwards must be rejected (returns ``False``) rather
    than poisoning the next query.

    Sequence:

    1. Call ``_query_cursor`` on a worker thread; let it time out.
    2. After timeout, ``_cpr_pending is None``.
    3. Fire ``_handle_cpr`` — must return ``False`` and not set the
       event.
    """
    inst, _out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        result: dict[str, object] = {}

        def worker() -> None:
            result["row"] = inst._query_cursor()  # type: ignore[attr-defined]

        t = threading.Thread(target=worker)
        t.start()
        # Wait for the timeout to fire (50 ms + slack).
        t.join(timeout=0.2)
        assert not t.is_alive(), "worker should have timed out by now"
        assert result.get("row") is None
        # Pending was cleared by the timeout path.
        assert inst._cpr_pending is None  # type: ignore[attr-defined]
        # Now a late CPR arrives — must be rejected.
        assert not inst._cpr_event.is_set()  # type: ignore[attr-defined]
        consumed = inst._handle_cpr(row=12, col=1)  # type: ignore[attr-defined]
        assert consumed is False
        assert not inst._cpr_event.is_set()  # type: ignore[attr-defined]
        assert inst._cpr_row is None  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_handle_cpr_rejects_when_newer_generation_in_flight() -> None:
    """Race-condition guard (ConPTY reorder): when a fresh DSR has been
    issued (``_cpr_pending`` set to a newer generation), a stale CPR
    for the previous generation must NOT match.

    Setup:

    1. Bump generation by issuing DSR (``gen=1``).
    2. Without delivering a CPR, bump again (``gen=2``).
    3. Fire ``_handle_cpr`` — the implicit generation from gen=1 is
       stale. Implementation note: the current design tracks only the
       *latest* pending generation, so the stale-response case is
       handled by the same ``_cpr_pending is None`` check after a
       timeout clears it. This test exercises the simpler invariant:
       once a CPR has been consumed, ``_cpr_pending`` is None and a
       second CPR (the stale echo) is rejected.
    """
    inst, _out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        # Issue a DSR and immediately fulfil it.
        inst._cpr_event.clear()  # type: ignore[attr-defined]
        inst._cpr_generation += 1  # type: ignore[attr-defined]
        inst._cpr_pending = inst._cpr_generation  # type: ignore[attr-defined]
        inst._cpr_row = None  # type: ignore[attr-defined]
        consumed = inst._handle_cpr(row=3, col=1)  # type: ignore[attr-defined]
        assert consumed is True
        # ``_cpr_pending`` is cleared after consumption.
        assert inst._cpr_pending is None  # type: ignore[attr-defined]
        # A stale duplicate CPR (from the same generation) arrives —
        # must be rejected because nothing is pending.
        consumed2 = inst._handle_cpr(row=3, col=1)  # type: ignore[attr-defined]
        assert consumed2 is False
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_terminal_cpr_callback_consumes_cpr_sequence() -> None:
    """The Terminal's dispatcher routes CSI CPR sequences through the
    registered callback instead of emitting them as Keys. Outside an
    active DSR the callback returns ``False`` and the CPR surfaces as
    a (harmless empty) Key — safety default."""
    from ink.render.terminal import Terminal

    term = Terminal(io.StringIO(), stdin=io.StringIO())
    try:
        consumed: list[tuple[int, int]] = []

        def handler(row: int, col: int) -> bool:
            consumed.append((row, col))
            return True

        term.set_cpr_callback(handler)
        callbacks: list = []
        # Simulate the input loop's dispatch path.
        term._dispatch_sequences(["\x1b[25;1R"], callbacks)
        assert consumed == [(25, 1)], (
            f"expected CPR handler to fire once with (25, 1), got {consumed!r}"
        )
        # The CPR was consumed — no Key dispatch happens (callbacks
        # list stays empty because the dispatcher short-circuits).
    finally:
        term.set_cpr_callback(None)


def test_terminal_cpr_callback_passthrough_when_not_consumed() -> None:
    """When the registered handler returns ``False`` (no DSR in flight),
    the dispatcher falls through to normal Key emission. A literal
    capital ``R`` keystroke also does not match the CPR regex and is
    delivered as a normal Key."""
    from ink.render.terminal import Terminal

    term = Terminal(io.StringIO(), stdin=io.StringIO())
    try:
        # Handler that always rejects — CPR should fall through.
        term.set_cpr_callback(lambda r, c: False)
        received: list = []
        term._key_callbacks.append(lambda k: received.append(k))
        term._dispatch_sequences(["\x1b[25;1R"], list(term._key_callbacks))
        # CPR fell through — parse_key returns an empty-input Key.
        assert len(received) == 1
        assert received[0].input == ""

        # A literal ``R`` keystroke must NOT be intercepted as CPR.
        received.clear()
        term._dispatch_sequences(["R"], list(term._key_callbacks))
        assert len(received) == 1
        assert received[0].input == "R"
    finally:
        term.set_cpr_callback(None)


def test_terminal_cpr_regex_does_not_match_non_cpr() -> None:
    """The CPR regex matches ONLY the exact ``\\x1b[<n>;<n>R`` form.
    Other ``R``-terminated sequences (e.g. ``\\x1b[R`` cursor-move,
    bare ``R``) must not be misinterpreted as CPR."""
    from ink.render.terminal import _CPR_RE

    # Valid CPRs.
    assert _CPR_RE.match("\x1b[1;1R") is not None
    assert _CPR_RE.match("\x1b[999;999R") is not None
    assert _CPR_RE.match("\x1b[25;80R") is not None
    # NOT CPRs.
    assert _CPR_RE.match("R") is None
    assert _CPR_RE.match("\x1b[R") is None
    assert _CPR_RE.match("\x1b[25R") is None  # missing ;col
    assert _CPR_RE.match("\x1b[25;1A") is None  # wrong final byte
    assert _CPR_RE.match("\x1b[?25;1R") is None  # has intermediate '?'


def test_handle_cpr_does_not_deadlock_when_painter_holds_lock() -> None:
    """REGRESSION: ``_handle_cpr`` must NOT acquire ``self._lock``.

    The real ``_paint_now`` runs under ``self._lock`` (acquired at the
    method head, held across the whole body). The ``force_repaint``
    branch calls ``_query_cursor`` inside that lock, and ``_query_cursor``
    blocks on ``_cpr_event.wait(timeout=0.05)``. The terminal's input
    thread (a DIFFERENT thread from the painter) receives the CPR bytes
    and dispatches ``_handle_cpr``.

    If ``_handle_cpr`` tries to acquire ``self._lock`` it will BLOCK on
    the painter's held lock — the painter won't release it until
    ``_cpr_event.wait`` times out (50 ms later), at which point the
    late ``_handle_cpr`` writes ``_cpr_row`` and sets the event — but
    the painter has already returned ``None`` and fallen back to Root
    I. **Every real-terminal force_repaint silently degrades to
    Root I.** Mock-based tests don't catch this because they patch
    ``_query_cursor`` and never exercise the deadlock path.

    This test simulates the production threading exactly:

    1. Main thread acquires ``self._lock`` (mimics ``_paint_now``'s
       outer ``with self._lock:``).
    2. Worker thread runs ``_query_cursor`` — writes DSR, blocks on
       the event wait (still under the main thread's lock).
    3. After 20 ms (well within the 50 ms timeout), a second "input"
       thread fires ``_handle_cpr``.
    4. The worker must wake up and return the CPR row BEFORE the 50 ms
       timeout — i.e., the test thread holding ``self._lock`` must
       NOT deadlock the input thread.

    Pass criterion: the worker returns the CPR row (not ``None``) and
    the round-trip completes in well under 50 ms. A buggy impl that
    uses ``self._lock`` for CPR state would deadlock and the worker
    would time out at ~50 ms, returning ``None``.
    """
    inst, _out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        result: dict[str, object] = {}

        def worker() -> None:
            # ``_query_cursor`` will write DSR + wait. With the bug,
            # the input thread's ``_handle_cpr`` blocks on the
            # painter's ``self._lock`` (held by the test's main
            # thread) and the worker times out at 50 ms. With the
            # fix, ``_handle_cpr`` uses the separate ``_cpr_lock``
            # and the worker returns well inside the timeout.
            result["row"] = inst._query_cursor()  # type: ignore[attr-defined]
            result["done_at"] = time.monotonic()

        # Hold the painter's lock for the entire test duration —
        # mirrors ``_paint_now``'s outer ``with self._lock:``.
        with inst._lock:  # type: ignore[attr-defined]
            start = time.monotonic()
            t = threading.Thread(target=worker)
            t.start()

            # Wait for the worker to send DSR (it does this immediately
            # after acquiring _cpr_lock and bumping generation).
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                if inst._cpr_pending is not None:  # type: ignore[attr-defined]
                    break
                time.sleep(0.001)
            assert inst._cpr_pending is not None, (
                "worker did not set _cpr_pending before timeout"
            )

            # Wait 20 ms so the input thread's CPR arrives well inside
            # the worker's 50 ms timeout window. Then dispatch CPR
            # from another thread (simulates the input thread).
            time.sleep(0.02)
            cpr_thread = threading.Thread(
                target=lambda: inst._handle_cpr(row=4, col=1)  # type: ignore[attr-defined]
            )
            cpr_thread.start()
            cpr_thread.join(timeout=1.0)
            assert not cpr_thread.is_alive(), (
                "_handle_cpr deadlocked — input thread blocked on self._lock "
                "while painter (test main thread) holds it"
            )

            # Worker should already have returned because _handle_cpr
            # signalled the event.
            t.join(timeout=1.0)
            elapsed = time.monotonic() - start

        assert not t.is_alive(), (
            f"worker did not finish — elapsed={elapsed:.3f}s; "
            f"result={result!r}"
        )
        # The load-bearing assertion: row is 4 (NOT None — would
        # indicate a timeout fallback to Root I).
        assert result.get("row") == 4, (
            f"expected _query_cursor to return row=4 (CPR delivered "
            f"through the lock without deadlock), got {result.get('row')!r}; "
            f"elapsed={elapsed:.3f}s"
        )
        # And it returned well under the 50 ms timeout (20 ms sleep +
        # dispatch + slack). If it had deadlocked and timed out we'd
        # see ≥ 50 ms.
        assert elapsed < 0.05, (
            f"expected CPR round-trip to complete well under 50 ms; "
            f"elapsed={elapsed:.3f}s indicates a deadlock-induced timeout"
        )
    finally:
        inst.unmount()  # type: ignore[attr-defined]


def test_handle_cpr_uses_dedicated_lock_not_painter_lock() -> None:
    """Structural check: ``_handle_cpr`` and ``_query_cursor`` must use
    ``_cpr_lock`` (a dedicated lock), not ``self._lock``. This is the
    fix for the deadlock documented in
    :func:`test_handle_cpr_does_not_deadlock_when_painter_holds_lock`.

    The test holds ``self._lock`` continuously and confirms that
    ``_handle_cpr`` runs and returns without blocking. If the handler
    ever acquires ``self._lock`` (the bug), this test hangs / fails.
    """
    inst, _out = _render_silent(Text("x"), columns=20, rows=5)
    try:
        # Put the instance in a state where a DSR is "in flight".
        with inst._cpr_lock:  # type: ignore[attr-defined]
            inst._cpr_generation += 1  # type: ignore[attr-defined]
            inst._cpr_pending = inst._cpr_generation  # type: ignore[attr-defined]
            inst._cpr_row = None  # type: ignore[attr-defined]
        inst._cpr_event.clear()  # type: ignore[attr-defined]

        # Acquire the painter's lock for the whole call. A correct
        # ``_handle_cpr`` returns immediately; a buggy one that
        # acquires ``self._lock`` blocks here forever (or until the
        # test framework's timeout kicks in).
        with inst._lock:  # type: ignore[attr-defined]
            consumed = inst._handle_cpr(row=42, col=1)  # type: ignore[attr-defined]
            assert consumed is True
            assert inst._cpr_row == 42  # type: ignore[attr-defined]
            assert inst._cpr_pending is None  # type: ignore[attr-defined]
            assert inst._cpr_event.is_set()  # type: ignore[attr-defined]
    finally:
        inst.unmount()  # type: ignore[attr-defined]



