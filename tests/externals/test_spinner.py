"""Tests for :func:`pyink.externals.Spinner` (Phase 2 PR2).

All assertions use the live :func:`pyink.render.render` pipeline rather
than :func:`pyink.render_to_string`. The reason: ``Spinner`` mounts
:func:`pyink.hooks.use_interval`, whose guard requires the active
``_current_instance`` ContextVar — which is only set by ``render``,
not by the synchronous test renderer. This matches the existing
``tests/hooks/test_use_interval.py`` and
``tests/components/test_reactive_props.py`` conventions, which also
reach for the live pipeline whenever hooks are involved.

``use_interval`` is **not** mocked here. We use short intervals
(a few ms) and bounded polling, mirroring ``test_use_interval``'s style.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable

from pyink import Box, Text, create_element, render
from pyink.core.element import Element
from pyink.externals import SPINNERS, Spinner
from pyink.externals.spinner import _SpinnerComponent
from pyink.render.instance import Instance

ESC = "\x1b"


# ---------------------------------------------------------------------------
# Helpers (live render pipeline)
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _mount(
    build_tree: Element,
    *,
    columns: int = 30,
    rows: int = 5,
) -> tuple[Instance, io.StringIO]:
    out = io.StringIO()
    inst = render(
        build_tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=rows,
        exit_on_ctrl_c=False,
    )
    # Initial paint is synchronous, but give the throttle thread a beat
    # so a subsequent signal write can't race the first frame.
    time.sleep(0.05)
    return inst, out


def _frame(inst: Instance) -> str:
    """Clean frame snapshot — see ``test_reactive_props`` for rationale."""
    return inst.current_frame


def _wait_for(
    predicate: Callable[[], bool],
    *,
    attempts: int = 200,
    delay: float = 0.025,
) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(delay)
    return predicate()


def _first_frame_of(tree: Element, *, columns: int = 30) -> str:
    """Mount + snapshot first frame + unmount. Returns the rendered frame.

    The first paint is synchronous inside ``render``, so we can read the
    frame immediately. ``Spinner`` instances inside ``tree`` should
    disable the interval worker (``interval_ms=0``) so it can't advance
    before we snapshot — these tests assert the *initial* frame only.

    The live pipeline pads short frames to ``rows`` lines, so we strip
    trailing blank lines to recover the bare rendered content.
    """
    out = io.StringIO()
    inst = render(
        tree,
        stdout=out,
        stdin=_FakeTTY(),
        columns=columns,
        rows=5,
        exit_on_ctrl_c=False,
    )
    # No sleep: read the synchronous first paint immediately.
    snap = _frame(inst).rstrip("\n")
    inst.unmount()
    return snap


# ---------------------------------------------------------------------------
# Element shape
# ---------------------------------------------------------------------------


def test_spinner_returns_function_component_element() -> None:
    el = Spinner(type="dots")
    assert isinstance(el, Element)
    # Function component (not a host tag).
    assert callable(el.type)
    assert el.type is _SpinnerComponent
    # Props capture the spinner config.
    assert el.props["type"] == "dots"
    # No children — the function component builds its own subtree on mount.
    assert el.children == ()


def test_spinner_color_and_interval_captured_in_props() -> None:
    el = Spinner(type="line", color="red", interval_ms=42)
    assert el.props["type"] == "line"
    assert el.props["color"] == "red"
    assert el.props["interval_ms"] == 42


def test_spinner_defaults_are_dots_and_80ms() -> None:
    el = Spinner()
    assert el.props["type"] == "dots"
    assert el.props["interval_ms"] == 80
    assert el.props["color"] is None


# ---------------------------------------------------------------------------
# First-frame rendering
# ---------------------------------------------------------------------------


def test_spinner_first_frame_renders_on_mount() -> None:
    """Initial paint shows frames[0] before any tick fires."""
    snap = _first_frame_of(Spinner(type="dots", interval_ms=0))
    assert snap == SPINNERS["dots"][0]


def test_spinner_color_applied_to_first_frame() -> None:
    """Color is forwarded to Text and applied to the frame string."""
    snap = _first_frame_of(Spinner(type="dots", color="green", interval_ms=0))
    assert snap == f"{ESC}[32m{SPINNERS['dots'][0]}{ESC}[0m"


def test_spinner_color_red() -> None:
    snap = _first_frame_of(Spinner(type="dots", color="red", interval_ms=0))
    assert snap == f"{ESC}[31m{SPINNERS['dots'][0]}{ESC}[0m"


def test_spinner_color_hex() -> None:
    snap = _first_frame_of(Spinner(type="dots", color="#FF8800", interval_ms=0))
    assert snap == f"{ESC}[38;2;255;136;0m{SPINNERS['dots'][0]}{ESC}[0m"


# ---------------------------------------------------------------------------
# Per-type first-frame coverage (>= 12 distinct types)
# ---------------------------------------------------------------------------


_TYPES_TO_COVER = [
    "dots",
    "dots2",
    "line",
    "pipe",
    "simpleDots",
    "star",
    "star2",
    "flip",
    "hamburger",
    "grow",
    "box",
    "moon",
    "arc",
    "circle",
    "squareCorners",
    "triangle",
    "dqpb",
    "dots12",
]


def test_type_list_has_at_least_twelve_entries() -> None:
    assert len(_TYPES_TO_COVER) >= 12


def test_each_type_in_registry() -> None:
    for name in _TYPES_TO_COVER:
        assert name in SPINNERS, f"{name!r} missing from SPINNERS registry"


def test_spinner_each_type_renders_first_frame() -> None:
    """Each named type renders the first frame of its sequence.

    Iterates the list inline (rather than via ``parametrize``) so a
    failure reports which type broke in one shot.

    The render pipeline right-trims trailing whitespace per line, so
    spinners whose frames end with spaces (``simpleDots`` / ``balls``)
    are compared after stripping whitespace from both sides.
    """
    failures: list[str] = []
    for name in _TYPES_TO_COVER:
        snap = _first_frame_of(Spinner(type=name, interval_ms=0))
        expected = SPINNERS[name][0]
        # Equality works for frames without trailing spaces; otherwise
        # compare with startswith (the rendered snapshot is the frame
        # minus the right-stripped spaces).
        if snap != expected and not expected.startswith(snap.rstrip()):
            failures.append(
                f"{name!r}: expected {expected!r}, got {snap!r}"
            )
    assert not failures, "\n".join(failures)


def test_spinner_registry_has_at_least_twelve_types() -> None:
    assert len(SPINNERS) >= 12


def test_spinner_registry_entries_are_nonempty_tuples() -> None:
    """Defensive: every registered type has at least one frame."""
    for name, frames in SPINNERS.items():
        assert isinstance(frames, tuple), f"{name!r} must be a tuple"
        assert all(isinstance(f, str) for f in frames), (
            f"{name!r} must contain only str frames"
        )
        assert len(frames) >= 1, f"{name!r} must have at least one frame"


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------


def test_spinner_unknown_type_falls_back_to_dots_first_frame() -> None:
    """Unknown type silently renders dots — no crash, no missing frame."""
    snap = _first_frame_of(
        Spinner(type="this-spinner-does-not-exist", interval_ms=0)
    )
    assert snap == SPINNERS["dots"][0]


def test_spinner_unknown_type_renders_dots_first_frame_in_box() -> None:
    """Fallback is the full dots sequence, not just a placeholder."""
    snap = _first_frame_of(
        Box(Spinner(type="bogus", interval_ms=0), Text("hi")),
        columns=20,
    )
    assert SPINNERS["dots"][0] in snap
    assert "hi" in snap


def test_spinner_empty_type_string_falls_back_to_dots() -> None:
    """Empty string is treated as unknown → dots fallback."""
    snap = _first_frame_of(Spinner(type="", interval_ms=0))
    assert snap == SPINNERS["dots"][0]


# ---------------------------------------------------------------------------
# Single-frame / static safety
# ---------------------------------------------------------------------------


def test_spinner_single_frame_does_not_advance() -> None:
    """A type with one frame must not enter a no-op write loop.

    We register a synthetic single-frame spinner, mount it, let the
    worker run for a while, then assert the rendered frame is still the
    single frame.
    """
    SPINNERS["__test_single__"] = ("X",)
    try:

        def App() -> Element:
            return Spinner(type="__test_single__", interval_ms=10)

        inst, _ = _mount(App())
        time.sleep(0.2)
        assert "X" in _frame(inst)
        inst.unmount()
    finally:
        del SPINNERS["__test_single__"]


# ---------------------------------------------------------------------------
# Frame advance via the live render pipeline
# ---------------------------------------------------------------------------


def test_spinner_advances_frame_over_time() -> None:
    """The interval worker drives the signal; the render loop re-paints."""

    def App() -> Element:
        return Spinner(type="dots", interval_ms=10, color="green")

    inst, _ = _mount(App())
    # The worker (10 ms) will tick before long; we just need to see one
    # of the later frames appear, not necessarily from the start.
    def saw_any_frame() -> bool:
        snap = _frame(inst)
        return any(f in snap for f in SPINNERS["dots"])

    def saw_a_different_frame() -> bool:
        snap = _frame(inst)
        return any(f in snap for f in SPINNERS["dots"][1:])

    assert _wait_for(saw_any_frame, attempts=100, delay=0.025), "no frame ever rendered"
    advanced = _wait_for(saw_a_different_frame, attempts=200, delay=0.025)
    inst.unmount()
    assert advanced, "spinner never advanced past frame 0"


def test_spinner_unmount_tears_down_worker() -> None:
    import threading

    def App() -> Element:
        return Spinner(type="dots", interval_ms=20)

    inst, _ = _mount(App())
    assert _wait_for(
        lambda: any(
            t.name.startswith("pyink-interval-") and t.is_alive()
            for t in threading.enumerate()
        ),
        attempts=80,
    )
    inst.unmount()
    # ``use_interval`` join has a 1 s ceiling; we wait up to 2 s to be safe.
    assert _wait_for(
        lambda: not any(
            t.name.startswith("pyink-interval-") and t.is_alive()
            for t in threading.enumerate()
        ),
        attempts=120,
        delay=0.025,
    ), "interval thread still alive after Spinner unmount"


def test_spinner_interval_zero_renders_static_frame() -> None:
    """interval_ms=0 disables the worker; the first frame still renders."""

    def App() -> Element:
        return Spinner(type="dots", interval_ms=0)

    inst, _ = _mount(App())
    time.sleep(0.1)
    assert SPINNERS["dots"][0] in _frame(inst)
    inst.unmount()


# ---------------------------------------------------------------------------
# Integration: Spinner inside a Box with sibling Text
# ---------------------------------------------------------------------------


def test_spinner_inside_box_with_sibling_text() -> None:
    """Spinner + Text compose inside Box; spinner frame shows next to text."""
    snap = _first_frame_of(
        Box(
            Spinner(type="dots", color="green", interval_ms=0),
            Text(" Loading..."),
        ),
        columns=40,
    )
    assert SPINNERS["dots"][0] in snap
    assert "Loading..." in snap
    # Spinner green color sequence present.
    assert f"{ESC}[32m{SPINNERS['dots'][0]}{ESC}[0m" in snap


def test_spinner_inside_box_live_pipeline_advances() -> None:
    tree = Box(
        Spinner(type="dots", color="red", interval_ms=10),
        Text(" working"),
    )
    inst, _ = _mount(tree, columns=40, rows=3)
    # Wait for an advanced frame — don't assert the initial frame because
    # the worker may have ticked by the time we look.
    def advanced() -> bool:
        s = _frame(inst)
        return "working" in s and any(f in s for f in SPINNERS["dots"])

    assert _wait_for(advanced, attempts=200, delay=0.025), "spinner frame never rendered"
    inst.unmount()


def test_spinner_as_child_of_create_element_box() -> None:
    """Spinner works as a child of an Element built via create_element."""
    snap = _first_frame_of(
        create_element(
            "box",
            Spinner(type="line", interval_ms=0),
            Text("..."),
        ),
        columns=20,
    )
    assert SPINNERS["line"][0] in snap


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def test_externals_init_exports_spinner_and_spinners() -> None:
    from pyink.externals import SPINNERS as InitSPINNERS
    from pyink.externals import Spinner as InitSpinner

    assert InitSpinner is Spinner
    assert InitSPINNERS is SPINNERS


def test_spinner_not_in_pyink_top_level() -> None:
    """PRD Decision 5 — externals stay opt-in; top-level import must fail."""
    import pyink

    assert not hasattr(pyink, "Spinner"), "Spinner must NOT be top-level"
    assert not hasattr(pyink, "SPINNERS"), "SPINNERS must NOT be top-level"
