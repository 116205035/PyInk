"""``SelectInput`` — keyboard-navigable option list (Phase 4 PR3).

Mirrors ``ink-select-input`` (and the multi-select variant from
``ink-multi-select``) at the level Phase 4 needs:

* Single-select — ``ArrowUp`` / ``ArrowDown`` / ``j`` / ``k`` move the
  highlight; ``Enter`` triggers :attr:`on_select` with the focused
  item's ``value``; numeric keys ``1``–``9`` jump directly to that
  index.
* Multi-select — ``Space`` toggles the focused item in/out of the
  selection set; ``Enter`` confirms the whole selection by triggering
  :attr:`on_select` with a ``list`` of selected values (sorted by
  index).
* ``on_change`` fires whenever the focused index moves (regardless of
  whether the move was triggered by arrows, ``j`` / ``k``, or a number
  key).
* ``is_active=False`` parks the input — the handler is registered with
  :func:`use_input` 's own ``is_active`` flag so toggling focus is
  cheap (no re-subscribe).

Design (per PRD Decision 4 + Phase 4 design notes):

* ``SelectInput`` is a factory returning an :class:`Element` whose
  ``type`` is the :func:`_SelectInputImpl` function component. The
  factory itself never runs hooks; only the wrapped function does,
  when the reconciler mounts it. This is the same shape as
  :func:`pyink.externals.TextInput` (Phase 4 PR1) so callers can freely
  nest ``Box(SelectInput(...), Text(...))`` outside of a render
  context.
* Two :class:`Signal` s live inside the component: ``current`` (the
  focused index, clamped to ``[0, len(items) - 1]``) and ``selected``
  (a ``set[int]`` of indices, used only when ``multi_select=True``).
  The handler closure captures both and the render closure reads them
  through lazy callables so the render loop re-paints on each change.
* Callbacks (``on_select`` / ``on_change``) are captured in
  :class:`Ref` s refreshed every mount — this lets the caller swap
  callbacks between mounts without resubscribing the input handler.
* Items are normalised up-front to ``[{"label": str, "value": Any},
  ...]``: bare strings become ``{"label": s, "value": s}``, dicts must
  carry both ``label`` and ``value`` (anything else raises). Mixed
  str/dict lists are accepted; the helper coerces str entries.

Tab is intentionally *not* bound (PRD Decision 4 — SelectInput does
not own focus rotation; that's the job of an external
``use_focus_manager``). Esc is left for the surrounding pipeline
(``exit_on_ctrl_c`` / app-level cancel handlers).

Rendering:

* Each item is one :func:`Text` row.
* The focused row's prefix is the ``indicator`` glyph (``"❯"`` by
  default); non-focused rows get an all-space placeholder of the same
  width so the labels line up.
* In ``multi_select`` mode each row additionally carries
  ``selected_indicator`` (``"✓"``) when the item is in the selection
  set, or ``unselected_indicator`` (``" "``) otherwise.
* The focused row is rendered in ``selected_color`` (``"green"`` by
  default); non-focused rows inherit ``color`` (or no colour when
  ``color is None``).

Out of scope (Phase 4):

* Scrolling / ``limit`` / windowed rendering (ink-select-input's
  ``limit`` + ``rotateIndex`` dance). Every item is always rendered.
* A custom indicator / item component slot. The two indicators are
  strings; callers who want a custom glyph pass a different
  ``indicator``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Ref, Signal, ref, signal
from pyink.hooks.input import use_input
from pyink.render.keys import Key

__all__ = ["SelectInput"]

#: Normalised item shape — every entry passed to the impl is one of
#: these. ``value`` is untyped (``Any``) because callers may pass
#: arbitrary domain objects (enums, dataclasses, ints, …); the impl
#: just forwards it to ``on_select``.
_NormalisedItem = dict[str, Any]


def _normalize_items(
    items: list[str] | list[dict[str, Any]],
) -> list[_NormalisedItem]:
    """Coerce ``items`` to a uniform ``[{"label": str, "value": Any}, ...]`` list.

    Bare strings become ``{"label": s, "value": s}``. Dict entries must
    carry both ``label`` and ``value``. Mixed lists are accepted
    (string + dict entries can coexist).

    Raises
    ------
    TypeError
        An entry is neither ``str`` nor ``dict``.
    ValueError
        A ``dict`` entry is missing ``label`` or ``value``.
    """
    result: list[_NormalisedItem] = []
    for item in items:
        if isinstance(item, str):
            result.append({"label": item, "value": item})
        elif isinstance(item, dict):
            if "label" not in item or "value" not in item:
                raise ValueError(
                    "dict items must have both 'label' and 'value' keys"
                )
            result.append(item)
        else:
            raise TypeError(
                "items must be list[str] or list[dict], got "
                f"{type(item).__name__}"
            )
    return result


def _SelectInputImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Owns the ``current`` (focused index) and ``selected`` (multi-select
    index set) signals, registers the keyboard handler via
    :func:`use_input`, and returns a :func:`Box` containing one
    :func:`Text` row per item. Each :func:`Text` child is a callable
    that re-evaluates on every signal write so the paint tracks the
    current focus / selection state without re-mounting.
    """
    items: list[_NormalisedItem] = props["items"]
    initial_index: int = props["initial_index"]
    on_select: Callable[[Any], None] | None = props["on_select"]
    on_change: Callable[[int], None] | None = props["on_change"]
    multi_select: bool = props["multi_select"]
    indicator: str = props["indicator"]
    selected_indicator: str = props["selected_indicator"]
    unselected_indicator: str = props["unselected_indicator"]
    color: str | None = props["color"]
    selected_color: str | None = props["selected_color"]
    is_active: bool = props["is_active"]
    box_props: dict[str, Any] = props["box_props"]

    # Empty-list guard: render an empty column Box so the component is
    # safe to mount with zero items (a downstream UI can swap items in
    # later). Nothing to focus, nothing to navigate — handler is still
    # registered (no-op) to keep the hook contract uniform.
    if not items:
        return Box(flexDirection="column", **box_props)

    # Clamp the initial focus to a valid index; negative values and
    # out-of-range positives both settle on the nearest endpoint.
    clamped_initial = max(0, min(initial_index, len(items) - 1))
    current: Signal[int] = signal(clamped_initial)
    selected: Signal[frozenset[int]] = signal(frozenset())

    # Capture the latest callbacks without resubscribing the input
    # handler. Mirrors the TextInput (PR1) pattern: the handler closure
    # reads ``.value`` so future mounts that swap callbacks don't need
    # to re-subscribe.
    on_select_ref: Ref[Callable[[Any], None] | None] = ref(on_select)
    on_change_ref: Ref[Callable[[int], None] | None] = ref(on_change)
    on_select_ref.value = on_select
    on_change_ref.value = on_change

    def _set_current(next_idx: int) -> None:
        """Move the focus to ``next_idx`` (clamped) and fire ``on_change``.

        ``on_change`` only fires when the focus actually moves — a
        no-op move (already at the boundary) leaves the signal and the
        callback alone.
        """
        clamped = max(0, min(next_idx, len(items) - 1))
        old = current.value
        if clamped == old:
            return
        current.value = clamped
        cb = on_change_ref.value
        if cb is not None:
            cb(clamped)

    def _toggle_selected(idx: int) -> None:
        """Toggle ``idx`` in/out of the selection set (multi-select only).

        Builds a fresh ``frozenset`` rather than mutating in place so
        the signal's identity check fires and subscribers re-run.
        """
        cur = selected.value
        next_set = cur - {idx} if idx in cur else cur | {idx}
        selected.value = next_set

    def handle_key(key: Key) -> None:
        if not is_active:
            return

        # Vim-style ``k`` / ``j`` (no Ctrl, no Alt) — fall through to
        # the arrow handler. ``key.input.lower()`` normalises Shift.
        is_k = (
            key.input
            and not key.ctrl
            and not key.alt
            and key.input.lower() == "k"
        )
        is_j = (
            key.input
            and not key.ctrl
            and not key.alt
            and key.input.lower() == "j"
        )

        if key.up_arrow or is_k:
            _set_current(current.value - 1)
            return

        if key.down_arrow or is_j:
            _set_current(current.value + 1)
            return

        # Numeric jump — ``1``..``9`` map directly to indices 0..8.
        # ``0`` is deliberately excluded (matches ink-select-input's
        # ``/^[1-9]$/`` check) so it doesn't compete with leading-zero
        # numeric typing elsewhere in the host app.
        if (
            key.input
            and not key.ctrl
            and not key.alt
            and not key.shift
            and key.input.isdigit()
            and key.input != "0"
        ):
            target = int(key.input) - 1
            if 0 <= target < len(items):
                _set_current(target)
            return

        # Space — toggle the focused item (multi-select only).
        if key.input == " " and multi_select and not key.ctrl and not key.alt:
            _toggle_selected(current.value)
            return

        # Enter — single-select fires on_select(value); multi-select
        # fires on_select(list[value, ...]) sorted by index.
        if key.return_key and not key.ctrl and not key.alt:
            cb = on_select_ref.value
            if cb is None:
                return
            if multi_select:
                indices = sorted(selected.value)
                cb([items[i]["value"] for i in indices])
            else:
                cb(items[current.value]["value"])
            return

        # Esc / Tab / Ctrl+C / other keys are intentionally left for
        # the surrounding pipeline to handle (PRD Decision 4 — SelectInput
        # does not own focus rotation or app-level cancel).

    use_input(handle_key, is_active=is_active)

    # Space-pad the indicator so non-focused rows align with focused
    # ones even when the indicator is multi-cell (e.g. an emoji).
    # ``selected_indicator`` / ``unselected_indicator`` are always used
    # verbatim (no padding) because in multi-select mode every row
    # carries one or the other — the widths are the caller's contract.
    indicator_pad = " " * len(indicator)

    def _row_text(idx: int) -> str:
        """Build the visible label for row ``idx`` (indicators + label).

        Layout (left → right):

        * ``[selected_indicator | unselected_indicator]`` — only when
          ``multi_select``; collapses to nothing otherwise.
        * ``[indicator | indicator_pad]`` — focused row shows the
          indicator glyph, every other row shows the matching-width
          space pad.
        * ``item["label"]``
        """
        parts: list[str] = []
        if multi_select:
            if idx in selected.value:
                parts.append(selected_indicator)
            else:
                parts.append(unselected_indicator)
        if idx == current.value:
            parts.append(indicator)
        else:
            parts.append(indicator_pad)
        parts.append(items[idx]["label"])
        return " ".join(parts)

    def _row_color(idx: int) -> str | None:
        """Focused rows render in ``selected_color``; others in ``color``."""
        if idx == current.value:
            return selected_color
        return color

    def _make_row(idx: int) -> Element:
        """Build a per-row Text element with lazy callable content + colour.

        The callables close over ``idx`` and read the signals at paint
        time — so a focus / selection change repaints only this row's
        style without re-mounting the component.
        """
        # Capture idx in the local closure (loop variable hygiene).
        captured = idx

        def text_getter() -> str:
            return _row_text(captured)

        def color_getter() -> str | None:
            return _row_color(captured)

        return Text(text_getter, color=color_getter)

    return Box(
        *[_make_row(i) for i in range(len(items))],
        flexDirection="column",
        **box_props,
    )


