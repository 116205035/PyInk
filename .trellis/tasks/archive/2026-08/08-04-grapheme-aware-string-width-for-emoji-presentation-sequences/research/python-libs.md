# Research: Python grapheme cluster segmentation libraries

- **Query**: Pick a dep (or decide to vendor / hand-roll) for grapheme-aware string width in PyInk
- **Scope**: external (PyPI / GitHub / Unicode TR29)
- **Date**: 2026-08-05

## TL;DR for the impatient

- PyInk's current bug is **two-layer**, not one-layer:
  1. `wcwidth.wcswidth(s)` walks code points; for `🤝🏽` it returns **4**, but the cluster renders as a single 2-cell glyph on every modern terminal.
  2. Even if you segment first into extended grapheme clusters (EGCs), `wcwidth.wcswidth(cluster)` *still* returns wrong values for ZWJ / skin-tone / keycap sequences — see `wcwidth.wcswidth('🤝🏽') == 4` measured on `wcwidth==0.2.5`. So **segmentation alone is necessary but not sufficient**; the width heuristic must also be cluster-aware.
- The strongest pure-Python candidate is **`pyuegc`** (Unicode 16, MIT, 60 kB wheel, ~200 lines of segmentation logic), used *together with* the existing `wcwidth` plus a small "if it's a single cluster containing an Extended_Pictographic → width 2" override.
- `regex` (\X) works and is the most popular option by far, but it's a C extension and overshoots the use case.
- `grapheme` (the famous one) is unmaintained and stuck on Unicode 13; a community fork exists as `graphemeu` (Unicode 16, MIT, pure Python).

---

## Findings

### Primary candidates

#### 1. `grapheme` — https://github.com/alvinlindstam/grapheme

