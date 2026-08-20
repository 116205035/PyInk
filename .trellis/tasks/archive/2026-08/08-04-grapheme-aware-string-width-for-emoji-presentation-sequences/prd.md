# grapheme-aware string-width for emoji presentation sequences

## Goal

Fix `string_width()` (and downstream `wrap_text`, `_hard_break`,
`_word_break`, `_take_visible`, `_take_visible_tail`) so the layout
engine measures display width **per grapheme cluster** instead of
per codepoint. The visible symptom today is column misalignment in
any row that contains emoji presentation sequences, keycap sequences,
ZWJ-joined family emoji, or regional-flag pairs — because `wcwidth`
sums each codepoint independently and the result under- or
over-counts vs. what the terminal actually renders.

## What I already know

* `string_width()` lives at `src/ink/layout/measure.py:88` and is a
  thin wrapper over the module-local `wcswidth()` at line 71.
* The module-local `wcswidth()` strips ANSI (CSI + OSC 8) then
  **iterates per codepoint**, summing `wcwidth(ch)`. This is the
  bug — it ignores grapheme cluster boundaries.
* **`wcwidth` 0.8.1 is installed** (project pins `wcwidth>=0.2.0`,
  resolver picks 0.8.1). The upstream `wcwidth.wcswidth` in 0.8.x
  is **grapheme-aware** — verified empirically on 2026-08-05:

  | Input | PyInk's loop | upstream `wcwidth.wcswidth` |
  |---|---|---|
  | `☠️` (U+2620 + VS16) | 1 | **2** |
  | `1️⃣` (keycap) | 1 | **2** |
  | `👨‍👩‍👧` (ZWJ family) | 6 | **2** |
  | `🇨🇳` (flag) | 4 | **2** |
  | `👍🏽` (skin tone) | 2 or 4 | **2** |
  | `🎉` (single emoji) | 2 | **2** |
  | `❤︎` (VS15 text) | 1 | **1** |

* `wcwidth 0.8.1` also exposes a public `iter_graphemes(s)` helper
  that segments per UAX #29. Verified it correctly clusters all
  the inputs above. **We already have everything we need — no new
  dependency required.**
* `string_width` is consumed widely: layout/flex, render/diff,
  render/instance, layout/render_layout, plus externals
  (`big_text`, `diff`, `markdown`, `text_input`) — 58 occurrences
  across 11 files. The fix needs to be transparent at the
  `string_width`/`wcswidth` boundary so callers don't change.
* `rendering-contracts.md §2` documents the contract callers rely
  on today: "CJK full-width characters count as 2 cells; combining
  characters and emoji are handled via `wcwidth`". That sentence
  is the bug — wcwidth's *per-codepoint* API isn't emoji-aware,
  but `wcswidth` *is* in 0.8.x.
* Existing test `tests/layout/test_measure.py:30` only asserts
  `string_width("🎉") == 2`. No coverage for presentation
  sequences, keycaps, ZWJ families, or flags.

## Assumptions (temporary — to validate via research)

* "Grapheme cluster" = Unicode UAX #29 default grapheme cluster
  boundary, extended with the emoji-ZWJ rules from UAX #29 §3.
* Terminals we target (modern xterm, Windows Terminal, iTerm2,
  Apple_Terminal, kitty, Alacritty) render the examples above as
  2 cells per cluster. Need to verify Windows Terminal behaviour
  specifically since this project's primary dev env is Windows.
* A pure-Python grapheme clusterer exists that we can vendor or
  depend on without breaking the "minimal deps" stance.

## Open Questions

* _None — all resolved on 2026-08-05. See Decision below._

## Research References

* [`research/python-libs.md`](research/python-libs.md) — Recommends
  `pyuegc` + cluster-width override; flags that the bug has two
  layers (segmentation + cluster-aware width). *Finding superseded
  by the discovery that wcwidth 0.8.x already ships both layers —
  see "What I already know" above.*
* [`research/width-rules.md`](research/width-rules.md) — Canonical
  width per cluster type (all 2 except VS15 text-presentation → 1),
  UAX #11/#29/UTS #51 sources, terminal-divergence matrix.

