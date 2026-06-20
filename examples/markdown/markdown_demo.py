"""Markdown example — render Markdown source as PyInk elements (Phase 3 PR6).

Reference: ``ink-markdown``'s README examples. ``Markdown`` parses a
CommonMark source string via ``markdown-it-py`` and renders it as a
column of styled ``Text`` / ``Box`` leaves. This demo mounts a single
source string that exercises every supported block element so the
rendering of each can be eyeballed at once:

* ATX headings (``#`` ... ``######``),
* paragraphs with **bold** / *italic* / ``inline code`` / [links],
* ordered and unordered lists (with nesting),
* a block quote,
* a fenced code block (highlighted when Pygments is installed, plain
  text otherwise),
* a table,
* a horizontal rule.

Optional dependency: requires ``markdown-it-py``. Install with::

    pip install pyink[markdown]

For syntax-highlighted code blocks inside Markdown, additionally
install Pygments::

    pip install pyink[highlight]

Run::

    python examples/markdown/markdown_demo.py

Controls:

* Esc / Ctrl+C — quit
"""

from __future__ import annotations

import sys

from pyink import Box, Text, create_element, render, use_app, use_input
from pyink.core.element import Element
from pyink.externals import Markdown
from pyink.render.keys import Key

#: The Markdown source rendered in the demo. Every supported block
#: element appears at least once so the rendering of each can be
#: verified by eye.
SOURCE: str = """\
# Markdown demo

A paragraph with **bold**, *italic*, `inline code` and a
[link](https://github.com/anthropic/pyink).

## Lists

Unordered:

* apple
* banana
  * baby banana
  * baby plantain
* cherry

Ordered:

1. first
2. second
3. third

## Quote

> This is a block quote.
> It spans two lines.

## Code block

```python
@dataclass
class Greeter:
    name: str = "world"
    def hello(self) -> str:
        return f"Hello, {self.name}!"
```

## Table

| Language | Lexer | Notes            |
| -------- | ----- | ---------------- |
| Python   | py    | Most popular     |
| SQL      | sql   | Declarative      |
| JSON     | json  | Data, not code   |

## Horizontal rule

Above the line.

---

Below the line.
"""


def MarkdownDemo() -> Element:
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
            Text("Markdown demo", bold=True),
            Text(
                "pip install pyink[markdown] (and pyink[highlight] for code) — "
                "Esc / Ctrl+C to quit",
                dimColor=True,
            ),
            Markdown(SOURCE),
            flexDirection="column",
            padding=1,
            borderStyle="round",
        )

    return create_element(Impl)


def main() -> int:
    inst = render(MarkdownDemo(), columns=70, rows=36)
    try:
        inst.wait_until_exit()
    except KeyboardInterrupt:
        inst.unmount()
    return 0


if __name__ == "__main__":
    sys.exit(main())
