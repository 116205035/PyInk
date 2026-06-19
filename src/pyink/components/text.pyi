"""Stubs for :mod:`pyink.components.text` (PR4 — Decision 8)."""

from typing import Any, TypeVar

from pyink.core.element import Element

_T = TypeVar("_T")


def Text(*children: Any, **props: Any) -> Element: ...
