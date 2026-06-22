"""Tests for :func:`use_focus` + :func:`use_focus_manager` (Phase 2 PR6).

Coverage matrix (PR6 PRD):

* Single component ``use_focus`` outside any manager — defaults to
  ``is_focused=False`` (NullFocusManager).
* ``use_focus_manager`` creates a manager visible to descendants.
* Multiple components register in order.
* ``focus_next`` / ``focus_previous`` cycle through active handles.
* ``focus(id)`` jumps to a named handle.
* ``auto_focus=True`` focuses on mount.
* ``enable`` / ``disable`` flips every handle's ``is_focused``.
* Unmount auto-unregisters (next/prev skip it).
* ``is_active=False`` skips a handle but keeps it registered.
* Nested ``use_focus_manager`` — inner overrides outer.
* ``NullFocusManager`` safety: ``focus_self`` / ``blur`` are no-ops.
* Calling hooks outside a component body raises ``RuntimeError``.
* The control handle's ``wrap`` injects the manager via context.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from typing import Any

import pytest

from pyink import Text, create_element, render
from pyink.core.element import Element
from pyink.hooks._focus_runtime import (
    FocusHandle,
    FocusManager,
    FocusManagerHandle,
    NullFocusManager,
)
from pyink.hooks.focus import use_focus, use_focus_manager
from pyink.render.instance import Instance

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 99


def _render_comp(make_component: Callable[[], Element]) -> Instance:
    """Mount ``make_component`` via the live render pipeline and return it."""
    return render(
        create_element(make_component),
        stdout=_FakeTTY(),
        stdin=_FakeTTY(),
        columns=40,
        rows=5,
        exit_on_ctrl_c=False,
    )


def _settle() -> None:
    """Let the render loop paint once before assertions / unmount."""
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# Hook outside component
# ---------------------------------------------------------------------------


def test_use_focus_outside_component_raises() -> None:
    with pytest.raises(RuntimeError, match="use_focus"):
        use_focus()


def test_use_focus_manager_outside_component_raises() -> None:
    with pytest.raises(RuntimeError, match="use_focus_manager"):
        use_focus_manager()


# ---------------------------------------------------------------------------
# Single component without manager
# ---------------------------------------------------------------------------


def test_use_focus_without_manager_is_not_focused() -> None:
    """``use_focus`` outside any ``use_focus_manager`` returns a handle whose
    ``is_focused`` signal is permanently ``False``."""
    captured: dict[str, Any] = {}

    def Comp() -> Element:
        h = use_focus()
        captured["handle"] = h
        return Text("x")

    inst = _render_comp(Comp)
    _settle()
    handle: FocusHandle = captured["handle"]
    assert handle.is_focused.value is False
    # Calling focus_self / blur is safe but no-op.
    handle.focus_self()
    assert handle.is_focused.value is False
    handle.blur()
    assert handle.is_focused.value is False
    inst.unmount()


def test_use_focus_without_manager_has_auto_id() -> None:
    captured: dict[str, Any] = {}

    def Comp() -> Element:
        captured["handle"] = use_focus()
        return Text("x")

    inst = _render_comp(Comp)
    _settle()
    handle: FocusHandle = captured["handle"]
    assert isinstance(handle.id, str)
    assert handle.id.startswith("pyink-focus-")
    inst.unmount()


# ---------------------------------------------------------------------------
# use_focus_manager basics
# ---------------------------------------------------------------------------


def test_use_focus_manager_returns_handle_with_wrap() -> None:
    captured: dict[str, Any] = {}

    def App() -> Element:
        focus = use_focus_manager()
        captured["handle"] = focus
        return focus.wrap(Text("x"))

    inst = _render_comp(App)
    _settle()
    handle: FocusManagerHandle = captured["handle"]
    assert isinstance(handle, FocusManagerHandle)
    assert isinstance(handle.manager, FocusManager)
    assert handle.active_id is None
    inst.unmount()


def test_use_focus_in_manager_subtree_registers() -> None:
    """A focusable component inside ``focus.wrap(...)`` registers its handle."""
    manager_box: dict[str, Any] = {}
    handle_box: dict[str, Any] = {}

    def Focusable() -> Element:
        h = use_focus({"id": "input-1"})
        handle_box["handle"] = h
        return Text("input-1")

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(create_element(Focusable))

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handle: FocusHandle = handle_box["handle"]
    assert len(mgr.handles.value) == 1
    assert mgr.handles.value[0] is handle
    assert handle._manager is mgr
    inst.unmount()


# ---------------------------------------------------------------------------
# Navigation: focus_next / focus_previous
# ---------------------------------------------------------------------------


def test_focus_next_cycles_through_active_handles() -> None:
    manager_box: dict[str, Any] = {}
    handle_box: dict[str, list[FocusHandle]] = {"handles": []}

    def Focusable(label: str, hid: str) -> Element:
        def Impl() -> Element:
            h = use_focus({"id": hid})
            handle_box["handles"].append(h)
            return Text(label)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(
            Focusable("a", "a"),
            Focusable("b", "b"),
            Focusable("c", "c"),
        )

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles: list[FocusHandle] = handle_box["handles"]
    assert len(handles) == 3

    # No focus initially.
    assert mgr.active_id is None

    mgr.focus_next()
    assert mgr.active_id == "a"
    assert handles[0].is_focused.value is True
    assert handles[1].is_focused.value is False

    mgr.focus_next()
    assert mgr.active_id == "b"
    assert handles[0].is_focused.value is False
    assert handles[1].is_focused.value is True

    mgr.focus_next()
    assert mgr.active_id == "c"

    # Wrap around.
    mgr.focus_next()
    assert mgr.active_id == "a"
    inst.unmount()


def test_focus_previous_cycles_in_reverse() -> None:
    manager_box: dict[str, Any] = {}
    handle_box: dict[str, list[FocusHandle]] = {"handles": []}

    def Focusable(label: str, hid: str) -> Element:
        def Impl() -> Element:
            h = use_focus({"id": hid})
            handle_box["handles"].append(h)
            return Text(label)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(
            Focusable("a", "a"),
            Focusable("b", "b"),
            Focusable("c", "c"),
        )

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles = handle_box["handles"]

    # No focus initially → previous wraps to last.
    mgr.focus_previous()
    assert mgr.active_id == "c"
    assert handles[2].is_focused.value is True

    mgr.focus_previous()
    assert mgr.active_id == "b"

    mgr.focus_previous()
    assert mgr.active_id == "a"

    # Wrap around to last.
    mgr.focus_previous()
    assert mgr.active_id == "c"
    inst.unmount()


# ---------------------------------------------------------------------------
# focus(id)
# ---------------------------------------------------------------------------


def test_focus_by_id_jumps_to_named_handle() -> None:
    manager_box: dict[str, Any] = {}

    def Focusable(hid: str) -> Element:
        def Impl() -> Element:
            use_focus({"id": hid})
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(Focusable("a"), Focusable("b"), Focusable("c"))

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles = mgr.handles.value

    mgr.focus("b")
    assert mgr.active_id == "b"
    assert handles[1].is_focused.value is True
    assert handles[0].is_focused.value is False

    mgr.focus("c")
    assert mgr.active_id == "c"
    assert handles[2].is_focused.value is True
    assert handles[1].is_focused.value is False

    # Unknown id is tolerated — focus stays on c.
    mgr.focus("zzz")
    assert mgr.active_id == "c"
    inst.unmount()


# ---------------------------------------------------------------------------
# auto_focus
# ---------------------------------------------------------------------------


def test_auto_focus_focuses_first_mounted_with_flag() -> None:
    manager_box: dict[str, Any] = {}

    def Focusable(hid: str, auto: bool = False) -> Element:
        def Impl() -> Element:
            use_focus({"id": hid, "auto_focus": auto})
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(
            Focusable("a"),
            Focusable("b", auto=True),
            Focusable("c"),
        )

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles = mgr.handles.value

    assert mgr.active_id == "b"
    assert handles[1].is_focused.value is True
    assert handles[0].is_focused.value is False
    assert handles[2].is_focused.value is False
    inst.unmount()


def test_auto_focus_outside_manager_is_noop() -> None:
    """``auto_focus=True`` without a manager doesn't crash and doesn't focus."""
    captured: dict[str, Any] = {}

    def Comp() -> Element:
        h = use_focus({"auto_focus": True})
        captured["handle"] = h
        return Text("x")

    inst = _render_comp(Comp)
    _settle()
    handle: FocusHandle = captured["handle"]
    assert handle.is_focused.value is False
    inst.unmount()


# ---------------------------------------------------------------------------
# enable / disable
# ---------------------------------------------------------------------------


def test_disable_clears_is_focused() -> None:
    manager_box: dict[str, Any] = {}

    def Focusable(hid: str) -> Element:
        def Impl() -> Element:
            use_focus({"id": hid})
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(Focusable("a"), Focusable("b"))

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles = mgr.handles.value

    mgr.focus("a")
    assert handles[0].is_focused.value is True

    mgr.disable()
    assert handles[0].is_focused.value is False
    assert handles[1].is_focused.value is False
    assert mgr.active_id is None
    assert mgr.enabled.value is False

    # Navigation is suppressed while disabled.
    mgr.focus_next()
    assert mgr.active_id is None

    mgr.enable()
    # Restores the previously active handle.
    assert mgr.active_id == "a"
    assert handles[0].is_focused.value is True
    inst.unmount()


def test_enable_without_prior_focus_does_not_focus() -> None:
    manager_box: dict[str, Any] = {}

    def Focusable(hid: str) -> Element:
        def Impl() -> Element:
            use_focus({"id": hid})
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(Focusable("a"))

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles = mgr.handles.value

    # Disable without ever focusing — enable should not invent a focus.
    mgr.disable()
    mgr.enable()
    assert mgr.active_id is None
    assert handles[0].is_focused.value is False
    inst.unmount()


# ---------------------------------------------------------------------------
# Unmount auto-unregister
# ---------------------------------------------------------------------------


def test_unmount_auto_unregisters_handle() -> None:
    """Unmounting the focusable component removes it from the registry."""
    manager_box: dict[str, Any] = {}
    show_b: dict[str, bool] = {"value": True}

    def Focusable(hid: str) -> Element:
        def Impl() -> Element:
            use_focus({"id": hid})
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        children = [Focusable("a")]
        if show_b["value"]:
            children.append(Focusable("b"))
        return focus.wrap(*children)

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    assert len(mgr.handles.value) == 2

    # Re-render without b — but our reconciler doesn't diff in place, so
    # we instead unmount the whole tree and re-mount a smaller tree.
    inst.unmount()
    show_b["value"] = False
    inst2 = _render_comp(App)
    _settle()
    mgr2: FocusManager = manager_box["mgr"]
    assert len(mgr2.handles.value) == 1
    assert mgr2.handles.value[0].id == "a"
    inst2.unmount()


def test_use_focus_remount_does_not_leak_original_handle() -> None:
    """When a focusable component unmounts and a fresh component re-uses
    the same focus id, the new handle must be the one that receives
    focus — not a stale handle retained from the first mount.

    Regression guard for the double-dispose / cleanup-overwrite class of
    bugs: if the first mount's cleanup failed to unregister (or the
    second mount's register returned a no-op while the first handle
    lingered), ``focus_next`` would either skip the new handle entirely
    or land on a detached handle whose ``is_focused`` signal no longer
    drives any render.
    """
    manager_box: dict[str, Any] = {}
    handle_box: dict[str, Any] = {}

    def Focusable(hid: str) -> Element:
        def Impl() -> Element:
            h = use_focus({"id": hid})
            handle_box[hid] = h
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(Focusable("x"))

    # First mount: registers handle "x" (instance 1).
    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    first_handle: FocusHandle = handle_box["x"]
    assert len(mgr.handles.value) == 1
    assert mgr.handles.value[0] is first_handle

    # Focus it to prove the signal works on the original handle.
    mgr.focus("x")
    assert mgr.active_id == "x"
    assert first_handle.is_focused.value is True

    # Unmount; cleanup must remove the handle.
    inst.unmount()
    _settle()
    assert len(mgr.handles.value) == 0

    # Remount a fresh tree — a brand-new FocusHandle with the same id.
    inst2 = _render_comp(App)
    _settle()
    mgr2: FocusManager = manager_box["mgr"]
    second_handle: FocusHandle = handle_box["x"]
    assert second_handle is not first_handle, "expected a fresh FocusHandle on remount"
    assert len(mgr2.handles.value) == 1
    assert mgr2.handles.value[0] is second_handle

    # The new handle must be reachable via focus navigation.
    mgr2.focus_next()
    assert mgr2.active_id == "x"
    assert second_handle.is_focused.value is True
    # The stale handle must NOT have been touched by the second mount's
    # focus call (its signal is detached from any live render).
    assert first_handle.is_focused.value is False
    inst2.unmount()


def test_focus_handle_unmount_via_subtree_replacement() -> None:
    """When the active handle is removed, focus clears (no implicit jump)."""
    # We exercise this by directly manipulating the runtime objects —
    # a full subtree-replacement mount is heavier than needed to verify
    # the unregister bookkeeping.
    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    h2 = FocusHandle("b", mgr)
    unreg1 = mgr.register(h1)
    mgr.register(h2)
    mgr.focus("a")
    assert mgr.active_id == "a"
    assert h1.is_focused.value is True

    # Unregister the active handle.
    unreg1()
    assert mgr.active_id is None
    assert h1.is_focused.value is False
    assert len(mgr.handles.value) == 1


# ---------------------------------------------------------------------------
# is_active flag
# ---------------------------------------------------------------------------


def test_is_active_false_skips_handle_in_cycle() -> None:
    manager_box: dict[str, Any] = {}

    def Focusable(hid: str, active: bool = True) -> Element:
        def Impl() -> Element:
            h = use_focus({"id": hid, "is_active": active})
            return Text(lambda: f"{hid}:{'F' if h.is_focused.value else '-'}")

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(
            Focusable("a"),
            Focusable("b", active=False),
            Focusable("c"),
        )

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    handles = mgr.handles.value

    # All three are registered.
    assert len(handles) == 3
    # But the cycle skips b.
    mgr.focus_next()
    assert mgr.active_id == "a"
    mgr.focus_next()
    assert mgr.active_id == "c"
    mgr.focus_next()
    assert mgr.active_id == "a"
    # b was never focused.
    assert handles[1].is_focused.value is False
    inst.unmount()


def test_focus_id_still_works_on_inactive_handle() -> None:
    """``focus(id)`` jumps to an inactive handle even though the Tab cycle
    would skip it. This matches ink's behaviour — explicit jumps honour the
    caller's intent."""
    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    h2 = FocusHandle("b", mgr, is_active=False)
    mgr.register(h1)
    mgr.register(h2)
    mgr.focus("b")
    assert mgr.active_id == "b"
    assert h2.is_focused.value is True


