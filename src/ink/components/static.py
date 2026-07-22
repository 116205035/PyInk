"""``Static`` — permanently render a list of items above the live frame (PR7).

Mirrors ink's ``<Static>``: items passed to ``Static`` are rendered exactly
once and written to stdout *above* the live frame. Already-rendered items
are never re-painted by the frame diff, so they accumulate like ordinary
log output and scroll off the top of the viewport naturally.

API:

.. code-block:: python

    from ink import Static, Text, signal

    log = signal([])

    def App():
        return Box(
            Static(log, lambda item, idx: Text(item)),
            Text(lambda: f"Total: {len(log.value)} items"),
            flexDirection="column",
        )

The first argument (``items``) may be:

* a plain ``list[T]`` — rendered once on mount and never updated,
* a :class:`ink.core.signal.Signal` wrapping ``list[T]``,
* or a ``Callable[[], list[T]]`` evaluated inside a reactive effect.

In the reactive cases a new render is performed only for the items
appended since the last flush; items that disappeared or moved are not
re-rendered or erased (matching ink's semantics — Static is append-only).

The ``render_item`` callback receives ``(item, index)`` and must return an
:class:`ink.core.element.Element`. Each item is rendered via
:func:`ink.render.render_to_string` with the current terminal width, so
ANSI styling (colour, bold, …) is honoured.

Implementation notes:

* Because PyInk function-component bodies run exactly once on mount, the
  incremental-flush logic lives inside a reactive :func:`effect` that
  re-runs whenever the items source changes.
* The component returns an empty ``box`` host flagged ``_ink_static=True``.
  The layout engine (:func:`ink.layout.flex.build_flex_tree`) treats
  that sentinel as zero-sized, so it never participates in flex layout
  nor appears in the painted frame. Its sole purpose is to give the
  reconciler something to mount so the effect's lifecycle is bound to
  the component instance.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from ink.core.element import Element, create_element
from ink.core.signal import Signal, effect, ref
from ink.hooks._runtime import _get_current_instance
from ink.render import render_to_string

__all__ = ["Static"]

T = TypeVar("T")


def Static(
    items: list[T] | Signal[list[T]] | Callable[[], list[T]],
    render_item: Callable[[T, int], Element],
    *,
    style: dict[str, object] | None = None,
) -> Element:
    """Permanently render ``items`` above the live frame.

    Parameters
    ----------
    items:
        Data source. Plain lists render once on mount; ``Signal`` /
        callable sources are tracked reactively so newly-appended items
        flush to the static region.
    render_item:
        ``(item, index) -> Element``. Called once per item, in source
        order, with the absolute index inside the full list (so the
        callback can show line numbers if it wants).
    style:
        Reserved for parity with ink's ``style`` prop. Currently
        honoured only as a passthrough — every item is rendered
        independently and the container itself contributes zero size
        to layout, so per-side padding / margin on the container would
        have no effect.
    """
    # Capture the source at element-construction time. We deliberately do
    # not normalise here — the inner component resolves it on each effect
    # run so reactive sources stay live.
    items_source = items
    render = render_item
    style_props = dict(style) if style else {}

    def StaticImpl() -> Element:
        inst = _get_current_instance()
        if inst is None:
            raise RuntimeError(
                "Static must be mounted via ink.render.render() — "
                "no active Instance found. Use render_to_string with a "
                "plain tree if you only need a static snapshot."
            )
        # ``write_static`` is added in PR7; defend against an older host.
        if not hasattr(inst, "write_static"):  # pragma: no cover
            raise RuntimeError(
                "Active Instance does not implement write_static(); "
                "Static requires PR7 or later."
            )

        # Track how many items we have already flushed. ``ref`` keeps the
        # value stable across effect re-runs without subscribing.
        last_flushed = ref(0)
        # Register with the host Instance so ``Instance.reset_static()``
        # can zero us in one pass. Without this, a Jarvis ``/clear`` or
        # ``/agent`` switch could not reset ``last_flushed`` from outside
        # the closure — the new agent's history would never flush because
        # ``_flush`` sees ``len(current) <= start`` and returns. See
        # ``Instance.reset_static`` docstring for the full flow.
        if hasattr(inst, "_register_static_ref"):
            inst._register_static_ref(last_flushed)

        def _read_source() -> list[T]:
            if isinstance(items_source, Signal):
                return list(items_source.value)
            if callable(items_source):
                return list(items_source())
            return list(items_source)

        def _flush() -> None:
            current = _read_source()
            start = last_flushed.value
            if len(current) <= start:
                # No new items — nothing to do. We *do not* shrink when the
                # list shortens: ink's Static is append-only and removing
                # earlier items does not erase already-written output.
                return
            new_slice = current[start:]
            columns = getattr(inst, "columns", 0) or 80
            chunks: list[str] = []
            for offset, item in enumerate(new_slice):
                element = render(item, start + offset)
                chunks.append(render_to_string(element, columns=columns))
            if not chunks:
                last_flushed.value = len(current)
                return
            # Each item is written on its own line; the trailing newline
            # ensures the next frame's first row lands below the new text.
            payload = "\n".join(chunks) + "\n"
            inst.write_static(payload)
            last_flushed.value = len(current)

        # The effect runs synchronously on mount (flushing the initial
        # items) and re-runs whenever a reactive source changes. It is
        # bound to this component instance via the active-component
        # ContextVar, so unmounting the Static disposes it.
        effect(_flush)

        # Return a layout sentinel — flagged so the layout engine skips it.
        # ``style_props`` are attached for API parity but have no effect.
        return create_element("box", _ink_static=True, **style_props)

    return create_element(StaticImpl)
