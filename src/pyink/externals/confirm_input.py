"""``ConfirmInput`` — Y/N confirmation prompt (Phase 4 PR4).

Mirrors ``ink-confirm-input`` at the level Phase 4 needs: a focused
Y / N prompt that fires :attr:`on_confirm` / :attr:`on_cancel` either on
the single keystroke (default) or after Enter confirms a pre-selected
option (``require_enter=True``).

Design (per PRD Decision 5 + Phase 4 design notes):

* ``ConfirmInput`` is a factory returning an :class:`Element` whose
  ``type`` is the :func:`_ConfirmInputImpl` function component. The
  factory itself never runs hooks; only the wrapped function does,
  when the reconciler mounts it. This is the same shape as
  :func:`pyink.externals.TextInput` (PR1) and
  :func:`pyink.externals.SelectInput` (PR3), so callers can freely nest
  ``Box(ConfirmInput(...), Text(...))`` outside of a render context.
* One writable :class:`Signal` lives inside the component: ``selected``
  (``"confirm"`` / ``"cancel"`` / ``None``). The handler closure writes
  it; the render closure reads it through lazy callables so the render
  loop re-paints on each change.
* Callbacks (``on_confirm`` / ``on_cancel``) are captured in
  :class:`Ref` s refreshed every mount — this lets the caller swap
  callbacks between mounts without resubscribing the input handler. The
  handler closure is registered exactly once via
  :func:`pyink.hooks.use_input`.

Key bindings (``require_enter=False``, the default):

* ``confirm_key`` — fires :attr:`on_confirm` immediately.
* ``cancel_key`` — fires :attr:`on_cancel` (no-op when ``None``).
* ``Esc`` — fires :attr:`on_cancel` when set; otherwise falls through
  to the surrounding pipeline.

Key bindings (``require_enter=True``):

* ``confirm_key`` — moves the highlight to the confirm option.
* ``cancel_key`` — moves the highlight to the cancel option.
* ``Enter`` — confirms the highlighted option (fires the matching
  callback). When nothing is highlighted (``default=None``), Enter is a
  no-op so the user must first press ``confirm_key`` / ``cancel_key``.
* ``Esc`` — fires :attr:`on_cancel` when set; otherwise falls through.

Rendering:

* The prompt renders on its own row in bold.
* The two options render side by side (``flexDirection="row"``). Each
  option's glyph mirrors the underlying key: ``[y]`` for single-key
  mode, ``[y]`` / ``(y)`` for require-enter mode depending on whether
  it is the currently highlighted option.
* The highlighted option is rendered in ``selected_color``
  (``"green"`` by default); the other option inherits ``color`` (or no
  colour when ``color is None``).

Out of scope (Phase 4):

* Free-text Y / N parsing (ink-confirm-input wraps TextInput + the
  ``yn`` package). PyInk's variant uses dedicated hotkeys, which keeps
  the surface tiny and the interaction one keystroke.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from pyink.components.box import Box
from pyink.components.text import Text
from pyink.core.element import Element, create_element
from pyink.core.signal import Ref, Signal, ref, signal
from pyink.hooks.input import use_input
from pyink.render.keys import Key

__all__ = ["ConfirmInput"]

#: Which option is highlighted in ``require_enter=True`` mode. ``None``
#: means "neither" — the user must press ``confirm_key`` / ``cancel_key``
#: first to pick a side before Enter will fire anything.
_Selection = Literal["confirm", "cancel"]


def _derive_label(key: str) -> str:
    """Derive a display label from a single-key shortcut.

    Maps a one-character key to its common full word:

    * ``"y"`` → ``"yes"``, ``"Y"`` → ``"Yes"``
    * ``"n"`` → ``"no"``,  ``"N"`` → ``"No"``
    * ``"q"`` → ``"quit"``, ``"Q"`` → ``"Quit"``
    * ``"o"`` → ``"ok"``,   ``"O"`` → ``"Ok"``
    * ``"c"`` → ``"cancel"``, ``"C"`` → ``"Cancel"``
    * anything else falls back to ``key.lower()`` repeated so the label
      is never empty (``"x"`` → ``"xx"``). Callers who want a different
      word pass an explicit ``confirm_label`` / ``cancel_label``.

    The fallback is intentionally minimal — a real ``yn`` parser is out
    of scope, and the common keys cover the typical Y / N / Q prompts.
    """
    if not key:
        return ""
    lower = key.lower()
    upper = key.isupper()
    word_map = {
        "y": "yes",
        "n": "no",
        "q": "quit",
        "o": "ok",
        "c": "cancel",
        "a": "abort",
        "s": "skip",
        "r": "retry",
    }
    word = word_map.get(lower, lower)
    if upper:
        return word[:1].upper() + word[1:]
    return word


def _ConfirmInputImpl(**props: Any) -> Element:
    """Function component body — runs inside the reconciler render context.

    Owns the ``selected`` signal (which option is highlighted, or
    ``None``), registers the keyboard handler via :func:`use_input`, and
    returns a :func:`Box` containing the prompt row and an options row.
    The two option :func:`Text` s are lazy callables that re-evaluate on
    every signal write so the paint tracks the current highlight without
    re-mounting.
    """
    on_confirm: Callable[[], None] = props["on_confirm"]
    on_cancel: Callable[[], None] | None = props["on_cancel"]
    prompt: str = props["prompt"]
    confirm_key: str = props["confirm_key"]
    cancel_key: str = props["cancel_key"]
    require_enter: bool = props["require_enter"]
    default: _Selection | None = props["default"]
    confirm_label: str = props["confirm_label"]
    cancel_label: str = props["cancel_label"]
    color: str | None = props["color"]
    selected_color: str | None = props["selected_color"]
    is_active: bool = props["is_active"]
    box_props: dict[str, Any] = props["box_props"]

    # Normalise the comparison keys once — handlers compare against
    # ``.lower()`` so ``"Y"`` / ``"N"`` configured by callers behave the
    # same as the defaults. The displayed glyph keeps the caller's case.
    confirm_key_lower = confirm_key.lower()
    cancel_key_lower = cancel_key.lower()

    selected: Signal[_Selection | None] = signal(default)

    # Keep the latest callbacks without resubscribing the input handler.
    # Mirrors the TextInput (PR1) / SelectInput (PR3) pattern: the
    # handler closure reads ``.value`` so future mounts that swap
    # callbacks don't need to re-subscribe.
    on_confirm_ref: Ref[Callable[[], None]] = ref(on_confirm)
    on_cancel_ref: Ref[Callable[[], None] | None] = ref(on_cancel)
    on_confirm_ref.value = on_confirm
    on_cancel_ref.value = on_cancel

    def _fire_confirm() -> None:
        cb = on_confirm_ref.value
        if cb is not None:
            cb()

    def _fire_cancel() -> None:
        cb = on_cancel_ref.value
        if cb is not None:
            cb()

    def handle_key(key: Key) -> None:
        if not is_active:
            return

        # Escape always maps to cancel (when a cancel callback is set).
        # When ``on_cancel is None`` we deliberately fall through so the
        # surrounding exit_on_ctrl_c / app-level cancel pipeline still
        # owns the keystroke.
        if key.escape and not key.ctrl and not key.alt:
            if on_cancel_ref.value is not None:
                _fire_cancel()
            return

        # The confirm / cancel hotkeys are case-insensitive single
        # characters; ignore them when Ctrl / Alt are held so app-level
        # shortcuts (Ctrl+Y, Alt+N, …) keep working.
        if key.input and not key.ctrl and not key.alt:
            pressed = key.input.lower()
            if pressed == confirm_key_lower:
                if require_enter:
                    selected.value = "confirm"
                else:
                    _fire_confirm()
                return
            if pressed == cancel_key_lower:
                if require_enter:
                    selected.value = "cancel"
                else:
                    _fire_cancel()
                return

        # Enter — only meaningful in require_enter mode. Fires whichever
        # option is currently highlighted; a no-op when nothing is
        # highlighted yet (``default=None`` and no key pressed).
        if key.return_key and not key.ctrl and not key.alt:
            if require_enter:
                sel = selected.value
                if sel == "confirm":
                    _fire_confirm()
                elif sel == "cancel":
                    _fire_cancel()
            return

        # All other keys (Tab, Ctrl+C, function keys, …) are left for
        # the surrounding pipeline to handle.

    use_input(handle_key, is_active=is_active)

    def _confirm_prefix() -> str:
        """Glyph shown before the confirm label.

        In single-key mode every option is always shown as ``[key]``;
        in require-enter mode the highlighted option uses ``[key]`` and
        the other uses ``(key)``.
        """
        if not require_enter:
            return f"[{confirm_key}]"
        return (
            f"[{confirm_key}]"
            if selected.value == "confirm"
            else f"({confirm_key})"
        )

    def _cancel_prefix() -> str:
        if not require_enter:
            return f"[{cancel_key}]"
        return (
            f"[{cancel_key}]"
            if selected.value == "cancel"
            else f"({cancel_key})"
        )

    def _confirm_color() -> str | None:
        return selected_color if selected.value == "confirm" else color

    def _cancel_color() -> str | None:
        return selected_color if selected.value == "cancel" else color

    def _confirm_text() -> str:
        return f"{_confirm_prefix()} {confirm_label}"

    def _cancel_text() -> str:
        return f"{_cancel_prefix()} {cancel_label}"

    return Box(
        Text(prompt, bold=True),
        Box(
            Text(_confirm_text, color=_confirm_color),
            Text(_cancel_text, color=_cancel_color),
            flexDirection="row",
            gap=2,
        ),
        flexDirection="column",
        **box_props,
    )


def ConfirmInput(
    on_confirm: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
    *,
    prompt: str = "Confirm?",
    confirm_key: str = "y",
    cancel_key: str = "n",
    require_enter: bool = False,
    default: str | None = None,
    confirm_label: str | None = None,
    cancel_label: str | None = None,
    color: str | None = None,
    selected_color: str | None = "green",
    is_active: bool = True,
    **box_props: Any,
) -> Element:
    """Create a Y/N confirmation prompt.

    Parameters
    ----------
    on_confirm:
        Called (no arguments) when the user confirms. This is the only
        required callback — there is no useful "confirmation input"
        without it.
    on_cancel:
        Called when the user cancels. When ``None`` (default), cancel
        keystrokes are silently swallowed inside the component; the
        surrounding ``exit_on_ctrl_c`` / app-level pipeline still owns
        Esc / Ctrl+C.
    prompt:
        Text rendered above the options. Defaults to ``"Confirm?"``.
    confirm_key:
        Single character that confirms (case-insensitive). Defaults to
        ``"y"``.
    cancel_key:
        Single character that cancels (case-insensitive). Defaults to
        ``"n"``.
    require_enter:
        ``False`` (default) — pressing ``confirm_key`` / ``cancel_key``
        fires the callback immediately. ``True`` — the key only moves
        the highlight; the user must then press ``Enter`` to fire.
    default:
        Initial highlight in ``require_enter=True`` mode: ``"confirm"``,
        ``"cancel"``, or ``None`` (default — nothing highlighted, Enter
        is a no-op until the user picks a side).
    confirm_label / cancel_label:
        Visible labels next to the key glyphs. Default to a derived word
        (``"yes"`` for ``y``, ``"no"`` for ``n``, ``"quit"`` for ``q``,
        …) — pass an explicit string to override.
    color:
        Colour spec for the non-highlighted option(s) (``"red"``,
        ``"#ff0000"``, ``"rgb(255,0,0)"``, ``"ansi256(9)"``). ``None``
        (default) leaves them in the default terminal colour.
    selected_color:
        Colour spec applied to the highlighted option. Defaults to
        ``"green"``. Pass ``None`` to keep the highlighted option in the
        default colour.
    is_active:
        When ``False`` the input ignores all keystrokes. Toggle at
        runtime to switch focus between multiple inputs.
    **box_props:
        Forwarded to the wrapping :func:`Box` (``padding``,
        ``borderStyle``, ``width``, …).

    Returns
    -------
    Element
        An element whose ``type`` is the :func:`_ConfirmInputImpl`
        function component. The factory itself never runs hooks — the
        reconciler mounts the function, which is what makes
        ``Box(ConfirmInput(...), Text(...))`` safe to call from outside
        a render context.

    Key bindings
    ------------
    * ``confirm_key`` — confirm (single-key mode) or highlight confirm
      (require-enter mode).
    * ``cancel_key`` — cancel (single-key mode) or highlight cancel
      (require-enter mode).
    * ``Enter`` — confirm the highlighted option (require-enter mode
      only; ignored in single-key mode).
    * ``Esc`` — fire ``on_cancel`` when it is set; otherwise fall
      through to the surrounding pipeline.

    Usage
    -----
    ::

        Box(
            Text("Delete all files?", bold=True, color="red"),
            ConfirmInput(
                on_confirm=delete_everything,
                on_cancel=abort,
                prompt="This cannot be undone.",
                require_enter=True,
                default="cancel",
            ),
            flexDirection="column",
        )

    Custom keys (``q`` to confirm, ``Esc`` to cancel)::

        ConfirmInput(
            on_confirm=quit_app,
            confirm_key="q",
            cancel_key="x",   # Esc still fires on_cancel
        )
    """
    # Validate ``default`` early — passing ``"maybe"`` would otherwise
    # silently highlight nothing on mount, which is hard to debug.
    if default is not None and default not in ("confirm", "cancel"):
        raise ValueError(
            "default must be 'confirm' | 'cancel' | None, got "
            f"{default!r}"
        )

    # The check above narrows ``default`` to ``"confirm" | "cancel" | None``
    # at runtime; ``cast`` carries that narrowing into the type system
    # so the impl's ``_Selection``-typed prop matches.
    normalised_default = cast("_Selection | None", default)

    derived_confirm_label = (
        confirm_label if confirm_label is not None else _derive_label(confirm_key)
    )
    derived_cancel_label = (
        cancel_label if cancel_label is not None else _derive_label(cancel_key)
    )

    return create_element(
        _ConfirmInputImpl,
        on_confirm=on_confirm,
        on_cancel=on_cancel,
        prompt=prompt,
        confirm_key=confirm_key,
        cancel_key=cancel_key,
        require_enter=require_enter,
        default=normalised_default,
        confirm_label=derived_confirm_label,
        cancel_label=derived_cancel_label,
        color=color,
        selected_color=selected_color,
        is_active=is_active,
        box_props=box_props,
    )
