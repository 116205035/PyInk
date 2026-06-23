"""``ProgressBar`` — character-cell progress bar (Phase 6 PR2).

Mirrors :mod:`ink-progress-bar`: a horizontal bar whose filled portion
grows proportionally with ``value`` and (optionally) a trailing
percentage readout. PyInk's flavour collapses ink-progress-bar's
``percent`` (0-1) / ``columns`` / ``left`` / ``right`` / ``rightPad``
knobs into a simpler ``value`` + ``width`` + ``show_percentage`` API,
matching how the component is actually used in CLI UIs.

Design (per PRD PR2 scope):

* ``ProgressBar`` is a function component. On mount it resolves the
  ``value`` source (``float`` / ``Signal[float]`` /
  ``Callable[[], float]``), clamps to ``[0.0, 1.0]``, computes the
  filled / remaining character counts, and emits a single ``Text``
  leaf carrying the composed string and the caller's ``color``.
* The string is built inside a layout-time callable child so a
  reactive ``Signal`` / ``Callable`` source re-paints on every write
  — the same pattern :func:`pyink.externals.Spinner` uses for its
  frame index.
* ``width`` is the bar width in cells (excluding the optional
  percentage suffix). ``character`` fills the completed portion and
  ``remaining_character`` fills the rest so the bar always occupies
  exactly ``width`` cells, regardless of ``value``.
* ``show_percentage=True`` (default) appends ``" NN%"`` (zero-padded
  to 3 digits so the suffix width is stable across renders — a
  flapping width causes the surrounding layout to jitter). ``False``
  suppresses the suffix entirely.
* ``character`` / ``remaining_character`` default to the Unicode
  block elements ``█`` (U+2588) and ``░`` (U+2591), matching the
  upstream defaults. Callers may pass any single-character string
  (e.g. ``"="`` / ``"-"`` for ASCII-only terminals).
* ``value`` outside ``[0.0, 1.0]`` is clamped rather than rejected —
  the typical caller is computing a ratio and a transient overshoot
  (e.g. ``1.0000001`` from float arithmetic) shouldn't crash.

Cross-layer note: the bar is emitted as a single ``text`` leaf rather
than a ``box`` row because the layout engine's flex model isn't
needed — ``width`` pins the cell count and the percentage suffix
sits inline at the end. ``Box(ProgressBar(...), Text("..."))``
composes naturally because the ``Text`` leaf is just another inline
sibling.

PR2 scope: ships ``ProgressBar`` only.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal

__all__ = ["ProgressBar"]

#: Default bar width in cells. Mirrors the typical ink-progress-bar
#: example output (``█░`` bars hover around 20-40 cells; 30 lands in
#: the middle and is wide enough for the percentage suffix to read
#: cleanly even at small values).
_DEFAULT_WIDTH: int = 30

#: Default filled character. U+2588 FULL BLOCK paints a solid cell.
_DEFAULT_CHARACTER: str = "█"

#: Default remaining character. U+2591 LIGHT SHADE paints a faintly
#: textured cell so the unfilled portion is visible but de-emphasised.
_DEFAULT_REMAINING_CHARACTER: str = "░"


def _resolve_value(
    source: float | Signal[float] | Callable[[], float],
) -> float:
    """Return the current numeric value carried by ``source``.

    Centralises the three-shape dispatch (``float`` /
    :class:`Signal` ``[float]`` / ``Callable[[], float]``) so both
    the eager mount-time build and the layout-time callable share the
    resolution logic. Mirrors the helpers in
    :mod:`pyink.externals.streaming_text` / :mod:`pyink.externals.task_list`.
    """
    if isinstance(source, Signal):
        return source.value
    if callable(source):
        return source()
    return source


def _format_percentage(value: float) -> str:
    """Format ``value`` (0.0-1.0) as a fixed-width ``" NN%"`` suffix.

    The value is clamped to ``[0, 100]`` percent and zero-padded to
    three digits so the suffix width is stable across renders — a
    flapping width (``"5%"`` vs ``"50%"`` vs ``"100%"``) would cause
    the surrounding layout to jitter, which is visually worse than a
    few leading spaces.
    """
    pct = max(0, min(100, round(value * 100)))
    return f" {pct:3d}%"


def _build_bar(
    value: float,
    *,
    width: int,
    character: str,
    remaining_character: str,
    show_percentage: bool,
) -> str:
    """Compose the visible bar string for ``value``.

    The completed-cell count is ``round(value * width)`` rather than
    ``int(value * width)`` — rounding matches the typical user
    expectation that ``value=0.5`` on a 10-cell bar shows 5 filled
    cells, not 5 (which ``int`` happens to give here too, but for
    ``value=0.55`` on a 20-cell bar ``int`` gives 11 while ``round``
    gives 11 as well; the difference matters on widths like 3 where
    ``0.5 * 3 = 1.5`` rounds to 2 but truncates to 1). The result is
    clamped to ``[0, width]`` so a clamped ``value`` of 1.0 fills
    exactly ``width`` cells and 0.0 leaves the bar empty.
    """
    if width <= 0:
        # Degenerate: no bar cells, just the percentage if requested.
        return _format_percentage(value) if show_percentage else ""
    clamped = max(0.0, min(1.0, value))
    filled = round(clamped * width)
    if filled > width:
        filled = width
    if filled < 0:
        filled = 0
    remaining = width - filled
    bar = character * filled + remaining_character * remaining
    if show_percentage:
        return bar + _format_percentage(clamped)
    return bar


def _ProgressBarImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context."""
    source = props["__progress_value__"]
    width: int = props["__progress_width__"]
    character: str = props["__progress_character__"]
    remaining_character: str = props["__progress_remaining_character__"]
    show_percentage: bool = props["__progress_show_percentage__"]
    # All non-internal props are forwarded to the emitted Text leaf so
    # callers can pass ``color`` / ``bold`` / ``underline`` / etc. and
    # have them applied to the whole bar (filled + remaining + suffix).
    text_props: dict[str, Any] = {
        k: v for k, v in props.items() if not k.startswith("__progress_")
    }

    # Layout-time callable: re-resolves ``source`` on every paint so a
    # Signal write triggers re-render via the render-loop subscription
    # established when the callable is evaluated inside the layout pass.
    def _text() -> str:
        value = _resolve_value(source)
        return _build_bar(
            value,
            width=width,
            character=character,
            remaining_character=remaining_character,
            show_percentage=show_percentage,
        )

    return Text(_text, **text_props)


