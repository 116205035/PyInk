"""``StreamingText`` — stream-in text display (Phase 3 PR1).

Mirrors the Claude Code ``<StreamingText>`` pattern: a piece of text that
grows over time, optionally trailed by a cursor glyph and optionally
"smoothed" into a per-character reveal so the user sees a typing
animation rather than whole tokens popping in at once.

Three ``buffer`` shapes are supported (PRD Decision 5):

* :class:`pyink.core.signal.Signal` ``[str]`` — reactive. Each ``.value``
  write re-renders the surrounding component because the layout-time
  callable child subscribes to the signal. This is the canonical shape
  for an AI stream: the network thread appends characters to the buffer
  signal, the render loop re-paints on every write.
* ``Callable[[], str]`` — evaluated lazily during layout, like any other
  callable ``Text`` child. Useful when the caller wants to compose
  multiple signals / refs into a single string without materialising an
  intermediate ``Computed``.
* ``str`` — static. Renders exactly the same way as a plain ``Text``,
  but still benefits from the optional ``cursor`` and ``reveal_speed``
  affordances (typing effect over a known string).

Design:

* ``reveal_speed == 0`` (default) is the **fast path**. We never touch
  :func:`pyink.hooks.use_interval`; the buffer is concatenated with the
  cursor (if any) inside a callable child of a single ``Text``. This is
  the cheapest code path and the one most AI streaming UIs actually want
  — the network is already the bottleneck, no need to throttle on the
  render side.
* ``reveal_speed > 0`` flips to a function-component implementation that
  owns a private ``revealed`` count signal. A
  :func:`pyink.hooks.use_interval` worker ticks the count up by one
  character at ``1000 / reveal_speed`` ms intervals, clamped to the
  current buffer length. The displayed text is ``buffer[:revealed]``,
  recomputed on every layout. The cursor sits after the last revealed
  character so it visually leads the typing.
* ``cursor`` defaults to ``None`` (no glyph). When set, it is appended
  verbatim to the displayed text. ``cursor_color`` wraps the glyph in an
  SGR sequence via :func:`pyink.render.ansi.apply_style`; the layout
  measure pass already strips ANSI (CSI) sequences so the extra escape
  bytes don't inflate the column budget.

Cursor colour note: we deliberately emit the cursor *after* the leading
``reset`` that wraps the main text (when ``color`` is set) so the
cursor keeps its own colour independent of the text colour. A single
trailing ``reset`` is appended after the cursor so the colour does not
leak into subsequent output.

PR1 scope: ships ``StreamingText`` only. Markdown / HighlightedCode /
StructuredDiff land in later PRs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Signal, signal
from pyink.hooks.interval import use_interval
from pyink.render.ansi import apply_style

__all__ = ["StreamingText"]


def _resolve_buffer(buffer: Signal[str] | Callable[[], str] | str) -> str:
    """Return the current string carried by ``buffer``.

    Centralises the three-shape dispatch so both the fast path and the
    reveal-speed component can share the resolution logic.
    """
    if isinstance(buffer, Signal):
        return buffer.value
    if callable(buffer):
        return buffer()
    return buffer


def _render_cursor(cursor: str | None, cursor_color: str | None) -> str:
    """Build the cursor suffix (possibly empty).

    ``cursor`` is emitted verbatim so callers can pass any glyph
    (``"▋"`` / ``"|"`` / ``"_"``). When ``cursor_color`` is set, the
    glyph is wrapped in SGR sequences via :func:`apply_style`. The
    layout measure pass strips ANSI, so the wrapped form has the same
    column width as the raw glyph.
    """
    if not cursor:
        return ""
    if cursor_color:
        return apply_style(cursor, color=cursor_color)
    return cursor


def _StreamingTextImpl(**props: Any) -> Element:
    """Function component body for the ``reveal_speed > 0`` branch.

    Runs inside the reconciler render context, so hooks (``signal`` /
    :func:`pyink.hooks.use_interval`) are valid here. The fast path
    (``reveal_speed == 0``) never enters this function — it returns a
    plain ``Text`` directly from :func:`StreamingText`.
    """
    buffer: Signal[str] | Callable[[], str] | str = props["buffer"]
    cursor: str | None = props["cursor"]
    cursor_color: str | None = props["cursor_color"]
    reveal_speed: int = props["reveal_speed"]
    color: str | None = props["color"]
    text_props: dict[str, Any] = props["text_props"]

    # How many characters of ``buffer`` are currently on screen. Starts
    # at zero so a non-empty buffer animates from scratch on mount; for
    # a static ``str`` buffer this gives the classic typing effect.
    revealed: Signal[int] = signal(0)

    # Per-tick increment: advance the reveal counter by one character,
    # clamped to the current buffer length. The clamp matters for
    # reactive buffers that may shrink (e.g. a "clear stream" action) —
    # without it the counter could point past the end of a shorter
    # string and produce a stray cursor at the wrong column.
    def tick() -> None:
        current_len = len(_resolve_buffer(buffer))
        if revealed.value < current_len:
            revealed.value = min(revealed.value + 1, current_len)

    # The interval worker ticks once per character at the requested
    # rate. ``1000 / reveal_speed`` ms per tick means ``reveal_speed``
    # characters per second. Rounding up keeps the rate at-or-below the
    # requested value (faster rates would round down to 0 ms).
    interval_ms = max(1, round(1000 / reveal_speed))
    use_interval(tick, interval_ms)

    def display() -> str:
        text = _resolve_buffer(buffer)
        shown = text[: revealed.value]
        return shown + _render_cursor(cursor, cursor_color)

    return Text(display, color=color, **text_props)


def StreamingText(
    buffer: Signal[str] | Callable[[], str] | str,
    *,
    cursor: str | None = None,
    cursor_color: str | None = None,
    reveal_speed: int = 0,
    color: str | None = None,
    **text_props: Any,
) -> Element:
    """Render a piece of streaming text, optionally with a cursor and typing effect.

    Parameters
    ----------
    buffer:
        Text source. Three shapes are accepted (see module docstring):

        * :class:`Signal` ``[str]`` — reactive. Each ``.value`` write
          re-renders the surrounding component.
        * ``Callable[[], str]`` — evaluated lazily during layout.
        * ``str`` — static.

        The buffer may grow (the canonical AI-stream case), stay fixed
        (a typing animation over a known string), or shrink (a stream
        reset).
    cursor:
        Glyph appended to the end of the displayed text (e.g. ``"▋"`` /
        ``"|"``). ``None`` (default) renders no cursor. The cursor sits
        *after* the last revealed character so it visually leads the
        typing when ``reveal_speed > 0``.
    cursor_color:
        Optional colour spec for ``cursor`` (same format as
        :func:`Text`'s ``color``: ``"green"``, ``"#00ff00"``,
        ``"rgb(0,255,0)"``, ``"ansi256(2)"``). When set, the glyph is
        wrapped in SGR sequences; layout ignores ANSI bytes so the
        cursor keeps its single-cell width.
    reveal_speed:
        Characters per second to reveal. ``0`` (default) shows the
        buffer immediately on every change — the recommended mode for
        real AI streams where the network is already throttling. A
        positive value enables a typing animation driven by
        :func:`pyink.hooks.use_interval`; the displayed string grows by
        one character per tick until it catches up with the buffer.
    color:
        Forwarded to :func:`Text` as the text colour. Independent of
        ``cursor_color`` — set both to colour the cursor differently
        from the body.
    **text_props:
        Additional style props forwarded to :func:`Text`
        (``bold`` / ``italic`` / ``dimColor`` / ``wrap`` / …).

    Returns
    -------
    Element
        The fast path (``reveal_speed == 0``) returns a ``Text`` host
        element directly — no function component, no hooks. The
        reveal-speed branch returns an element whose ``type`` is
        :func:`_StreamingTextImpl`, a function component that owns a
        private ``revealed`` signal and an interval worker; that
        component is mounted by the reconciler, which is what makes
        ``Box(StreamingText(...), Text(...))`` safe to call from any
        context.

    Usage
    -----
    Reactive buffer::

        buf = signal("")
        # ... another thread appends characters ...
        Box(StreamingText(buf, cursor="▋"), padding=1)

    Static string with a typing effect::

        StreamingText("Hello, world!", cursor="|", reveal_speed=20)

    Callable buffer::

        StreamingText(lambda: f"{buf_a.value} / {buf_b.value}")
    """
    # Fast path: no interval, no function component. Just a Text whose
    # callable child resolves the buffer + cursor at layout time. This
    # is the cheapest code path and the default.
    if reveal_speed <= 0:
        def make_text() -> str:
            return _resolve_buffer(buffer) + _render_cursor(cursor, cursor_color)

        return Text(make_text, color=color, **text_props)

    # Reveal-speed branch: defer to a function component so we can
    # install ``use_interval``. The reconciler mounts it like any other
    # function component; the worker is torn down automatically on
    # unmount (see :func:`pyink.hooks.use_interval`).
    return create_element(
        _StreamingTextImpl,
        buffer=buffer,
        cursor=cursor,
        cursor_color=cursor_color,
        reveal_speed=reveal_speed,
        color=color,
        text_props=text_props,
    )
