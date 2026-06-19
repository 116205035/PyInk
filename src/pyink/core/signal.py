"""Reactive signals core for PyInk.

Implements a SolidJS / Vue 3 / Preact-Signals style reactive system.

Public API (see PRD Decision 6 — ``.value`` attribute style):

* :func:`signal` — observable writable value.
* :func:`computed` — lazily-evaluated derived value with dependency tracking.
* :func:`effect` — side-effect that re-runs when dependencies change.
* :func:`ref` — non-reactive mutable reference.
* :func:`batch` — coalesce multiple writes into one notification.

Design constraints:

* Pure sync + threads (no asyncio).
* Thread safety via ``threading.RLock``.
* Dependency tracking via ``contextvars.ContextVar``.
* Circular ``computed`` raises :class:`CyclicDependency`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from contextlib import suppress
from contextvars import ContextVar
from typing import Generic, Protocol, TypeVar, runtime_checkable

__all__ = [
    "CyclicDependency",
    "Computed",
    "Dispose",
    "Effect",
    "Ref",
    "Signal",
    "batch",
    "computed",
    "effect",
    "ref",
    "signal",
]

T = TypeVar("T")

#: Type alias for an effect dispose function.
Dispose = Callable[[], None]


class CyclicDependency(RuntimeError):
    """Raised when a ``computed`` forms a cycle through itself."""


# ---------------------------------------------------------------------------
# Tracking primitives (contextvars)
# ---------------------------------------------------------------------------

#: Innermost observer trigger callback. Set when reading a Signal/Computed
#: inside an ``effect`` body or a ``computed`` body.
_current_observer: ContextVar[_Observer | None] = ContextVar(
    "pyink_current_observer", default=None
)

#: Non-zero when inside one or more ``batch(...)`` calls.
_batch_depth: ContextVar[int] = ContextVar("pyink_batch_depth", default=0)

#: Non-zero while a notification flush is in progress (top-level or batch).
#: Effects consult this together with ``_notification_epoch`` to collapse
#: redundant triggers within the same flush.
_notifying: ContextVar[bool] = ContextVar("pyink_notifying", default=False)

#: Monotonic counter incremented at the start of each top-level notification
#: flush. Lets effects dedupe re-runs triggered by multiple paths within the
#: same flush (e.g. an effect reading both a Signal and a Computed derived
#: from that Signal).
_notification_epoch: int = 0
_epoch_lock = threading.Lock()


class _Observer:
    """Lightweight observer record.

    Each effect / computed has its own ``_Observer`` instance. When a Signal
    or Computed is read while ``_current_observer`` points at it, the read
    target registers a (target, trigger) pair on the observer so the observer
    can unsubscribe later.
    """

    __slots__ = ("trigger", "sources")

    def __init__(self, trigger: Callable[[], None]) -> None:
        self.trigger = trigger
        self.sources: list[tuple[_Trackable, Callable[[], None]]] = []


@runtime_checkable
class _Trackable(Protocol):
    """Anything that exposes a subscriber set + unsubscribe."""

    _subscribers: set[Callable[[], None]]

    def _unsubscribe(self, callback: Callable[[], None]) -> None: ...


def _track(
    owner: _Trackable,
    subscribers: set[Callable[[], None]],
) -> None:
    """Subscribe the current observer to ``owner`` and record the dependency."""
    observer = _current_observer.get()
    if observer is None:
        return
    if observer.trigger not in subscribers:
        subscribers.add(observer.trigger)
        observer.sources.append((owner, observer.trigger))


def _next_epoch() -> int:
    global _notification_epoch
    with _epoch_lock:
        _notification_epoch += 1
        return _notification_epoch


def _notify(subscribers: set[Callable[[], None]]) -> None:
    """Fire every subscriber. Snapshot first so callbacks may mutate the set.

    A notification epoch is bumped when entering a top-level flush so that
    effects can collapse redundant triggers (e.g. when they read both a
    Signal and a Computed derived from that Signal).
    """
    if _batch_depth.get() > 0:
        _schedule_batched(subscribers)
        return
    if _notifying.get():
        # Already inside a flush — reuse the current epoch so nested
        # notifications (e.g. a Computed notifying its own subscribers
        # while we are still iterating the source's subscribers) dedupe.
        for callback in list(subscribers):
            with suppress(Exception):
                # A subscriber raising should not break the rest of the graph.
                callback()
        return
    _next_epoch()  # advance epoch so effects dedupe within this flush
    token = _notifying.set(True)
    try:
        for callback in list(subscribers):
            with suppress(Exception):
                # A subscriber raising should not break the rest of the graph.
                callback()
    finally:
        _notifying.reset(token)


# ---------------------------------------------------------------------------
# Batch queue
# ---------------------------------------------------------------------------

#: Each entry is a snapshot of a signal's subscriber set taken at write time.
_batch_queue: list[set[Callable[[], None]]] = []
_batch_lock = threading.Lock()


def _schedule_batched(subscribers: set[Callable[[], None]]) -> None:
    # _batch_queue is only mutated, not reassigned, so no global declaration
    # is needed here.
    with _batch_lock:
        _batch_queue.append(subscribers)


def _flush_batched() -> None:
    global _batch_queue
    with _batch_lock:
        queue = _batch_queue
        _batch_queue = []
    # Collect unique callbacks (by identity) across all queued snapshots so
    # each subscriber fires at most once per batch flush.
    callbacks: dict[int, Callable[[], None]] = {}
    for subs in queue:
        for callback in subs:
            callbacks.setdefault(id(callback), callback)
    _next_epoch()  # advance epoch so effects dedupe within this flush
    token = _notifying.set(True)
    try:
        for callback in callbacks.values():
            with suppress(Exception):
                callback()
    finally:
        _notifying.reset(token)


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------


class Signal(Generic[T]):
    """An observable, writable value.

    Reading ``.value`` from inside an ``effect`` or ``computed`` body
    automatically subscribes the caller to subsequent changes.
    """

    __slots__ = ("_value", "_subscribers", "_lock", "__weakref__")

    def __init__(self, initial: T) -> None:
        self._value: T = initial
        self._subscribers: set[Callable[[], None]] = set()
        self._lock = threading.RLock()

    @property
    def value(self) -> T:
        with self._lock:
            _track(self, self._subscribers)
            return self._value

    @value.setter
    def value(self, new: T) -> None:
        with self._lock:
            if new is self._value:
                return
            try:
                if new == self._value:
                    self._value = new
                    return
            except Exception:
                # Unorderable / uncomparable types — treat as changed.
                pass
            self._value = new
            subscribers = set(self._subscribers)
        _notify(subscribers)

    def _unsubscribe(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._subscribers.discard(callback)


def signal(initial: T) -> Signal[T]:
    """Create a new writable :class:`Signal`."""
    return Signal(initial)


# ---------------------------------------------------------------------------
# Computed
# ---------------------------------------------------------------------------


class Computed(Generic[T]):
    """Lazily-evaluated, dependency-tracked derived value.

    The wrapped function runs on the first ``.value`` read and is cached
    until one of the signals it read changes. While unread, the function
    never executes.
    """

    __slots__ = (
        "_fn",
        "_value",
        "_has_value",
        "_dirty",
        "_lock",
        "_subscribers",
        "_observer",
        "_computing",
    )

    def __init__(self, fn: Callable[[], T]) -> None:
        self._fn = fn
        self._value: T = None  # type: ignore[assignment]
        self._has_value = False
        self._dirty = True
        self._lock = threading.RLock()
        self._subscribers: set[Callable[[], None]] = set()
        self._observer: _Observer | None = None
        self._computing = False

    @property
    def value(self) -> T:
        with self._lock:
            _track(self, self._subscribers)
            if self._dirty:
                if self._computing:
                    raise CyclicDependency(
                        "computed function created a cycle through itself"
                    )
                self._recompute_locked()
            return self._value

    def _on_source_changed(self) -> None:
        with self._lock:
            if not self._dirty:
                # Mark dirty and recompute eagerly so we can compare the new
                # value against the cached one. If they are equal, downstream
                # subscribers do not need to be notified (Decision 11.4 /
                # SolidJS-style ``equals`` semantics). This also prevents
                # downstream effects that read both a Signal and a Computed
                # derived from it from firing twice on a single write.
                self._dirty = True
                prev_value = self._value
                self._recompute_locked()
                try:
                    unchanged = self._has_value and self._value == prev_value
                except Exception:
                    unchanged = False
                if unchanged:
                    return  # value unchanged — swallow notification
                subscribers = set(self._subscribers)
            else:
                subscribers = set()
        _notify(subscribers)

    def _recompute_locked(self) -> None:
        # Reset observer for a fresh tracking pass.
        observer = _Observer(self._on_source_changed)
        self._observer = observer

        token = _current_observer.set(observer)
        self._computing = True
        try:
            new_value = self._fn()
        finally:
            self._computing = False
            _current_observer.reset(token)

        # ``observer.sources`` was populated by every Signal/Computed read
        # during ``self._fn()``. We don't need to store it separately — the
        # signals already have ``self._on_source_changed`` registered as a
        # subscriber, which is what gets called on write.
        self._value = new_value
        self._has_value = True
        self._dirty = False

    def _unsubscribe(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._subscribers.discard(callback)


def computed(fn: Callable[[], T]) -> Computed[T]:
    """Create a lazily-evaluated :class:`Computed`."""
    return Computed(fn)


# ---------------------------------------------------------------------------
# Effect
# ---------------------------------------------------------------------------


class Effect:
    """Reactive side-effect.

    ``deps`` controls re-run behavior:

    * ``None`` — auto-track every signal read inside ``fn``.
    * ``[]`` — mount effect, run exactly once.
    * ``[a, b, ...]`` — re-run only when any element changes (``!=``).
    """

    __slots__ = (
        "_fn",
        "_deps",
        "_prev_deps",
        "_cleanup",
        "_observer",
        "_disposed",
        "_lock",
        "_trigger",
        "_last_epoch",
    )

    def __init__(
        self,
        fn: Callable[[], Callable[[], None] | None],
        deps: Iterable[object] | None = None,
    ) -> None:
        self._fn = fn
        self._deps: tuple[object, ...] | None = None if deps is None else tuple(deps)
        self._prev_deps: tuple[object, ...] | None = None
        self._cleanup: Callable[[], None] | None = None
        self._observer: _Observer | None = None
        self._disposed = False
        self._lock = threading.RLock()
        self._trigger = self._on_dependency_changed
        # Epoch of the last notification that actually caused a re-run. Used
        # to collapse redundant triggers inside the same notification flush.
        self._last_epoch: int = 0
        # Mount.
        self._run()

    def _resolve_deps_snapshot(self) -> tuple[object, ...]:
        """Read current dep values. Signals/Computeds are read via ``.value``.

        Elements that aren't reactive (plain values) are returned as-is.
        """
        if self._deps is None:
            return ()
        out: list[object] = []
        for dep in self._deps:
            if isinstance(dep, (Signal, Computed)):
                out.append(dep.value)
            else:
                out.append(dep)
        return tuple(out)

    def _run(self) -> None:
        with self._lock:
            if self._disposed:
                return
            # Explicit-deps mode: snapshot current dep values. If they match
            # the previous run, skip but re-subscribe so future changes still
            # notify us.
            if self._deps is not None and self._prev_deps is not None:
                current_snapshot = self._resolve_deps_snapshot()
                if _deps_equal(self._prev_deps, current_snapshot):
                    self._refresh_dep_subscriptions_locked()
                    return

            cleanup = self._cleanup
            self._cleanup = None

        if cleanup is not None:
            with suppress(Exception):
                cleanup()

        with self._lock:
            if self._disposed:
                return
            prev = self._observer
            self._observer = None
        if prev is not None:
            for src, trigger in prev.sources:
                src._unsubscribe(trigger)

        with self._lock:
            if self._disposed:
                return
            observer = _Observer(self._trigger)
            self._observer = observer
            token = _current_observer.set(observer)
            # Subscribe to explicit deps (auto-tracking also happens via
            # any Signal/Computed read inside ``fn``).
            if self._deps is not None:
                self._subscribe_to_deps_locked()
            try:
                new_cleanup = self._fn()
            finally:
                _current_observer.reset(token)
            self._cleanup = new_cleanup if callable(new_cleanup) else None
            self._prev_deps = self._resolve_deps_snapshot()

    def _refresh_dep_subscriptions_locked(self) -> None:
        """Re-subscribe to deps without re-running fn. Must hold self._lock."""
        if self._deps is None or self._observer is None:
            return
        token = _current_observer.set(self._observer)
        try:
            self._subscribe_to_deps_locked()
        finally:
            _current_observer.reset(token)

    def _subscribe_to_deps_locked(self) -> None:
        """Subscribe the current observer to every Signal/Computed dep.

        Caller must hold ``self._lock`` and have a current observer set.
        """
        if self._deps is None:
            return
        observer = self._observer
        if observer is None:
            return
        for dep in self._deps:
            if isinstance(dep, (Signal, Computed)):
                _track(dep, dep._subscribers)

    def _on_dependency_changed(self) -> None:
        # Collapse redundant triggers within a single notification flush. If
        # two paths would notify this effect during the same flush (e.g. an
        # effect that reads both a Signal and a Computed derived from it),
        # only the first triggers a re-run; subsequent notifications within
        # the same epoch are no-ops.
        global _notification_epoch
        if _notifying.get():
            epoch = _notification_epoch
            if epoch == self._last_epoch:
                return
            self._last_epoch = epoch
        # Always re-evaluate; deps comparison inside _run decides whether
        # to actually re-run the fn body.
        self._run()

    def dispose(self) -> None:
        with self._lock:
            if self._disposed:
                return
            self._disposed = True
            prev = self._observer
            self._observer = None
            cleanup = self._cleanup
            self._cleanup = None
        if prev is not None:
            for src, trigger in prev.sources:
                src._unsubscribe(trigger)
        if cleanup is not None:
            with suppress(Exception):
                cleanup()


def _deps_equal(prev: tuple[object, ...], current: tuple[object, ...]) -> bool:
    """Element-wise ``==`` comparison, returning False on length mismatch."""
    if len(prev) != len(current):
        return False
    for a, b in zip(prev, current, strict=True):
        try:
            if a != b:
                return False
        except Exception:
            return False
    return True


def effect(
    fn: Callable[[], Callable[[], None] | None],
    deps: Iterable[object] | None = None,
) -> Callable[[], None]:
    """Register a reactive side-effect; returns a dispose callable."""
    fx = Effect(fn, deps)

    def dispose() -> None:
        fx.dispose()

    return dispose


# ---------------------------------------------------------------------------
# Ref
# ---------------------------------------------------------------------------


class Ref(Generic[T]):
    """Non-reactive mutable reference holder.

    Use ``ref`` to keep a stable mutable handle (timer handles, raw-mode
    flags, etc.) across re-renders without participating in reactivity.
    Reading ``ref.value`` from inside an ``effect`` does *not* subscribe.
    """

    __slots__ = ("_value",)

    def __init__(self, initial: T) -> None:
        self._value: T = initial

    @property
    def value(self) -> T:
        return self._value

    @value.setter
    def value(self, new: T) -> None:
        self._value = new


def ref(initial: T) -> Ref[T]:
    """Create a non-reactive :class:`Ref`."""
    return Ref(initial)


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


def batch(fn: Callable[[], T]) -> T:
    """Run ``fn`` coalescing notifications until ``batch`` returns.

    Nested ``batch`` calls only flush when the outermost one exits. The
    return value of ``fn`` is forwarded.
    """
    token = _batch_depth.set(_batch_depth.get() + 1)
    try:
        return fn()
    finally:
        new_depth = _batch_depth.get() - 1
        _batch_depth.reset(token)
        if new_depth <= 0:
            _flush_batched()
        else:
            _batch_depth.set(new_depth)