def SelectInput(
    items: list[str] | list[dict[str, Any]],
    *,
    initial_index: int = 0,
    on_select: Callable[[Any], None] | None = None,
    on_change: Callable[[int], None] | None = None,
    multi_select: bool = False,
    indicator: str = "❯",
    selected_indicator: str = "✓",
    unselected_indicator: str = " ",
    color: str | None = None,
    selected_color: str | None = "green",
    is_active: bool = True,
    **box_props: Any,
) -> Element:
    """Create a keyboard-navigable option list.

    Parameters
    ----------
    items:
        Options to render. Each entry is either a ``str`` (label and
        value are the same string) or a ``dict`` with ``"label"`` and
        ``"value"`` keys. Mixed lists (some ``str``, some ``dict``)
        are accepted. ``dict`` entries missing either key raise
        :class:`ValueError`; entries of any other type raise
        :class:`TypeError`.
    initial_index:
        Index of the row that should be focused on mount. Clamped to
        ``[0, len(items) - 1]``; out-of-range values silently settle
        on the nearest endpoint rather than raising.
    on_select:
        Called with the selected value when the user confirms a
        choice. In single-select mode this fires with the focused
        item's ``value`` on ``Enter``. In multi-select mode it fires
        with a ``list`` of the selected values (sorted by index) on
        ``Enter``. ``None`` (default) disables selection.
    on_change:
        Called with the new focus index whenever the highlight moves
        (arrows, ``j`` / ``k``, or a numeric jump). Not invoked for
        no-op moves (already at the boundary) or for ``Space`` /
        ``Enter``.
    multi_select:
        When ``True``, ``Space`` toggles the focused item's membership
        in the selection set and ``Enter`` triggers ``on_select`` with
        the whole selection as a list. When ``False`` (default),
        ``Space`` is ignored and ``Enter`` triggers ``on_select`` with
        the focused value.
    indicator:
        Glyph shown before the focused row. Defaults to ``"❯"``.
    selected_indicator:
        Glyph shown before each selected row in multi-select mode.
        Defaults to ``"✓"``.
    unselected_indicator:
        Glyph shown before each unselected row in multi-select mode.
        Defaults to a single space.
    color:
        Colour spec for non-focused rows (``"red"``, ``"#ff0000"``,
        ``"rgb(255,0,0)"``, ``"ansi256(9)"``). ``None`` (default)
        leaves them in the default terminal colour.
    selected_color:
        Colour spec applied to the focused row. Defaults to
        ``"green"``. Pass ``None`` to keep the focused row in the
        default colour.
    is_active:
        When ``False`` the input ignores all keystrokes. Toggle at
        runtime to switch focus between multiple ``SelectInput`` s.
    **box_props:
        Forwarded to the wrapping :func:`Box` (``padding``,
        ``borderStyle``, ``width``, …).

    Returns
    -------
    Element
        An element whose ``type`` is the :func:`_SelectInputImpl`
        function component. The factory itself never runs hooks — the
        reconciler mounts the function, which is what makes
        ``Box(SelectInput(...), Text(...))`` safe to call from outside
        a render context.

    Key bindings
    ------------
    * ``ArrowUp`` / ``k`` — move the focus up (clamps at top).
    * ``ArrowDown`` / ``j`` — move the focus down (clamps at bottom).
    * ``1``–``9`` — jump directly to that index.
    * ``Space`` (multi-select only) — toggle the focused item.
    * ``Enter`` — confirm (single-select fires ``on_select(value)``,
      multi-select fires ``on_select(list[value, ...])``).

    Tab and Esc are deliberately not handled here — Tab is owned by an
    external ``use_focus_manager`` (PRD Decision 4) and Esc / Ctrl+C
    are owned by the surrounding exit / cancel pipeline.

    Usage
    -----
    ::

        Box(
            Text("Pick a fruit:", bold=True),
            SelectInput(
                ["Apple", "Banana", "Cherry"],
                on_select=lambda v: print(f"Selected: {v}"),
            ),
        )

    Multi-select::

        SelectInput(
            [
                {"label": "Read",  "value": "r"},
                {"label": "Write", "value": "w"},
                {"label": "Exec",  "value": "x"},
            ],
            multi_select=True,
            on_select=lambda vals: print(f"Permissions: {''.join(vals)}"),
        )
    """
    normalised = _normalize_items(items)

    return create_element(
        _SelectInputImpl,
        items=normalised,
        initial_index=initial_index,
        on_select=on_select,
        on_change=on_change,
        multi_select=multi_select,
        indicator=indicator,
        selected_indicator=selected_indicator,
        unselected_indicator=unselected_indicator,
        color=color,
        selected_color=selected_color,
        is_active=is_active,
        box_props=box_props,
    )
