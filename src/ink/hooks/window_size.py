"""``use_window_size`` — terminal viewport size, reactive to resize.

Returns a :class:`WindowSize` whose ``.columns`` / ``.rows`` properties
re-read the underlying data on every access. A component that captured
the object at mount time therefore observes fresh values after a
resize — the Instance's resize subscription drives a re-paint, which
in turn updates the signal the properties read from.

Mirrors ink's ``useWindowSize`` hook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ink.hooks._runtime import _get_current_instance

if TYPE_CHECKING:
    from ink.render.instance import Instance

__all__ = ["WindowSize", "use_window_size"]


class WindowSize:
    """Terminal viewport size, in character cells.

    ``.columns`` and ``.rows`` are read-only properties — assigning to
    them raises ``AttributeError`` (preserves the legacy frozen-dataclass
    contract). Concrete subclasses decide whether each access reads a
    live signal (inside ``render``) or a snapshot captured at call time
    (outside ``render``).
    """

    __slots__ = ()

    @property
    def columns(self) -> int:
        raise NotImplementedError

    @property
    def rows(self) -> int:
        raise NotImplementedError


class _LiveWindowSize(WindowSize):
    """Reactive proxy — every property access reads the Instance signal.

    Created when ``use_window_size()`` is called inside a mounted
    Instance. The signal is written at the end of every ``_paint_now``
    pass, so a consumer that reads ``.columns`` from a callable ``Text``
    leaf re-evaluates with the post-resize value on the next paint.
    """

    __slots__ = ("_inst",)

    def __init__(self, inst: "Instance") -> None:
        self._inst = inst

    @property
    def columns(self) -> int:
        cols, _rows = self._inst._size_signal.value
        override = _options_int(self._inst, "columns")
        return override if override is not None else cols

    @property
    def rows(self) -> int:
        _cols, rows = self._inst._size_signal.value
        override = _options_int(self._inst, "rows")
        return override if override is not None else rows


class _SnapshotWindowSize(WindowSize):
    """Non-reactive snapshot — used outside of a render context.

    ``use_window_size()`` returns one of these when there is no active
    Instance (e.g. in a test that drives layout directly). The values
    are captured at construction time and never update.
    """

    __slots__ = ("_columns", "_rows")

    def __init__(self, columns: int, rows: int) -> None:
        self._columns = columns
        self._rows = rows

    @property
    def columns(self) -> int:
        return self._columns

    @property
    def rows(self) -> int:
        return self._rows


def _options_int(inst: "Instance", key: str) -> int | None:
    options = getattr(inst, "options", None)
    raw = getattr(options, key, None)
    if isinstance(raw, int) and raw > 0:
        return raw
    return None


def use_window_size() -> WindowSize:
    """Return the current terminal size.

    Inside ``render`` the returned object is reactive — subsequent
    property accesses reflect the latest size, so closures that captured
    it at mount time still see fresh values after a resize. Outside
    ``render`` the returned object is a one-shot snapshot (there is no
    Instance to subscribe to).
    """
    inst = _get_current_instance()
    if inst is None:
        import shutil

        ts = shutil.get_terminal_size()
        return _SnapshotWindowSize(
            columns=max(1, ts.columns),
            rows=max(1, ts.lines),
        )
    return _LiveWindowSize(inst)