## Research Notes

### Verified facts (2026-08-05)

* `wcwidth 0.8.1` is installed; project pins `wcwidth>=0.2.0`.
* `wcwidth.wcswidth` is grapheme-aware and returns the correct
  width for every cluster in the acceptance criteria.
* `wcwidth.iter_graphemes(s)` correctly segments presentation
  sequences, keycaps, ZWJ families, flags, and CJK (each CJK char
  is its own grapheme).
* `wcwidth.wcstwidth(s, term)` exists for terminal-specific width
  overrides (e.g. account for xterm vs Windows Terminal emoji
  divergence). Out of scope for MVP — Unicode baseline matches
  Windows Terminal, which is the project's primary dev env.

### Constraints from our repo

* `wcwidth` is already a hard runtime dep; bumping the floor to
  `>=0.8.0` is the only dep change.
* The fix must be transparent at the `string_width` / `wcswidth`
  boundary — callers in 11 files don't change.
* Wrap helpers (`_hard_break`, `_word_break`, `_take_visible`,
  `_take_visible_tail`, `_visible_chars`, `_tokenise_words`) all
  iterate per codepoint via `_char_width` / `_strip_ansi`. They
  need to be refactored to iterate per cluster so that wrap never
  splits a cluster.

### Feasible approaches

**Approach A: Delegate to upstream `wcwidth` 0.8.x APIs** — RECOMMENDED

* How it works:
  * Replace the module-local `wcswidth(s)` body with
    `wcwidth.wcswidth(_strip_ansi(s))` (handle the `-1` error path
    like today).
  * Add a `_iter_clusters(s)` helper that returns
    `[(cluster_str, cluster_width), ...]` from
    `wcwidth.iter_graphemes(_strip_ansi(s))` + `wcwidth.wcswidth`.
  * Refactor `_hard_break` / `_word_break` / `_take_visible` /
    `_take_visible_tail` / `_visible_chars` / `_tokenise_words` to
    iterate clusters instead of characters.
  * Bump `wcwidth>=0.2.0` → `wcwidth>=0.8.0` in `pyproject.toml`.
* Pros: Smallest change, no new dep, all cluster types work,
  Unicode version tracks wcwidth upstream (currently 17.0).
* Cons: Ties us to wcwidth's evolution; the VS15 bug ships
  unless we override it.
* Cost: ~1 PR (~150-250 LoC including tests + spec update).

**Approach B: Hand-roll a narrow segmenter for presentation
sequences only**

* How it works: Build a regex / scan that splits `<base> U+FE0F`
  into a cluster, sum widths per cluster via `_char_width`.
* Pros: No dep bump, no new helper.
* Cons: Doesn't fix ZWJ families, flags, keycaps, skin-tone
  modifiers — the task title's symptom. Defeats the point.
* Cost: ~1 PR but only solves ~30% of the actual reported bugs.

**Approach C: Add `pyuegc` + custom width table**

* How: Use `pyuegc.egc_split` for segmentation; build our own
  cluster→width lookup so we control the VS15 fix and any
  PyInk-specific overrides.
* Pros: Independent of wcwidth evolution; we own the width table.
* Cons: Adds a runtime dep (small but real); more code to maintain;
  duplicates work wcwidth 0.8.x already does.
* Cost: ~2 PRs (segregation + width table + integration).

## Requirements (evolving)

* `string_width` MUST remain the canonical entry — no caller-side
  changes.
* Width MUST be computed per grapheme cluster, not per codepoint.
* All existing tests in `tests/layout/test_measure.py` MUST stay
  green (ascii, cjk, ansi-stripping, simple emoji like 🎉,
  combining marks, mixed).
* New tests MUST cover presentation sequences, keycaps, ZWJ
  families, regional flags — both in isolation and mixed with CJK
  / styled content.
* `wrap_text` round-trips: a wrapped line whose payload contains
  such sequences MUST NOT split a cluster across lines.

## Acceptance Criteria (evolving)

