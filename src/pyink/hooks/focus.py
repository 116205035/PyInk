"""``use_focus`` + ``use_focus_manager`` (Phase 2 PR6).

Mirrors ink's ``use-focus`` / ``use-focus-manager`` (see
``D:/Projects/github/ink/ink-master/src/hooks/use-focus-manager.ts``).

The two hooks sit on top of the Phase 2 PR5 Context system:

* :func:`use_focus_manager` builds a fresh :class:`FocusManager`, exposes
  it as a :class:`FocusManagerHandle` (control API + ``wrap`` builder for
  the Provider), and lets descendants read the manager back via the
  module-level ``_FOCUS_MANAGER_CONTEXT``.
* :func:`use_focus` reads the nearest manager from context (falling back
  to a module-level :class:`NullFocusManager` when no
  ``use_focus_manager`` subtree wraps the consumer), registers a
  :class:`FocusHandle` on mount, and disposes it on unmount via the
  component's effect-cleanup binding.

Tab / Shift+Tab default binding is intentionally NOT wired here — the
PRD marks it optional and the simplest correct API is to expose
``focus_next`` / ``focus_previous`` on the manager handle and let callers
hook them up to :func:`pyink.hooks.use_input` in their own key handler
(see the focus example). This keeps the focus hooks free of any
input-parsing dependency.

Reactivity notes:

* The handle's ``is_focused`` is a :class:`~pyink.core.signal.Signal`.
  Consumers read ``.value`` inside a callable style prop / ``Text`` leaf
  / ``effect`` to subscribe to focus changes — that triggers a re-render
  via the render loop's tracking context, exactly like the
  ``Spinner`` frame signal.
* The manager's ``handles`` / ``active_index`` / ``enabled`` signals are
  read-only from the consumer's perspective — they exist so
  introspection (``active_id``) stays live when read inside an effect.
"""

from __future__ import annotations

from typing import Any

from pyink.core.context import Context, create_context
from pyink.core.signal import effect
from pyink.hooks._focus_runtime import (
    FocusHandle,
    FocusManager,
    FocusManagerHandle,
    NullFocusManager,
    _next_handle_id,
)
from pyink.hooks._runtime import _get_current_instance
from pyink.hooks.context import use_context

__all__ = ["use_focus", "use_focus_manager"]


#: Module-level Context carrying the active :class:`FocusManager` for the
#: current subtree. Defaults to a shared :class:`NullFocusManager` so
#: ``use_focus`` calls outside any ``use_focus_manager`` subtree still
#: return a working handle (``is_focused`` permanently ``False``).
#:
#: Kept at module scope (not inside ``use_focus_manager``) so every call
#: site agrees on the id — otherwise Providers and Consumers would never
#: match. The default is reused across consumers to avoid spawning a
#: fresh NullFocusManager per ``use_focus`` call.
_NULL_MANAGER: NullFocusManager = NullFocusManager()
_FOCUS_MANAGER_CONTEXT: Context[FocusManager] = create_context(_NULL_MANAGER)


#: Type alias for the options dict accepted by :func:`use_focus`. Kept as
#: a plain dict (not a frozen dataclass) to match the flexibility of ink's
#: ``FocusOptions`` — callers can pass any subset of keys.
FocusOptions = dict[str, Any]


