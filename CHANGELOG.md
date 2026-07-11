# Changelog

All notable changes to PyInk are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed — `quote_color` theme key now wired up

The `quote_color` theme key (`DEFAULT_MARKDOWN_THEME["quote_color"]`)
was defined but never read — passing `theme={"quote_color": "red"}`
silently produced the default look. The key is now resolved through
the semantic layer (default `"muted"` → gray, SGR 90) and applied to
every inline text run inside a blockquote. The legacy `__quote__`
boolean flag (which only drove `dimColor=True`, SGR 2) is removed;
its role is folded into `__quote_color__` (a `None` value disables
quote colouring entirely).

### Changed — blockquote visual: `dimColor` (SGR 2) → gray colour (SGR 90)

As a consequence of wiring `quote_color` up, the default blockquote
inline text now carries the resolved quote colour (gray, SGR 90)
instead of the old `dimColor` attribute (SGR 2). Both are muted
treatments, but the SGR code differs and the terminal may render
them slightly differently. This completes PR3's semantic-colour
intent (the default value was already flipped from `"gray"` to
`"muted"` but the change was a no-op because the key was unread).

To opt out entirely, pass `theme={"quote_color": None,
"muted_color": None}` (both keys must be `None` to defeat the
semantic fallback).

### Changed (internal) — table border glyphs de-duplicated

The markdown-internal `_TABLE_BORDER_CHARS` dict (which carried both
the outer corners and the cross pieces) is replaced by
`_get_table_border_chars(style)`: outer corners are now read from
`ink.render.ansi.BORDER_STYLES` (single source of truth), only the
5 table-specific cross glyphs (`top_cross` / `mid_cross` /
`mid_left` / `mid_right` / `bottom_cross`) remain markdown-side via
`_TABLE_CROSS_CHARS`. The cross glyphs are intentionally NOT folded
into `BORDER_STYLES` (the layout renderer's `_paint_box_border`
would not read them and the rework cost is out of scope).

`BORDER_STYLES["rounded"]` is added as an alias for
`BORDER_STYLES["round"]` so the markdown-facing `table_border_style
= "rounded"` (PR2 default) and the ansi-facing `"round"` are now
interchangeable on both sides. `_TABLE_BORDER_ALIASES = {"rounded":
"round"}` maps the markdown name to the ansi name inside
`_get_table_border_chars`.

### Changed (breaking) — `Markdown` default theme rewrite (PR3)

`DEFAULT_MARKDOWN_THEME` has been rewritten to mirror the Claude Code
terminal UX. Existing callers that relied on the pre-PR3 rainbow
palette / red inline code / pure-indent blockquotes will see different
output. **Migration**: pass an explicit `theme={...}` to restore the
old defaults (see the per-key table below).

#### Heading defaults

The rainbow heading colours are gone. All six heading levels now use
the terminal's default text colour (`None`) with bold; h1 additionally
gets italic + underline for emphasis (Claude Code style).

| Key | Pre-PR3 | PR3 | Restore old look |
| --- | --- | --- | --- |
| `h1_color` | `"magenta"` | `None` | `theme={"h1_color": "magenta"}` |
| `h2_color` | `"yellow"` | `None` | `theme={"h2_color": "yellow"}` |
| `h3_color` | `"green"` | `None` | `theme={"h3_color": "green"}` |
| `h4_color` | `"cyan"` | `None` | `theme={"h4_color": "cyan"}` |
| `h5_color` | `"blue"` | `None` | `theme={"h5_color": "blue"}` |
| `h6_color` | `"gray"` | `None` | `theme={"h6_color": "gray"}` |
| `h1_italic` | `False` | `True` | `theme={"h1_italic": False}` |
| `h1_underline` | `False` | `True` | `theme={"h1_underline": False}` |

#### Inline defaults

Inline code and links now use the semantic `accent` colour key
(resolves to `cyan`, SGR 36) instead of hard-coded `red` / `blue`.
Blockquote inline text uses the `muted` semantic key (resolves to
`gray`). The semantic layer lets callers re-skin the whole document
via `theme={"accent_color": "blue"}` rather than overriding every
per-block colour.

| Key | Pre-PR3 | PR3 | Restore old look |
| --- | --- | --- | --- |
| `code_color` | `"red"` | `"accent"` (→ cyan) | `theme={"code_color": "red"}` |
| `link_color` | `"blue"` | `"accent"` (→ cyan) | `theme={"link_color": "blue"}` |
| `quote_color` | `"gray"` | `"muted"` (→ gray) | `theme={"quote_color": "gray"}` |

#### Blockquote defaults

Blockquotes now render with a visible left bar (`▎`, U+258E) in the
`muted` colour, matching Claude Code. Pre-PR3 the default was a
pure-indent look (`paddingLeft=2`, no bar).

| Key | Pre-PR3 | PR3 | Restore old look |
| --- | --- | --- | --- |
| `quote_bar_char` | `None` | `"▎"` | `theme={"quote_bar_char": None}` |
| `quote_bar_color` | `None` | `"muted"` (→ gray) | `theme={"quote_bar_color": None}` |

#### Block spacing

`Markdown` no longer applies a flat `gap=1` between every block. PR3
introduces 14 per-block spacing theme keys (`spacing_before_<type>` /
`spacing_after_<type>`) so a heading gets a 2-row trailing gap, a
paragraph gets 1, etc. The gap between two adjacent blocks is
`max(spacing_after_<prev>, spacing_before_<next>)` — whichever block
wants more space wins.

New keys (all default to `0` or `1` per the Claude Code spacing rules):

```
spacing_before_heading    = 1    spacing_after_heading    = 2
spacing_before_paragraph  = 0    spacing_after_paragraph  = 1
spacing_before_code_block = 1    spacing_after_code_block = 1
spacing_before_blockquote = 1    spacing_after_blockquote = 1
spacing_before_list       = 0    spacing_after_list       = 1
spacing_before_table      = 1    spacing_after_table      = 1
spacing_before_hr         = 1    spacing_after_hr         = 1
```

Callers that want the old flat `gap=1` look can pass all 14 keys set
to `0` and then wrap the `Markdown(...)` element in a parent `Box`
with `gap=1` — but the new defaults are the recommended starting
point.

### Fixed — nested table responsive shrink (PR3)

Tables nested inside a blockquote or list item now responsively shrink
to the parent's available width. Pre-PR3 the recursive `_render_tokens`
call inside `_render_blockquote` / `_render_list` / `_render_list_item`
didn't thread `columns`, so a nested table rendered at its ideal width
and overflowed the parent. PR3 threads `columns - indent_width` so the
table can shrink or degrade to the key-value fallback inside a quote
or list item.

### Added — semantic colour resolution (PR1, wired in PR3)

The semantic colour layer (`text` / `accent` / `secondary` / `muted` /
`border` + `success` / `error` / `warning` / `info`) introduced in PR1
is now wired into the legacy colour keys. A legacy value that is
itself a semantic name (e.g. `theme={"h1_color": "accent"}`) resolves
through `SEMANTIC_COLORS` to the concrete colour (`cyan`). This lets
callers re-skin the whole document via the semantic layer without
overriding every per-block colour.