# ---------------------------------------------------------------------------
# Nested use_focus_manager (inner overrides outer)
# ---------------------------------------------------------------------------


def test_nested_managers_inner_overrides_outer() -> None:
    """A focusable inside the inner manager subtree does NOT see the outer one."""
    outer_box: dict[str, Any] = {}
    inner_box: dict[str, Any] = {}
    inner_handle_box: dict[str, Any] = {}

    def InnerFocusable() -> Element:
        def Impl() -> Element:
            h = use_focus({"id": "inner-1"})
            inner_handle_box["handle"] = h
            return Text("inner-1")

        return create_element(Impl)

    def Inner() -> Element:
        focus = use_focus_manager()
        inner_box["mgr"] = focus.manager
        return focus.wrap(create_element(InnerFocusable))

    def OuterFocusable() -> Element:
        def Impl() -> Element:
            use_focus({"id": "outer-1"})
            return Text("outer-1")

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        outer_box["mgr"] = focus.manager
        return focus.wrap(
            create_element(OuterFocusable),
            create_element(Inner),
        )

    inst = _render_comp(App)
    _settle()
    outer_mgr: FocusManager = outer_box["mgr"]
    inner_mgr: FocusManager = inner_box["mgr"]
    inner_handle: FocusHandle = inner_handle_box["handle"]

    # Inner handle registered with inner manager only.
    assert inner_handle._manager is inner_mgr
    assert inner_handle not in outer_mgr.handles.value
    assert len(inner_mgr.handles.value) == 1
    assert len(outer_mgr.handles.value) == 1  # only OuterFocusable
    inst.unmount()