def use_focus_manager() -> FocusManagerHandle:
    """Create a :class:`FocusManager` for the current subtree.

    Returns
    -------
    FocusManagerHandle
        Carries the underlying manager and a ``wrap(*children)`` method
        that builds a Provider Element injecting the manager into the
        subtree. Callers mount the Provider around their focusable
        children::

            def App() -> Element:
                focus = use_focus_manager()
                return focus.wrap(Input1(), Input2(), Text("..."))

        The handle also exposes the manager's control methods
        (``focus_next`` / ``focus_previous`` / ``focus`` / ``enable`` /
        ``disable``) and a live ``active_id`` so key handlers can drive
        navigation::

            def App() -> Element:
                focus = use_focus_manager()
                def on_key(key: Key) -> None:
                    if key.tab:
                        if key.shift:
                            focus.focus_previous()
                        else:
                            focus.focus_next()
                use_input(on_key)
                return focus.wrap(Input("a"), Input("b"))

    Why a handle object + explicit ``wrap``:

    * The hook needs to return both the control surface (so the caller
      can call ``focus_next`` from a key handler) and the Provider
      Element (so descendants see the manager via context). Returning a
      tuple would force callers to unpack at every call site; a handle
      object with a ``wrap`` method keeps the API compact.
    * The manager is constructed once per mount and reused for every
      ``wrap`` call — multiple Providers in the same subtree all inject
      the same manager, which is the intended semantics.

    Raises
    ------
    RuntimeError
        If called outside a function-component body mounted via
        :func:`pyink.render.render` (the hook binds its cleanup to the
        active :class:`~pyink.core.component.ComponentInstance`).
    """
    if _get_current_instance() is None:
        raise RuntimeError(
            "use_focus_manager() must be called from inside a function "
            "component mounted via pyink.render.render()"
        )
    manager = FocusManager()
    return FocusManagerHandle(manager)


def use_focus(options: FocusOptions | None = None) -> FocusHandle:
    """Subscribe the calling component to the nearest focus manager.

    Parameters
    ----------
    options:
        Optional dict with any of:

        * ``"id"`` (``str``) — stable identifier for ``focus(id=...)``
          jumps. Auto-generated when omitted.
        * ``"auto_focus"`` (``bool``) — when ``True``, focus this handle
          immediately on mount (overriding any prior focus). Only one
          handle per subtree should set this; if multiple do, the last
          mounted wins.
        * ``"is_active"`` (``bool``) — initial value of the handle's
          ``is_active`` flag. ``False`` keeps the handle registered but
          skipped by Tab navigation. Defaults to ``True``.

    Returns
    -------
    FocusHandle
        Carries ``id``, ``is_focused`` (a :class:`~pyink.core.signal.Signal`
        of ``bool``), ``is_active``, and the ``focus_self`` / ``blur``
        methods. Read ``is_focused.value`` inside a callable style prop
        or ``effect`` to subscribe to focus changes.

    Behaviour outside any ``use_focus_manager`` subtree:

    * A shared :class:`NullFocusManager` is returned from context.
    * The handle's ``is_focused`` signal is permanently ``False``.
    * ``focus_self`` / ``blur`` are safe to call but have no effect.

    Raises
    ------
    RuntimeError
        If called outside a function-component body mounted via
        :func:`pyink.render.render`.
    """
    if _get_current_instance() is None:
        raise RuntimeError(
            "use_focus() must be called from inside a function component "
            "mounted via pyink.render.render()"
        )

    opts = options or {}
    handle_id: str = opts.get("id") or _next_handle_id()
    auto_focus: bool = bool(opts.get("auto_focus", False))
    initial_active: bool = bool(opts.get("is_active", True))

    manager = use_context(_FOCUS_MANAGER_CONTEXT)
    handle = FocusHandle(handle_id, manager, is_active=initial_active)
    unregister = manager.register(handle)

    if auto_focus and manager is not _NULL_MANAGER:
        # Defer the focus call to after registration so the manager's
        # ``handles`` list already contains us. Calling here (inside the
        # component body) is safe — manager state mutations are
        # synchronous and the render loop will observe the resulting
        # signal writes on the next paint.
        manager.focus(handle_id)

    # Bind cleanup via a no-op effect so the component's unmount path
    # invokes ``unregister``. Mirrors the pattern used by ``use_input``
    # and ``use_interval``: ``effect`` is the cheapest way to register a
    # dispose with the active ComponentInstance.
    #
    # The cleanup guards against double-invocation with a ``_cleaned``
    # flag: a manual dispose of the effect followed by component unmount
    # would otherwise call ``unregister`` twice. The underlying
    # ``FocusManager._unregister`` is already tolerant of a missing
    # handle, but the flag keeps the contract explicit and avoids
    # surprising a future custom manager that isn't as defensive.
    _cleaned = False

    def _setup() -> Any:
        def cleanup() -> None:
            nonlocal _cleaned
            if _cleaned:
                return
            _cleaned = True
            unregister()

        return cleanup

    effect(_setup)

    return handle
