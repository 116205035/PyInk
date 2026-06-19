"""Stubs for :mod:`pyink.components.box` (PR4 — Decision 8)."""

from typing import Any, TypeVar

from pyink.core.element import Element

_T = TypeVar("_T")


def Box(*children: Any, **props: Any) -> Element: ...