# ---------------------------------------------------------------------------
# FocusManagerHandle.wrap injects the manager via context
# ---------------------------------------------------------------------------


def test_wrap_injects_manager_visible_to_use_context() -> None:
    """Sanity: the Provider built by ``wrap`` is the one ``use_focus`` reads."""
    seen_managers: list[int] = []

    def Focusable() -> Element:
        def Impl() -> Element:
            h = use_focus()
            seen_managers.append(id(h._manager) if h._manager is not None else 0)
            return Text("x")

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        return focus.wrap(create_element(Focusable))

    inst = _render_comp(App)
    _settle()
    inst.unmount()
    # Exactly one focusable mounted; it saw a real manager (not the shared
    # NullFocusManager). We can't compare by ``is`` here because the
    # manager was created inside the component — instead we just confirm
    # the call succeeded and produced a non-zero id.
    assert len(seen_managers) == 1
    assert seen_managers[0] != 0


def test_wrap_supports_multiple_children_and_fragments() -> None:
    """``wrap`` flattens tuples/lists like any other element factory."""
    manager_box: dict[str, Any] = {}

    def Focusable(hid: str) -> Element:
        def Impl() -> Element:
            use_focus({"id": hid})
            return Text(hid)

        return create_element(Impl)

    def App() -> Element:
        focus = use_focus_manager()
        manager_box["mgr"] = focus.manager
        return focus.wrap(
            Focusable("a"),
            [Focusable("b"), Focusable("c")],  # fragment
        )

    inst = _render_comp(App)
    _settle()
    mgr: FocusManager = manager_box["mgr"]
    assert {h.id for h in mgr.handles.value} == {"a", "b", "c"}
    inst.unmount()


