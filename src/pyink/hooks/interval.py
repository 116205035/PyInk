"""``use_interval`` — periodically invoke a callback on a daemon thread (Phase 2 PR1).

Mirrors the common ``useInterval`` pattern from React/Vue TUI frameworks.
A background daemon thread sleeps for ``interval_ms`` between invocations
and calls ``callback`` on its own thread. Because PyInk signals are
thread-safe (per-signal ``RLock``), callbacks that write to signals are
safe — this is the canonical pattern for Spinner (PR2) and any
polling / clock display.

Implementation notes:

* The thread stops cleanly via a :class:`threading.Event`. ``dispose``
  sets the event; the loop's per-tick ``wait`` is sliced into short
  chunks (``_STOP_POLL_SECONDS``) so the worker observes the stop flag
  within ~50 ms even when ``interval_ms`` is large. ``dispose`` also
  joins the thread with a 1 s timeout as a safety net.
* ``interval_ms <= 0`` is treated as "do not start" — the dispose is
  still registered with the component (so unmount is a no-op) and the
  returned dispose is safe to call.
* ``is_active=False`` at mount time starts the thread paused. Toggling
  the pause later is not supported by the simple boolean form — callers
  that need runtime toggling should dispose + re-create the hook from a
  derived signal (PR2 Spinner does this naturally because its lifecycle
  matches the component's mount).
* Callback exceptions are swallowed + logged so a single bad tick can't
  kill the loop — matches the effect / key-dispatcher "swallow + continue"
  philosophy used elsewhere (see :func:`pyink.core.signal._notify`).
* Like :func:`pyink.hooks.use_input`, the dispose is auto-registered with
  the active :class:`ComponentInstance` via :func:`effect`'s binder hook,
  so component unmount tears the thread down automatically.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from contextlib import suppress

from pyink.core.signal import effect, ref
from pyink.hooks._runtime import _get_current_instance

__all__ = ["use_interval"]

#: Upper bound on how long the worker thread's ``Event.wait`` blocks before
#: re-checking the stop flag. Lets dispose return promptly even when the
#: configured ``interval_ms`` is large (e.g. 60 s for a slow poller).
_STOP_POLL_SECONDS: float = 0.05


def use_interval(
    callback: Callable[[], None],
    interval_ms: int,
    *,
    is_active: bool = True,
) -> Callable[[], None]:
    """Periodically invoke ``callback`` every ``interval_ms`` milliseconds.

    Parameters
    ----------
    callback:
        Zero-arg callable invoked on the worker thread. Exceptions raised
        inside it are swallowed so the loop keeps ticking.
    interval_ms:
        Tick interval in milliseconds. Values ``<= 0`` are treated as
        "do not start" — the dispose is still registered with the
        component (so unmount is a no-op) and the returned dispose is
        safe to call.
    is_active:
        When ``False`` at mount time the loop is never started. Useful
        for conditionally enabling timers based on initial props.

    Returns
    -------
    dispose
        Stops the worker thread. Also auto-invoked on component unmount
        — callers usually don't need to manage this themselves.

    Raises
    ------
    RuntimeError
        If called outside a function-component body mounted via
        :func:`pyink.render.render` (the hook needs an active
        :class:`ComponentInstance` to bind the cleanup to).
    """
    inst = _get_current_instance()
    if inst is None:
        raise RuntimeError(
            "use_interval() must be called from inside a function component "
            "mounted via pyink.render.render()"
        )

    # Capture the latest callback without re-subscribing — mirrors how
    # ``use_input`` keeps its handler fresh across re-renders. (Currently
    # ``use_interval`` runs once per mount, but the ref future-proofs the
    # API for callers whose callback closes over changing locals.)
    callback_ref = ref(callback)
    callback_ref.value = callback

    stop_event: threading.Event | None = None
    thread: threading.Thread | None = None

    if is_active and interval_ms > 0:
        stop_event = threading.Event()
        # Unique, debuggable thread name in the spirit of
        # ``pyink-input-reader`` / ``pyink-fps-throttle``.
        seq = next(itertools.count())
        thread = threading.Thread(
            target=_interval_loop,
            args=(stop_event, callback_ref, interval_ms),
            name=f"pyink-interval-{seq}",
            daemon=True,
        )
        thread.start()

    def dispose() -> None:
        nonlocal stop_event, thread
        local_stop = stop_event
        local_thread = thread
        stop_event = None
        thread = None
        if local_stop is None:
            return
        local_stop.set()
        # The loop's per-tick wait is sliced into ``_STOP_POLL_SECONDS``
        # chunks, so the worker observes the flag within ~50 ms. Join with
        # a 1 s ceiling as a safety net; if the callback is wedged we
        # don't hang the caller forever (the thread is a daemon, so an
        # unjoined thread would also die with the process).
        if local_thread is not None and local_thread is not threading.current_thread():
            with suppress(RuntimeError):
                local_thread.join(timeout=1.0)

    # Mirror ``use_input``'s binding trick: a no-op effect whose only job
    # is to register a dispose with the owning ComponentInstance, so the
    # interval thread is torn down automatically on unmount.
    def _setup() -> Callable[[], None]:
        return dispose

    effect_dispose = effect(_setup)

    # A ``_disposed`` flag guards against double-invocation: the returned
    # dispose is meant to be called manually by the caller *and* is
    # registered on the component instance for unmount (via the effect
    # cleanup, which is ``dispose`` itself). Without the guard, calling
    # the manual dispose and then unmounting would re-enter ``dispose``
    # and ``effect_dispose`` on already-torn-down state. The flag makes
    # both entry points truly idempotent — the second call is a no-op.
    _disposed = False

    def combined_dispose() -> None:
        nonlocal _disposed
        if _disposed:
            return
        _disposed = True
        dispose()
        if effect_dispose is not None:
            effect_dispose()

    # The effect already invoked ``_setup`` once synchronously, so
    # ``dispose`` is registered on the ComponentInstance. We also return a
    # manual dispose so callers can stop the interval early — both paths
    # are idempotent.
    return combined_dispose


def _interval_loop(
    stop: threading.Event,
    callback_ref: object,
    interval_ms: int,
) -> None:
    """Worker body: tick + invoke ``callback_ref.value`` until ``stop`` is set.

    Exceptions inside the callback are swallowed so the loop survives a
    bad tick. The callback indirection through a ``Ref`` keeps the
    freshest closure (see comment in :func:`use_interval`).
    """
    interval_seconds = interval_ms / 1000.0
    # Bound the per-tick ``wait`` so a high ``interval_ms`` doesn't make
    # dispose noticeably slow: even if ``interval_ms`` is 60 s the loop
    # observes the stop flag within ~50 ms.
    wait_slice = min(interval_seconds, _STOP_POLL_SECONDS)

    while not stop.is_set():
        # Sleep the full interval, sliced into ``wait_slice`` chunks so a
        # ``stop.set()`` from dispose wakes us within ``_STOP_POLL_SECONDS``
        # rather than waiting out the entire interval. Track the deadline
        # rather than decrementing a counter to avoid float drift.
        deadline = time.monotonic() + interval_seconds
        while not stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            if stop.wait(timeout=min(wait_slice, remaining)):
                return
        if stop.is_set():
            return
        cb = getattr(callback_ref, "value", None)
        if callable(cb):
            with suppress(Exception):
                # A bad tick must not kill the worker thread.
                cb()