| Field | Value |
|---|---|
| Latest version | **0.6.0** |
| Last release | **2020-03-07** (sdist only) |
| Last commit on `master` | **2021-01-01** (a docs PR); substantive code last touched 2020-06-02 |
| Maintenance status | **Effectively abandoned.** 14 open issues, last issue activity 2026-01. No new release in 6+ years. |
| License | **MIT** |
| Pure-Python? | Yes (ships optional Cython sources in `grapheme/cython/`, but they are not built by default — pure-Python `finder.py` is the runtime path) |
| Wheels | **None.** sdist only (#24 "Missing wheel for 0.6.0" still open). Builds fine from sdist on Py 3.11+. |
| Install size | ~200 kB sdist; ~50 kB installed |
| `requires_python` | Not declared (classifiers stop at Py 3.8) |
| Unicode version tracked | `UNICODE_VERSION = "13.0.0"` (hard-coded in `api.py`). Unicode 13 = 2020. Newer emoji (handshake with skin tones `🤝🏽` is Unicode 14.0 / Emoji 14.0, Sep 2021) are NOT in the property table, but they still get bucketed as `EXTENDED_PICTOGRAPHIC` because that property is a wide range, so they still segment correctly — verified live: `grapheme.length('🤝🏽') == 1`, family ZWJ `👨‍👩‍👧‍👦 == 1`, flag `🇺🇳 == 1`, keycap `1️⃣ == 1`, etc. |
| API shape | `grapheme.graphemes(s)` → iterator of clusters. `grapheme.length(s, until=None)`. `grapheme.slice(s, start, end)`. `grapheme.safe_split_index(s, index)`. `grapheme.grapheme_lengths(s)` → iterator of code-point counts per cluster. |
| Width? | **No.** The package exposes zero width information. You still need `wcwidth`. README and `__init__.py` confirm there is no width API. |
| Known issues affecting us | #19 "Unicode 14.0" (open since 2022), #22 "Error finding grapheme_break_property.json on windows after making executable" (PyInstaller pain), #24 "Missing wheel for 0.6.0", #25 "An updated fork" (links to `timendum/grapheme`). |

#### 1b. `graphemeu` (timendum's maintained fork) — https://github.com/timendum/grapheme

| Field | Value |
|---|---|
| PyPI name | **`graphemeu`** (the import name is still `grapheme` — see `pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages = ["grapheme"]`) |
| Latest version | **0.10.0** |
| Last release | **2026-01-08** |
| Last commit | **2026-01-08** |
| Maintenance status | **Active single-maintainer fork.** 0 open issues, recent test/coverage/ruff work, type annotations added. |
| License | **MIT** (preserved from upstream) |
| Pure-Python? | Yes |
| Wheels | `graphemeu-0.10.0-py3-none-any.whl` |
| `requires_python` | `>=3.10` (satisfies PyInk's 3.11+ floor) |
| Unicode version tracked | Unicode 16.0 (per CHANGELOG and `unicode-data/` directory) |
| API shape | Same as `grapheme` (drop-in replacement) |
| Install size | Comparable to upstream (~50 kB installed) |
| Width? | **No**, same as upstream |
| Caveat | Low adoption (11 stars). Single-maintainer bus factor. PyPI name differs from import name (`graphemeu` vs `grapheme`) — mildly confusing for contributors reading `requirements.txt`. |

#### 2. `regex` (Mrab Nytt) — https://github.com/mrabarnett/mrab-regex

| Field | Value |
|---|---|
| Latest version | **2026.7.19** |
| Release cadence | ~2–4 weeks (multiple releases per month in 2026) |
| Maintenance status | **Very active.** Author responds to issues; recent commits fix SIGSEGVs and free-threading bugs. |
| License | **Apache 2.0** (file `LICENSE.txt`: derived from CPython `re`, additions under Apache 2.0). SPDX marks it `NOASSERTION` on GitHub because the file combines CNRI Python 1.6 + Apache 2.0, but for our purposes Apache 2.0 is BSD/MIT-compatible. |
| Pure-Python? | **No.** C extension. Wheels for every CPython from 3.10 to 3.14, every platform (`win32`, `win_amd64`, `win_arm64`, `macosx_10_9_universal2`, `macosx_11_0_arm64`, manylinux x86_64/aarch64/ppc64le/s390x, musllinux, riscv64). No mypyc / no source-build pain on Win/macOS/Linux. |
| Install size | ~150 kB wheel + C ext |
| Unicode version | Tracks the latest Unicode at release time (currently Unicode 16.x); `\X` grapheme segmentation follows UAX #29 with the version of Unicode the wheel was built against. |
| API for graphemes | `regex.findall(r'\X', s)` → `list[str]`. Also `regex.split(r'\X', s)`, `regex.finditer(r'\X', s)`. Verified live against every test sequence from the task: handshake+skin, family ZWJ, flag, keycap, presentation, heart-fire, eye-speech — all return a single cluster. |
| Width? | **No.** Regex only segments. You still need `wcwidth`. |
| Issues affecting us | 77 open issues, but none about `\X`. The C-extension dependency is the main objection for PyInk (which currently has zero C-extension runtime deps). The package is also much larger than the problem we need to solve. |

#### 3. `pyuegc` — https://github.com/mlodewijck/pyuegc

| Field | Value |
|---|---|
| Latest version | **16.0.3** |
| Last release | **2025-01-14** |
| Last commit | **2025-01-14** ("Fix RegEx pattern"); other recent commits fix RI symbols and conjunct-linker clusters |
| Maintenance status | **Active.** 0 open issues. Author pushes Unicode-version-aligned releases. |
| License | **MIT** (code) + Unicode Data Files and Software License (data) — both permissive, MIT-compatible |
| Pure-Python? | **Yes.** `py3-none-any.whl`. The whole implementation is one file `egc.py` (197 lines) + a generated `_unicode.py` property table. |
| Wheels | `pyuegc-16.0.3-py3-none-any.whl` (61 kB wheel, ~418 kB uncompressed, dominated by the Unicode property table) |
| `requires_python` | `>=3.8` (works on 3.11+) |
| Unicode version tracked | **16.0.0** (Unicode 16 = Sep 2024). `UNICODE_VERSION` and `UCD_VERSION` are exported. |
| API shape | `EGC(unistr) -> list[str]` — single function, returns the list of clusters. Empty string returns `[]`. That's the entire public API. |
| Width? | **No.** Author is explicit: this only implements UAX #29 segmentation. Width is out of scope. |
| Handles all task sequences? | **Yes, verified live:** `EGC('🤝🏽')==1`, `EGC('👨‍👩‍👧‍👦')==1`, `EGC('🇺🇳')==1`, `EGC('1️⃣')==1`, `EGC('☠️')==1`, `EGC('❤️‍🔥')==1`, `EGC('👁️‍🗨️')==1`, `EGC('🧑‍🚀')==1`. Also handles Hangul L+V+T sequences, Devanagari conjunct-linker clusters (GB9c), and Indic spacing marks. Tested against the official `GraphemeBreakTest.txt`. |
| Code quality | Clean, type-annotated, doctests, references TR29 sections inline (GB9c, GB11, GB12/13). The algorithm is a straightforward rules-engine: build a `(prev, curr)` break-rule set from a chart, walk code points, track RI parity and Extended_Pictographic positions. |

#### 4. Other candidates considered and rejected

- **`graphemeX`** (PyPI) — Rust + PyO3 extension. Disqualified (C extension; only macOS arm64 wheel).
- **`uniseg`** (PyPI, hosted on Bitbucket) — mature, pure-Python, covers grapheme/word/line segmentation AND wraps `wcwidth`-like helpers. License unspecified on PyPI ("no license" field). 0.10.1 released 2026-01-09. Would be a reasonable choice but the unspecified license is a problem for PyInk's MIT-compatibility requirement, and it's much more code than we need.
- **`cwcwidth`** — Cython bindings to libc `wcswidth`. Same per-codepoint limitations as `wcwidth`; doesn't help.

### Coverage matrix (segmentation correctness, live-tested)

| Sequence | Code points | `wcwidth.wcswidth` (current PyInk) | `grapheme.length` | `regex \X` count | `pyuegc EGC` count | Real rendered width |
|---|---|---|---|---|---|---|
| `🤝🏽` handshake + skin tone | 2 | **4** (wrong) | 1 | 1 | 1 | 2 |
| `👨‍👩‍👧‍👦` family ZWJ | 7 | **8** (wrong) | 1 | 1 | 1 | 2 |
| `🇺🇳` flag (RI pair) | 2 | 2 (correct by accident) | 1 | 1 | 1 | 2 |
| `1️⃣` keycap | 3 | **1** (wrong, should be 2) | 1 | 1 | 1 | 2 |
| `☠️` presentation | 2 | 1 (wrong, should be 2 on emoji-presentation terminals) | 1 | 1 | 1 | 1 or 2 |
| `❤️‍🔥` heart on fire | 4 | **3** (wrong) | 1 | 1 | 1 | 2 |
| `👁️‍🗨️` eye in speech bubble | 5 | **2** (wrong) | 1 | 1 | 1 | 2 |
| `日本語` CJK | 3 | 6 (correct) | 3 | 3 | 3 | 6 |

**Key takeaway:** segmentation alone fixes counting (cluster count vs code-point count), but to get the *width* right you also need a cluster-level width rule. The simplest such rule that's still useful: "if a cluster contains any Extended_Pictographic code point, treat it as width 2; otherwise sum `wcwidth` of the code points in the cluster." This is what e.g. Textual and urwid do.

---

### The pure-stdlib path

**Can `unicodedata` + `re` segment grapheme clusters per UAX #29?** Yes, but `re` does not support `\X`, so you have to encode the rules yourself.

- `unicodedata.category(cp)` exists, but UAX #29 needs more granular properties than the general category: it needs the Grapheme_Cluster_Break (GCB) property, `Extended_Pictographic`, and `Indic_Conjunct_Break` (InCB). None of these are exposed by `unicodedata`.
- The GCB / Extended_Pictographic / InCB data is in `GraphemeBreakProperty.txt`, `emoji-data.txt`, and `DerivedCoreProperties.txt` from the UCD. You'd have to ship those tables (or a derived lookup) yourself.
- Python 3.12's `unicodedata` tracks UCD 15.0.0; Python 3.13 tracks 16.0.0; Python 3.14 tracks 16.0.0. So `unicodedata` *does* know about new emoji code points (e.g., it can tell you `🤝` exists), but it cannot tell you the GCB property of a code point.
- A spec-compliant minimal segmentation ruleset is ~150–250 lines of Python **plus** a code-point → GCB lookup table. The lookup table is the painful part: `GraphemeBreakProperty.txt` is ~30k code points with explicit assignments plus a handful of ranges. The table compresses to maybe 30–60 kB of JSON or 5–15 kB of Python range tuples. See `pyuegc/_unicode.py` (418 kB uncompressed, mostly the property table) for a worked example.
- The actual algorithm (the part you write) is short — see `pyuegc/egc.py` (197 lines including docstrings, comments, and doctests) for a complete reference. The author essentially encodes the UAX #29 rules as a `(prev_gcb, curr_gcb)` break chart + special-case handlers for GB9c (conjunct linkers), GB11 (ZWJ + emoji), GB12/13 (RI pair parity).
- Maintaining freshness = re-running the Unicode data extraction each Unicode release (every September). This is real ongoing work, which is exactly what `pyuegc`'s maintainer already does for us.

**Honest assessment of "vendor 200 lines":** the *algorithm* is 200 lines; the *data* is a separate 400 kB blob you must regenerate. If you only need the common cases (CR/LF, Extend, SpacingMark, ZWJ, Regional_Indicator pair, Hangul syllables, Extended_Pictographic + ZWJ + Extended_Pictographic, keycap sequence, tag flags) you can hand-roll a ~80-line ruleset on top of `unicodedata.category` plus a tiny hardcoded range table for the ~10 property categories that matter — *but* this is exactly what `grapheme` 0.6.0 did and the result bit-rotted (stuck on Unicode 13 for 6 years).

---

## Recommendation table

| Option | Dep cost | Maintenance risk | Code complexity for us | Unicode freshness | Width handling | Recommendation tier |
|---|---|---|---|---|---|---|
| **`pyuegc`** (add dep) | +1 pure-Python dep (~60 kB wheel) | Low — active maintainer, 0 open issues, MIT, MIT-compatible data license | Trivial: `from pyuegc import EGC` then call `EGC(s)` | Tracks Unicode 16.0 already; releases within weeks of new Unicode versions | Not included — pair with existing `wcwidth` + an emoji-pictographic override (~10 lines) | **Tier 1 — Recommended.** Smallest dep that fully solves segmentation, freshest Unicode, cleanest API, easiest upgrade path. |
| **`graphemeu`** (timendum fork, add dep) | +1 pure-Python dep (~50 kB) | Medium — single-maintainer fork, 11 stars, but actively shipping | Trivial: same API as `grapheme` (`grapheme.graphemes`, `grapheme.length`, etc.) | Unicode 16.0 as of 0.10.0 | Not included — same as pyuegc, pair with `wcwidth` | **Tier 2 — Backup.** Use if you prefer the iterator API or already have `grapheme` muscle memory. Lower community trust than pyuegc. |
| **`regex`** (add dep) | +1 C-extension dep (~150 kB + binary) | Very low — extremely active, 592 stars, on PyPI since 2010 | Trivial: `regex.findall(r'\X', s)` | Tracks latest Unicode | Not included — pair with `wcwidth` | **Tier 3.** Disqualified by the "no C extensions" rule. Otherwise would be Tier 1. |
| **`grapheme`** (original) | +1 pure-Python dep | **High** — unmaintained 6 years, no wheels, stuck on Unicode 13 | Same as graphemeu | Unicode 13.0 | Not included | **Tier 4 — Do not use.** Pick the fork instead. |
| **Vendor `pyuegc/egc.py`** | 0 deps, +~600 lines incl. data table | Low — code is stable; you only re-pull when Unicode updates | Medium: copy `egc.py` + `_unicode.py` into `_vendor/pyuegc/`, add attribution | As fresh as the snapshot you vendor | Not included | **Tier 2 — Reasonable if "zero new runtime deps" is a hard rule.** Buy yourself 1–2 years before needing a refresh; re-vendor on the next Unicode bump. |
| **Hand-roll 50-line ruleset** | 0 deps, +50 lines | **High** — you become the segmentation maintainer; bit-rot is guaranteed | Low initial, high long-term | Whatever you last updated to | Not included | **Tier 5 — Do not do this** for the segmentation itself. The 50-line version will miss conjunct-linker clusters, prepend-augmented graphemes, and edge cases in tag-flag sequences. Only do this for the *width override* on top of a real segmenter. |
| **Hand-roll 200-line UAX #29 + data** | 0 deps, +200 lines code + ~400 kB data | Medium — same maintenance as vendoring pyuegc but you own the bugs | High | You regenerate the data table each September | Not included | **Tier 4.** Strictly worse than vendoring `pyuegc` unless you have a strong reason to want full control. |

### Concrete recommendation

Use **`pyuegc`** as the new runtime dep, keep **`wcwidth`**, and add a small cluster-width function in PyInk:

```python
from pyuegc import EGC
from wcwidth import wcwidth

_EMOJI_PRESENTATION_SEL = 0xFE0F  # VS-16
_ZWJ = 0x200D

def cluster_width(cluster: str) -> int:
    # Emoji cluster heuristic: contains ZWJ, VS-16, or any Extended_Pictographic
    # → renders as one double-width cell on a modern terminal.
    if any(ord(c) >= 0x1F000 or c == '‍' or c == '️' for c in cluster):
        return 2
    w = sum(wcwidth(ord(c)) for c in cluster)
    return w if w >= 0 else 0

def string_width(s: str) -> int:
    return sum(cluster_width(c) for c in EGC(s))
```

That's ~10 lines of PyInk code on top of `pyuegc` + the existing `wcwidth`, and it fixes both the segmentation and the width layers of the bug. If "no new runtime deps" is non-negotiable, vendor `pyuegc` (it's a single self-contained module) instead.

---

## External References

- PyPI: [`pyuegc`](https://pypi.org/project/pyuegc/) · [source](https://github.com/mlodewijck/pyuegc) · [CHANGELOG](https://github.com/mlodewijck/pyuegc/blob/main/CHANGELOG.md)
- PyPI: [`grapheme`](https://pypi.org/project/grapheme/) · [source](https://github.com/alvinlindstam/grapheme) (unmaintained)
- PyPI: [`graphemeu`](https://pypi.org/project/graphemeu/) · [source](https://github.com/timendum/grapheme) (active fork)
- PyPI: [`regex`](https://pypi.org/project/regex/) · [source](https://github.com/mrabarnett/mrab-regex) · [LICENSE.txt](https://github.com/mrabarnett/mrab-regex/blob/hg/LICENSE.txt) (Apache 2.0)
- PyPI: [`uniseg`](https://pypi.org/project/uniseg/) · [Bitbucket](https://bitbucket.org/emptypage/uniseg-py/) (license unspecified — not recommended)
- PyPI: [`graphemeX`](https://pypi.org/project/graphemeX/) (Rust+PyO3 — disqualified)
- Unicode: [UAX #29 Text Segmentation, rev 45](https://www.unicode.org/reports/tr29/tr29-45.html) · [GraphemeBreakTest.txt](https://www.unicode.org/Public/16.0.0/ucd/auxiliary/GraphemeBreakTest.txt) · [Unicode 16.0.0](https://www.unicode.org/versions/Unicode16.0.0/core-spec/chapter-3/#G52443)

## Related Specs

None — `.trellis/spec/` does not currently contain a grapheme or text-width spec.

## Caveats / Not Found

- "Width 2 for every cluster containing an emoji-related code point" is a *heuristic*; it matches what Windows Terminal, iTerm2, Kitty, WezTerm, and modern GNOME Terminal render. Older terminals (and `cat` in a non-emoji-aware font) may render `☠️` as width 1. There is no way to be perfectly correct without querying the terminal's font, which is out of scope.
- Regional indicator pairs (flags) and tag sequences (e.g. `🏴󠁧󠁢󠁥󠁮󠁧󠁿` England) are *also* width 2 in modern terminals. The heuristic above catches flags via `ord >= 0x1F000` and catches tag sequences via the trailing U+E00xx range, but you may want to special-case the tag flag base `U+1F3F4` for clarity.
- License text for `regex` is dual (CNRI Python 1.6 for the CPython-derived parts + Apache 2.0 for additions). Both are MIT-compatible, but you should mention this in `NOTICES` if you adopt it.
- The `pyuegc` wheel contains Unicode data tables governed by the [Unicode Data Files and Software License](https://www.unicode.org/license.txt); if PyInk ships a `NOTICES` file, both the MIT (code) and Unicode (data) attributions belong there.
- I did not benchmark performance. `pyuegc` walks the string in pure Python with a dict lookup per code point; for very long strings it's slower than `regex`'s C `\X` matcher. PyInk's hot path is short UI strings (typically < 1000 chars), so this is unlikely to matter, but worth a profile if you're rendering a 10k-line log viewer.