* [x] `string_width("☠️") == 2` (presentation sequence)
* [x] `string_width("1️⃣") == 2` (keycap sequence)
* [x] `string_width("👨‍👩‍👧") == 2` (ZWJ family)
* [x] `string_width("🇨🇳") == 2` (regional flag)
* [x] `string_width("👍🏽") == 2` (skin tone modifier)
* [x] `string_width("❤︎") == 1` (VS15 text presentation)
* [x] `string_width("a☠️b") == 4` (mixed with ASCII)
* [x] `string_width("你☠️好") == 6` (mixed with CJK)
* [x] `string_width("\x1b[31m☠️\x1b[0m") == 2` (with ANSI)
* [x] `wrap_text("☠️☠️☠️", 4)` returns the cluster boundary, not a
      mid-cluster break
* [x] `wrap_text("👨‍👩‍👧!", 2)` returns `["👨‍👩‍👧", "!"]`, not a
      mid-ZWJ-family break
* [x] Existing `tests/layout/test_measure.py` stays green
  (ascii / cjk / ansi / single-emoji / combining-marks / mixed)
* [x] 1 render-pipeline integration test: a `Table` or `Box` row
      containing `☠️` cells renders aligned
* [x] `rendering-contracts.md §2` updated; docstring at
      `src/ink/layout/measure.py:88` refreshed

## Definition of Done (team quality bar)

* Tests added/updated (unit + integration — at minimum
  `tests/layout/test_measure.py`, plus 1 render-pipeline test
  that shows a Table / diff row containing `☠️` aligns)
* Lint / typecheck / pytest all green
* `rendering-contracts.md §2` updated; docstring refreshed
* Rollout considered — this changes widths in-flight for any
  rendered frame; risk is layout reflow, not data loss

## Out of Scope (explicit, will revisit if user expands)

* East-Asian width fixes outside emoji (Nushu, tangent width —
  handled by wcwidth already)
* RTL / bidi reordering
* Rendering of emoji themselves (only their *width* is in scope)
* Custom font / glyph fallback (terminal-level concern)
* **TextInput cursor atomicity on clusters** — Left/Right arrow
  currently moves per codepoint, which puts the cursor *inside* a
  ZWJ family emoji after one keypress. Related but separate concern;
  should be its own task (`text-input-grapheme-cursor`).
* **VS15 (text-presentation) override** — ship upstream's behaviour
  as-is. `🎉︎` (party + VS15) measures 2 instead of 1; rare in
  practice. Document as known minor divergence.
* **Terminal-specific width overrides** via `wcwidth.wcstwidth` —
  Windows Terminal matches Unicode baseline, which is the project's
  primary dev env. xterm / Apple Terminal / VTE / Alacritty diverge
  but we won't compensate per-terminal in MVP.
* **CHANGELOG entry** — user opted out for this iteration.

## Technical Notes

* Spec contract: `.trellis/spec/frontend/rendering-contracts.md §2`
  (string_width + ANSI stripping helpers)
* Implementation site: `src/ink/layout/measure.py` — public
  `string_width`, internal `wcswidth`, plus the per-character
  helpers used by wrap (`_char_width`, `_visible_chars`,
  `_hard_break`, `_word_break`, `_take_visible`, `_take_visible_tail`)
* The wrap path is sensitive to *where* a cluster boundary falls,
  not just its width — `_hard_break` and `_word_break` advance per
  codepoint today. Any fix likely needs a `_graphemes(s)` helper
  that returns `[(cluster_str, cluster_width), ...]` and a
  refactor of the wrap helpers to iterate clusters instead of
  characters.

## Research References

* _to be filled in after trellis-research sub-agents persist
  findings to `research/*.md` — see Open Questions_

## Decision (ADR-lite)

**Context**: `string_width` measures per-codepoint, breaking on
grapheme clusters (emoji presentation sequences, keycaps, ZWJ
families, flags, skin-tone modifiers). 11 files / 58 call sites
depend on the current `string_width` / `wcswidth` boundary.