# ---------------------------------------------------------------------------
# NullFocusManager explicit unit tests
# ---------------------------------------------------------------------------


def test_null_focus_manager_all_methods_are_noops() -> None:
    mgr = NullFocusManager()
    h = FocusHandle("x", mgr)
    # Register returns a callable; calling it is safe.
    unreg = mgr.register(h)
    mgr.focus_next()
    mgr.focus_previous()
    mgr.focus("x")
    mgr.disable()
    mgr.enable()
    assert mgr.active_id is None
    assert h.is_focused.value is False
    unreg()  # should not raise


def test_null_focus_manager_default_is_shared() -> None:
    """The default context value is the same shared NullFocusManager instance."""
    from pyink.hooks.focus import _FOCUS_MANAGER_CONTEXT

    assert isinstance(_FOCUS_MANAGER_CONTEXT.default, NullFocusManager)


# ---------------------------------------------------------------------------
# FocusManagerHandle control API forwards to manager
# ---------------------------------------------------------------------------


def test_focus_manager_handle_forwards_calls() -> None:
    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    h2 = FocusHandle("b", mgr)
    mgr.register(h1)
    mgr.register(h2)
    handle = FocusManagerHandle(mgr)

    handle.focus_next()
    assert handle.active_id == "a"
    handle.focus_next()
    assert handle.active_id == "b"
    handle.focus_previous()
    assert handle.active_id == "a"
    handle.focus("b")
    assert handle.active_id == "b"
    handle.disable()
    assert handle.enabled is False
    assert handle.active_id is None
    handle.enable()
    assert handle.enabled is True
    assert handle.active_id == "b"


