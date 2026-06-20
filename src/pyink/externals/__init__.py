"""PyInk extension components.

Externals are opt-in: users do ``from pyink.externals import Spinner``
rather than importing them from the top-level package (PRD Decision 5 —
externals carry heavier dependencies / non-essential surface area and
stay out of the default namespace).
"""

from pyink.externals.spinner import SPINNERS, Spinner

__all__ = [
    "SPINNERS",
    "Spinner",
]