**Decision**: Approach A — delegate to upstream `wcwidth 0.8.x`
APIs. Replace the module-local `wcswidth` body with a call to
`wcwidth.wcswidth`; refactor the wrap helpers (`_hard_break`,
`_word_break`, `_take_visible`, `_take_visible_tail`,
`_visible_chars`, `_tokenise_words`) to iterate
`wcwidth.iter_graphemes` clusters instead of per codepoint. Bump
`pyproject.toml` constraint from `wcwidth>=0.2.0` to `wcwidth>=0.8.0`.

**Consequences**:
* All cluster types in scope work for free; no hand-rolled
  segmentation or width table to maintain.
* Unicode version tracks wcwidth upstream (currently 17.0).
* We inherit one known upstream bug: VS15 on default-emoji
  codepoints measures 2 instead of 1. Documented; rare in
  practice. Override is Out of Scope for MVP.
* Width table changes from "per codepoint via wcwidth" to
  "per cluster via wcwidth.wcswidth" — this means rendered width
  of emoji-heavy frames changes; users may see reflow but no data
  loss. Acceptable for an unreleased v0.2.x project.

## Technical Approach

### Files to change

* `src/ink/layout/measure.py` — refactor core
* `pyproject.toml` — bump `wcwidth>=0.8.0`
* `tests/layout/test_measure.py` — cluster unit tests
* `tests/layout/test_measure_integration.py` (new) or augment an
  existing `tests/render/` test — 1 render integration test
* `.trellis/spec/frontend/rendering-contracts.md §2` — refresh
  the contract wording + the docstring at `measure.py:88`

### Implementation sketch

```python
# src/ink/layout/measure.py (after refactor)

def wcswidth(s: str) -> int:
    """Display width of ``s`` ignoring ANSI escape sequences.

    Delegates to upstream ``wcwidth.wcswidth`` which is grapheme-
    cluster-aware (handles emoji presentation sequences, keycaps,
    ZWJ families, regional flags, skin-tone modifiers).
    """
    stripped = _strip_ansi(s)
    if not stripped:
        return 0
    w = _wcswidth_upstream(stripped)  # wcwidth.wcswidth
    return w if w >= 0 else 0


def _iter_clusters(s: str) -> list[tuple[str, int]]:
    """Return ``[(cluster_str, cluster_width), ...]`` for ``s``
    after stripping ANSI. Cluster boundaries come from
    ``wcwidth.iter_graphemes``.
    """
    out = []
    for cl in _iter_graphemes_upstream(_strip_ansi(s)):
        w = _wcswidth_upstream(cl)
        out.append((cl, w if w >= 0 else 0))
    return out
```

Wrap helpers iterate `_iter_clusters(s)` instead of
`for ch in _strip_ansi(s)`. Each cluster is emitted as an atomic
unit — never split.

### PR breakdown

* **PR1 — width boundary + dep bump**:
  * `measure.py`: replace `wcswidth` body with upstream call.
  * `pyproject.toml`: bump `wcwidth>=0.8.0`.
  * `tests/layout/test_measure.py`: add cluster-width unit tests
    (presentation seq, keycap, ZWJ family, flag, skin tone,
    VS15 text-presentation, mixed-with-ANSI, mixed-with-CJK).
  * All other wrap helpers continue to work per-codepoint until
    PR2 (so this PR is shippable on its own — widths are right,
    wrap may still split clusters).

* **PR2 — cluster-aware wrap**:
  * `measure.py`: add `_iter_clusters` helper; refactor
    `_hard_break` / `_word_break` / `_take_visible` /
    `_take_visible_tail` / `_visible_chars` / `_tokenise_words`
    to iterate clusters.
  * `tests/layout/test_measure.py`: add wrap-never-splits-cluster
    tests for every cluster type.

* **PR3 — integration test + docs**:
  * One render-pipeline integration test: a `Table` (or
    `Box` row) containing `☠️` / `👨‍👩‍👧` cells renders aligned.
  * Update `.trellis/spec/frontend/rendering-contracts.md §2` and
    the `string_width` docstring.
  * Mark task complete via `task.py finish`.
