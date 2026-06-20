"""HighlightedCode example — Pygments-driven syntax highlighting (Phase 3 PR6).

Reference: ``ink-syntax-highlight``'s gallery. ``HighlightedCode``
tokenises a code string via Pygments and emits a column of ``Text``
rows, each coloured per the token type. This demo mounts four code
blocks (Python / JavaScript / SQL / JSON) so the colour mapping is
visible side by side, plus a ``line_numbers=True`` variant and a
``theme`` override variant that demonstrates how callers can recolour
individual Pygments token classes.

Optional dependency: requires Pygments. Install with::

    pip install pyink[highlight]

Without Pygments installed, ``HighlightedCode`` raises an ``ImportError``
with the same hint on first call.

Run::

    python examples/highlighted-code/highlighted_code_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import HighlightedCode
from pyink.render.keys import Key

#: A short Python snippet covering most token families (keywords,
#: builtins, function names, strings, numbers, comments, decorators).
PYTHON_SNIPPET: str = """\
@dataclass
class Greeter:
    name: str = "world"

    def hello(self, times: int = 1) -> None:
        # Greet ``times`` times.
        for i in range(times):
            print(f"Hello, {self.name}! (#{i + 1})")
"""

#: A JavaScript snippet. Picked for ``const`` / arrow functions /
#: template literals so the lexer has plenty to colour.
JS_SNIPPET: str = """\
const fib = (n) => {
  if (n < 2) return n;
  return fib(n - 1) + fib(n - 2);
};
console.log(`fib(10) = ${fib(10)}`);
"""

#: A SQL snippet — keywords + string literals + numbers.
SQL_SNIPPET: str = """\
SELECT id, name, created_at
FROM users
WHERE active = TRUE AND age >= 18
ORDER BY created_at DESC
LIMIT 10;
"""

#: A JSON snippet — keys, string values, numbers, booleans, null.
JSON_SNIPPET: str = """\
{
  "name": "pyink",
  "version": "0.1.0",
  "tags": ["tui", "signals"],
  "active": true,
  "stars": null
}
"""

#: Custom theme for the override demo. Only the ``Keyword`` colour is
#: changed (to ``cyan``) so the diff against the default is obvious.
CUSTOM_THEME: dict[str, str | None] = {
    "Keyword": "cyan",
    "Keyword.Declaration": "cyan",
    "Keyword.Namespace": "cyan",
    "Keyword.Reserved": "cyan",
}


def HighlightedCodeDemo() -> Element:
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
            Text("HighlightedCode demo", bold=True),
            Text("pip install pyink[highlight]  — Esc / Ctrl+C to quit", dimColor=True),
            Text("Python:", dimColor=True),
            Box(HighlightedCode(PYTHON_SNIPPET, language="python"), padding=0),
            Text("JavaScript:", dimColor=True),
            Box(HighlightedCode(JS_SNIPPET, language="javascript"), padding=0),
            Text("SQL:", dimColor=True),
            Box(HighlightedCode(SQL_SNIPPET, language="sql"), padding=0),
            Text("JSON (line_numbers=True):", dimColor=True),
            Box(
                HighlightedCode(JSON_SNIPPET, language="json", line_numbers=True),
                padding=0,
            ),
            Text("Custom theme (Keyword=cyan):", dimColor=True),
            Box(
                HighlightedCode(
                    PYTHON_SNIPPET, language="python", theme=CUSTOM_THEME
                ),
                padding=0,
            ),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(HighlightedCodeDemo(), columns=70, rows=40)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
