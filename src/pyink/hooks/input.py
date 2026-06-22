"""``use_input`` — subscribe to keyboard events (PR6).

Mirrors ink's ``useInput``. The handler receives a parsed
:class:`pyink.render.keys.Key` for every keystroke. ``is_active=False``
pauses the subscription (useful for multi-input focus management).

Implementation notes:

* Raw mode is entered automatically by the :class:`Terminal` when the
  first ``on_key`` subscriber registers.
* The dispose is bound to the current component via
  :func:`pyink.core.signal.effect`'s auto-binding mechanism: we create
  a no-op effect purely to obtain a dispose callback that's registered
  with the component instance. (Direct registration would require
  reaching into ``_current_component`` — using ``effect`` keeps the
  abstraction.)
* The default ``exit_on_ctrl_c`` behaviour (when raw mode is active) is
  handled by the render pipeline, which installs its own internal
  Ctrl+C listener. This hook just forwards keys.
"""

from __future__ import annotations

from collections.abc import Callable

from pyink.core.signal import Ref, effect, ref
from pyink.hooks._runtime import _get_current_instance
from pyink.render.keys import Key

__all__ = ["use_input"]


def use_input(
    handler: Callable[[Key], None],
    *,
    is_active: bool = True,
) -> Callable[[], None]:
    """Subscribe ``handler`` to keyboard :class:`Key` events.

    Parameters
    ----------
    handler:
        Called on the input-reader thread for every parsed keypress.
        Exceptions raised inside the handler are swallowed by the
        Terminal's dispatcher (so one bad handler can't kill the loop).
    is_active:
        When ``False`` the handler is not invoked. Toggling this at
        runtime (e.g. via a signal-driven flag) is the standard way to
        implement focus management between multiple input consumers.

    Returns
    -------
    dispose
        Call to unsubscribe. Also auto-invoked on component unmount.
    """
    inst = _get_current_instance()
    if inst is None:
        raise RuntimeError(
            "use_input() must be called from inside a function component "
            "mounted via pyink.render.render()"
        )
    terminal = inst.terminal

    # ``active_ref`` lets the toggle flip without re-subscribing.
    active_ref: Ref[bool] = ref(is_active)
    # ``handler_ref`` keeps the latest handler without re-subscribing —
    # closures over local state otherwise go stale.
    handler_ref: Ref[Callable[[Key], None]] = ref(handler)
    handler_ref.value = handler

    def on_key(key: Key) -> None:
        if not active_ref.value:
            return
        h = handler_ref.value
        h(key)

    dispose_subscribe = terminal.on_key(on_key)

    # The ``effect`` here is just a vehicle for auto-dispose binding —
    # we don't actually need reactivity. Registering through ``effect``
    # means the dispose is added to the current component's cleanup
    # list, so unmount will tear down the subscription.
    def _setup() -> Callable[[], None]:
        def cleanup() -> None:
            dispose_subscribe()

        return cleanup

    effect_dispose = effect(_setup)
    # ``effect`` already ran _setup once synchronously, registering the
    # dispose on the component instance. We return a manual dispose that
    # both unsubscribes and tears down the effect binding.
    #
    # A ``_disposed`` flag guards against double-invocation: the manual
    # dispose is meant to be called by the caller *and* is registered on
    # the component instance for unmount. Without the guard, calling the
    # manual dispose and then unmounting would invoke ``dispose_subscribe``
    # twice (once directly, once via the effect cleanup) and re-enter
    # ``effect_dispose`` on an already-disposed Effect. The flag makes the
    # second call a no-op so both entry points are truly idempotent.
    _disposed = False

    def dispose() -> None:
        nonlocal _disposed
        if _disposed:
            return
        _disposed = True
        dispose_subscribe()
        if effect_dispose is not None:
            effect_dispose()

    return dispose