def ProgressBar(
    *,
    value: float | Signal[float] | Callable[[], float],
    width: int = _DEFAULT_WIDTH,
    character: str = _DEFAULT_CHARACTER,
    remaining_character: str = _DEFAULT_REMAINING_CHARACTER,
    color: str | None = None,
    show_percentage: bool = True,
    **props: Any,
) -> Element:
    """Render a horizontal progress bar.

    Parameters
    ----------
    value:
        Completion ratio in the range ``[0.0, 1.0]``. Values outside
        the range are clamped (a transient overshoot from float
        arithmetic won't crash). Accepts a plain ``float``, a
        :class:`Signal` ``[float]``, or a 0-arg ``Callable`` returning
        ``float``; the latter two re-paint on every write via a
        layout-time subscription.
    width:
        Bar width in cells (excluding the optional percentage
        suffix). Defaults to 30. ``<= 0`` suppresses the bar portion
        entirely, leaving only the suffix (useful when the caller
        wants a bare percentage readout).
    character:
        Cell character for the completed portion. Defaults to
        ``█`` (U+2588 FULL BLOCK).
    remaining_character:
        Cell character for the unfilled portion. Defaults to ``░``
        (U+2591 LIGHT SHADE).
    color:
        Optional colour spec forwarded to the emitted :func:`Text`
        leaf (``"red"``, ``"#ff0000"``, ``"rgb(255,0,0)"``,
        ``"ansi256(9)"``). Applied uniformly to the whole bar
        (filled + remaining + suffix); pass ``None`` to inherit the
        terminal default.
    show_percentage:
        When ``True`` (default), appends ``" NN%"`` (zero-padded to
        three digits) so the suffix width is stable across renders.
        ``False`` suppresses the suffix entirely.
    **props:
        Forwarded to the emitted :func:`Text` leaf. Supports every
        style prop :func:`Text` accepts (``bold`` / ``underline`` /
        etc.).

    Returns
    -------
    Element
        An element whose ``type`` is a function component
        (:func:`_ProgressBarImpl`). The factory itself never reads
        ``value`` — the wrapped function does, when the reconciler
        mounts it.

    Raises
    ------
    TypeError
        If ``value`` is not a ``float`` / ``Signal`` / callable, or
        if ``width`` is not an ``int``.

    Usage
    -----
    ::

        ProgressBar(value=0.5)
        ProgressBar(value=0.75, width=20, color="green")
        ProgressBar(value=signal_value, character="=", remaining_character=" ")
        ProgressBar(value=0.0, show_percentage=False)
    """
    if not isinstance(value, (int, float, Signal)) and not callable(value):
        raise TypeError(
            f"ProgressBar 'value' must be float / Signal[float] / callable, "
            f"got {type(value).__name__!r}"
        )
    if isinstance(value, bool):  # ``bool`` is a subclass of ``int``
        raise TypeError(
            "ProgressBar 'value' must be a float, got bool"
        )
    if not isinstance(width, int) or isinstance(width, bool):
        raise TypeError(
            f"ProgressBar 'width' must be an int, got {type(width).__name__!r}"
        )
    if not isinstance(character, str) or len(character) == 0:
        raise TypeError(
            "ProgressBar 'character' must be a non-empty string"
        )
    if not isinstance(remaining_character, str) or len(remaining_character) == 0:
        raise TypeError(
            "ProgressBar 'remaining_character' must be a non-empty string"
        )

    progress_props: dict[str, Any] = {
        k: v for k, v in props.items() if not k.startswith("__progress_")
    }
    progress_props["color"] = color
    progress_props["__progress_value__"] = value
    progress_props["__progress_width__"] = width
    progress_props["__progress_character__"] = character
    progress_props["__progress_remaining_character__"] = remaining_character
    progress_props["__progress_show_percentage__"] = show_percentage
    return create_element(_ProgressBarImpl, **progress_props)
