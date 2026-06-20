"""StructuredDiff example — file-edit diff display (Phase 3 PR6).

Reference: Claude Code's ``<StructuredDiff>`` component. Two snapshots
of a Python module are diffed via ``difflib.unified_diff`` and rendered
as a column of coloured rows — green for additions, red for deletions,
magenta for hunk headers, default colour for context lines. When
Pygments is installed, the ``+`` / ``-`` row bodies additionally get
syntax highlighting.

The demo mounts three variants side by side:

* the default (``context_lines=3``) with the language set to Python so
  Pygments colours the bodies,
* a zero-context variant (``context_lines=0``) that shows only the
  changed lines,
* a "no Pygments" simulation rendered with ``language="text"`` so the
  fallback path is visible even when Pygments is installed.

Optional dependency: per-line syntax highlighting requires Pygments.
Install with::

    pip install pyink[highlight]

Without Pygments installed, the diff machinery still works — the
``+`` / ``-`` rows just render as plain coloured ``Text``.

Run::

    python examples/diff/diff_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import StructuredDiff
from pyink.render.keys import Key

#: The "before" snapshot — a small Python module.
BEFORE: str = """\
def greet(name):
    print("Hello, " + name)


def main():
    greet("world")


if __name__ == "__main__":
    main()
"""

#: The "after" snapshot — adds a default value, a docstring, and
#: switches to an f-string. The diff touches several lines so the
#: colour mapping is obvious.
AFTER: str = """\
def greet(name: str = "world") -> str:
    \"\"\"Return a friendly greeting.\"\"\"
    return f"Hello, {name}"


def main() -> None:
    message = greet()
    print(message)


if __name__ == "__main__":
    main()
"""


def DiffDemo() -> Element:
    """Build the demo tree.

    Hooks run inside the Impl closure so they execute during mount.
    """

    def Impl() -> Element:
        app = use_app()

        def on_key(key: Key) -> None:
            if key.escape:
                app.exit(None)

        use_input(on_key)

        return Box(
            Text("StructuredDiff demo", bold=True),
            Text(
                "pip install pyink[highlight] for syntax-highlighted diff "
                "— Esc / Ctrl+C to quit",
                dimColor=True,
            ),
            Text("Default (context_lines=3, language=python):", dimColor=True),
            Box(
                StructuredDiff(BEFORE, AFTER, language="python"),
                padding=0,
            ),
            Text("No context (context_lines=0):", dimColor=True),
            Box(
                StructuredDiff(
                    BEFORE, AFTER, language="python", context_lines=0
                ),
                padding=0,
            ),
            Text("Plain text fallback (language=text):", dimColor=True),
            Box(
                StructuredDiff(BEFORE, AFTER, language="text"),
                padding=0,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(DiffDemo(), columns=72, rows=40)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