# ---------------------------------------------------------------------------
# FocusHandle.focus_self / blur
# ---------------------------------------------------------------------------


def test_focus_handle_focus_self_blur_round_trip() -> None:
    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    h2 = FocusHandle("b", mgr)
    mgr.register(h1)
    mgr.register(h2)

    h1.focus_self()
    assert mgr.active_id == "a"
    assert h1.is_focused.value is True

    # blur moves to the next active handle (b).
    h1.blur()
    assert mgr.active_id == "b"
    assert h1.is_focused.value is False
    assert h2.is_focused.value is True


def test_focus_handle_blur_when_not_focused_is_noop() -> None:
    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    h2 = FocusHandle("b", mgr)
    mgr.register(h1)
    mgr.register(h2)
    mgr.focus("a")
    # b is not focused — blur on b is a no-op.
    h2.blur()
    assert mgr.active_id == "a"


def test_focus_handle_blur_only_active_handle_clears_focus() -> None:
    """Blurring the only registered handle leaves no active focus."""
    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    mgr.register(h1)
    mgr.focus("a")
    h1.blur()
    # No other handles to jump to — focus is cleared.
    assert mgr.active_id is None
    assert h1.is_focused.value is False


# ---------------------------------------------------------------------------
# Reactivity: writing is_focused via the manager triggers signal subscribers
# ---------------------------------------------------------------------------


def test_is_focused_signal_notifies_subscribers() -> None:
    """Reading ``is_focused.value`` inside an effect subscribes to changes."""
    from pyink import effect

    mgr = FocusManager()
    h1 = FocusHandle("a", mgr)
    h2 = FocusHandle("b", mgr)
    mgr.register(h1)
    mgr.register(h2)

    seen: list[bool] = []

    def _track() -> None:
        seen.append(h1.is_focused.value)

    dispose = effect(_track)
    # Initial read captured False.
    assert seen == [False]
    mgr.focus("a")
    assert seen == [False, True]
    mgr.focus("b")
    assert seen == [False, True, False]
    dispose()
