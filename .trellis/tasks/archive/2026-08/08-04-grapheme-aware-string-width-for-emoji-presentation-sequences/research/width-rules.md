# Research: Display-width rules per grapheme cluster type

- **Query**: Build a correct cluster-type to terminal-width lookup table for the PyInk layout engine, covering emoji presentation sequences, text presentation sequences, keycaps, ZWJ sequences, regional indicators / flags, flag tag sequences, single-codepoint emoji, and skin-tone modifiers.
- **Scope**: external (Unicode standards + terminal emulator behaviour)
- **Date**: 2026-08-04
- **Authoritative sources cited**: UAX #11 (East Asian Width), UAX #29 (Text Segmentation), UTS #51 (Unicode Emoji), the `wcwidth` 0.8.1 Python library source (which is grapheme-aware and is the project's existing dependency), Markus Kuhn's `wcwidth.c`, and terminal emulator behaviour tables shipped in `wcwidth 0.8.1`.

---

## TL;DR (decision matrix)

For each cluster category, the **Unicode-prescribed / "correct" terminal width** is shown below. This is the width that the major modern terminals (Windows Terminal, iTerm2, kitty, wezterm, ghostty) converge on, and the width that the latest grapheme-aware `wcwidth.wcswidth` returns.

| # | Cluster type | Example | Canonical width | Rule source |
|---|---|---|---|---|
| 1 | **Emoji presentation sequence** (`<base> U+FE0F`) | `☠️` (U+2620 + VS16), `❤️`, `➡️` | **2** | UTS #51 §2.2 "Emoji Presentation Sequence"; base must be in `Emoji_Base ∪ Emoji` and base must be a *text-presentation-default* codepoint (the `Emoji_Presentation` property is false). VS16 promotes narrow→wide. |
| 2 | **Text presentation sequence** (`<base> U+FE0E`) | `☠︎`, `❤︎` | **1** (narrow) | UTS #51 §2.2; VS15 forces text presentation. If the base is normally wide (i.e. in the VS15-WIDE-TO-NARROW table, which mirrors `Emoji_Presentation=true`), it shrinks to 1. If the base is already narrow (`a` + VS15), VS15 is a no-op and width stays 1. |
| 3 | **Emoji keycap sequence** (`<0-9|#|*> U+FE0F U+20E3`) | `1️⃣`, `*️⃣`, `#️⃣` | **2** | UTS #51 §4.2 "Emoji Keycap Sequence" — this is a fixed 3-codepoint sequence; U+20E3 (COMBINING ENCLOSING KEYCAP) visually merges base + VS16 into a single 2-cell rounded box. The `<base>` MUST be one of `0..9 # *`; other bases are not canonical. The VS16 is **required** in the canonical form — `<base> U+20E3` alone (no VS16) is **not** a valid keycap and `wcwidth` measures it as 1. |
| 4 | **ZWJ sequence** (`<emoji> (U+200D <emoji>)+`) | `👨‍👩‍👧` family, `🧑🏻‍🤝‍🧑🏽` people-holding-hands, `🏴‍☠️` pirate flag | **2** | UAX #29 §3 GB11 rule (`ExtPict Extend* ZWJ × ExtPict`). UTS #51 §2.4 "Emoji ZWJ Sequence" defines the canonical list (`emoji-zwj-sequences.txt`). Always 2 cells in Unicode's model — even multi-ZWJ chains like 5-person families are 2 cells. The *only* practical upper bound on chain length is the sequence being a *canonical* ZWJ sequence; non-canonical chains may still cluster graphemically but terminal rendering falls back to multiple glyphs. |
| 5 | **Regional indicator pair / flag** (`<RI> <RI>`) | `🇨🇳`, `🇺🇸`, `🇯🇵` | **2** | UAX #29 §3 GB12/GB13 ("do not break between regional indicator pairs"). UTS #51 §2.3 defines the 256 canonical flag pairs. **Unpaired regional indicator letter is technically 2 cells per codepoint in UAX #11** (RI block is East Asian Width `A`mbiguous → `wcwidth` 0.8.1 returns 2 in the default ambiguous-wide path). This is the most controversial case — see Terminal Divergence below. |
| 6 | **Flag tag sequence** (`U+1F3F4 <tag letters> U+E007F`) | `🏴󠁧󠁢󠁥󠁮󠁧󠁿` England, `🏴󠁧󠁢󠁳󠁣󠁴󠁿` Scotland | **2** | UTS #51 §2.5 "Emoji Tag Sequence"; the black flag base (U+1F3F4) is wide; tag letters (U+E0061..U+E007A) and the tag terminator (U+E007F) are all zero-width (in the `Default_Ignorable_Code_Point` set). |
| 7 | **Single-codepoint emoji** (default emoji presentation) | `🎉`, `🐲`, `👍` | **2** | UAX #11 places the emoji block (`U+1F300..U+1FAFF`, plus pictographs in the `U+2600..U+27BF` range with `Emoji_Presentation=true`) in the **W**ide category → 2 cells. Already correct in legacy `wcwidth` per-codepoint. |
| 8 | **Skin-tone modifier** (`<emoji> U+1F3FB..U+1F3FF`) | `👍🏻`, `👍🏿`, `👋🏽` | **2** | UTS #51 §2.6 "Emoji Modifier"; the modifier (Fitzpatrick skin-tone) is a zero-width `Extend` codepoint that combines with the preceding `Emoji_Modifier_Base`. The cluster retains the base's width (2). A lone Fitzpatrick codepoint is itself width 2 in `wcwidth` (terminal renders a coloured square), so even if the modifier is misplaced you won't undercount. |

### Fallback rule (cluster not classifiable)

If a grapheme cluster doesn't match any of categories 1–8 (e.g. a base + combining-mark cluster in a non-emoji script), compute the width as **`wcwidth(base) + sum(0 for mark in marks)`** — i.e. the existing per-codepoint logic. The new grapheme-aware path is an *override* on top of the existing `_char_width` behaviour, not a replacement.

### Lone control / combining characters

| Codepoint(s) | Width | Notes |
|---|---|---|
| `U+200D` ZWJ (alone) | **0** | Combining joiner, no visible glyph by itself. |
| `U+FE0E` / `U+FE0F` VS15/VS16 alone (no base) | **0** | Variation selectors are zero-width extenders. |
| `U+20E3` COMBINING ENCLOSING KEYCAP alone | **0** | Combining mark; needs a base. |
| `U+1F3FB..U+1F3FF` Fitzpatrick alone | **2** | `wcwidth` returns 2 — terminal renders a coloured square. |
| `U+E0020..U+E007F` tag block | **0** | All `Default_Ignorable_Code_Point`. |
| `U+1F1E6..U+1F1FF` regional indicator alone | **2** (default `wcwidth`), **1** (some terminals) | See "Terminal divergence" below — *the single biggest source of misalignment in practice*. |

---

## Detailed rule per category

### 1. Emoji presentation sequences — `<base> U+FE0F`

**Rule.** If `<base>` is in the **VS16_NARROW_TO_WIDE** table (i.e. base has `Emoji=true` and `Emoji_Presentation=false` — base defaults to text presentation), then `base + U+FE0F` is one grapheme cluster of width **2**.

**Authoritative source.**
- UTS #51 §2.2 "Presentation Sequences": https://www.unicode.org/reports/tr51/#Emoji_Variation_Sequences
- `emoji-data.txt` `Emoji_Presentation` column: https://www.unicode.org/Public/UCD/latest/ucd/emoji/emoji-data.txt
- `wcwidth.table_vs16.VS16_NARROW_TO_WIDE['9.0.0']` — 113 ranges / 213 codepoints (incl. `0-9 # *` for keycaps, `© ®`, the `U+203C..U+3299` pictograph block, etc.).

**Is VS16 always 2?** **No.** VS16 is a no-op if the base is **not** in the narrow-to-wide table:
- `a️` (ASCII `a` + VS16) → still 1 (ASCII not in the table).
- `中️` (CJK `中` + VS16) → still 2 (CJK wide chars are not in the narrow-to-wide table; VS16 doesn't shrink them, but it also doesn't promote them — they're already wide).
- `🎉️` (PARTY + VS16) → still 2 (already default-emoji-presentation; VS16 is redundant but harmless).

So the cluster-width rule for category 1 is: **width(base) if base ∉ VS16_NARROW_TO_WIDE, else 2**.

### 2. Text presentation sequences — `<base> U+FE0E`

**Rule.** If `<base>` is in the **VS15_WIDE_TO_NARROW** table (i.e. base defaults to emoji presentation), then `base + U+FE0E` shrinks the cluster from width 2 down to **1**. Otherwise VS15 is a no-op and width is `wcwidth(base)`.

**Authoritative source.**
- UTS #51 §2.2.
- `wcwidth.table_vs15.VS15_WIDE_TO_NARROW['9.0.0']` — 91 ranges covering default-emoji-presentation codepoints such as `U+2600..U+27BF` pictographs, `U+1F300..U+1F5FF` miscellaneous symbols, etc.
- Example: `🎉︎` (PARTY + VS15) is *expected* to be 1 cell. `wcwidth 0.8.1` returns 2 — this is a known bug in the VS15 path (it never shrinks because the base wasn't measured as 2 *before* encountering VS15 in some sequences). PyInk's lookup should **follow UTS #51** (return 1) rather than the buggy `wcwidth 0.8.1` value.

**Important nuance.** VS15 attached to a wide CJK character (`中︎`) is **not** in the VS15_WIDE_TO_NARROW table (CJK isn't emoji), so width stays 2. VS15 only narrows codepoints whose `Emoji_Presentation` property is `true`.

### 3. Emoji keycap sequences — `<base> U+FE0F U+20E3`

**Rule.** When `<base>` ∈ `{0..9, #, *}` and the next two codepoints are `U+FE0F` then `U+20E3`, the three-codepoint sequence forms a single 2-cell grapheme cluster.

**Authoritative source.**
- UTS #51 §4.2 "Emoji Keycap Sequences": https://www.unicode.org/reports/tr51/#Emoji_Keycap_Sequences — canonical list is exactly the 12 bases × 1 form (12 sequences).
- UAX #29 GB11 (`ExtPict Extend* ZWJ × ExtPict`) plus GB9 (`× Extend`) — U+20E3 has `Extend` category so it doesn't break the cluster.

**Edge cases.**
- `<base> U+20E3` (no VS16) — **not** a canonical keycap sequence. `wcwidth` measures this as 1. Real terminals may render it as 2 or 1 depending on font fallback. PyInk should treat the **canonical** 3-codepoint sequence as width 2 and fall back to per-codepoint for the malformed 2-codepoint form.
- `<base> U+FE0F U+20E3` where base is **not** one of `0..9 # *` (e.g. `a️⃣`) — not canonical, terminals generally don't render as a keycap. Treat as `<narrow base> + <0-width VS16> + <0-width CCK>` = `wcwidth(base)` = 1 in PyInk's lookup.

### 4. ZWJ sequences — `<emoji> (U+200D <emoji>)*`

**Rule.** A U+200D ZWJ between two `Extended_Pictographic` codepoints (or with Fitzpatrick modifiers in between) does **not** break the grapheme cluster. The whole chain renders as a single glyph occupying **2 cells**, regardless of how many emoji are chained.

**Authoritative source.**
- UAX #29 §3 GB11 rule: `ExtPict Extend* ZWJ × ExtPict`. https://www.unicode.org/reports/tr29/#GB11
- UTS #51 §2.4 "Emoji ZWJ Sequences": https://www.unicode.org/reports/tr51/#emoji_zwj_sequences — canonical list at https://www.unicode.org/Public/emoji/latest/emoji-zwj-sequences.txt
- `wcwidth._wcswidth._scan_zwj_cluster_end` implements this exact rule.

**Canonical examples (from `emoji-zwj-sequences.txt`):**
- `👨‍👩‍👧` family (man + woman + girl) — 5 codepoints, 1 cluster, width 2.
- `👨‍👩‍👧‍👦` family of 4 — 7 codepoints, 1 cluster, width 2.
- `👨‍👩‍👧‍👦‍👦` family of 5 — 9 codepoints, 1 cluster, width 2.
- `🧑🏻‍🤝‍🧑🏽` people holding hands with skin tones — 8 codepoints, 1 cluster, width 2.
- `🏴‍☠️` pirate flag (black flag + ZWJ + skull-and-crossbones + VS16) — 4 codepoints, 1 cluster, width 2.
- `👩‍❤️‍👨` couple with heart (woman + ZWJ + heavy black heart + VS16 + ZWJ + man) — 6 codepoints, 1 cluster, width 2.
- `🧑🏻‍❤️‍💋‍🧑🏽` couple kiss with skin tones — 10 codepoints, 1 cluster, width 2.

**Multi-ZWJ that are wider than 2?** **No** — in Unicode's data model every canonical ZWJ sequence is exactly 2 cells. Terminals that render multi-ZWJ families as 3+ cells (Apple Terminal renders 4-person family as 11 cells in the table above) are buggy / outdated. PyInk should follow Unicode: **always 2**.

**Non-canonical ZWJ chains.** A user-typed `🚀‍🚀‍🚀` (3 rockets joined by ZWJ) is **not** in `emoji-zwj-sequences.txt`. UAX #29 still treats it as a single grapheme cluster (per GB11), but no font ships a ligature for it, so real terminals render each rocket separately (visible width = 6) even though the grapheme count is 1. This is a hard case — see "Controversial cases" below.

### 5. Regional indicator pairs / flags — `<RI> <RI>`

**Rule.** Two consecutive regional indicator letters (U+1F1E6..U+1F1FF) form a single flag grapheme cluster of width **2** if they encode a valid ISO 3166-1 alpha-2 country code. UAX #29 GB12/GB13 simply says "pairs of regional indicators don't break" — so any pair of RIs clusters as 1 grapheme.

**Authoritative source.**
- UAX #29 §3 GB12 + GB13: https://www.unicode.org/reports/tr29/#GB12
- UTS #51 §2.3 "Emoji Flag Sequences": https://www.unicode.org/reports/tr51/#emoji_flag_sequences — canonical list at https://www.unicode.org/Public/emoji/latest/emoji-sequences.txt (search for "flag sequences").
- `wcwidth._wcswidth` regional-indicator parity logic at lines 152-160: if the number of preceding RIs is odd, the current RI is consumed without width contribution.

**Parity algorithm.** Walk the string; maintain a count of consecutive RIs ending at the current position. The 1st RI of each pair contributes width 2, the 2nd contributes 0. Result:
- `🇨🇳` (CN, valid) → width 2.
- `🇦🇦` (AA, not a valid country but still paired) → width 2 (UAX #29 doesn't validate ISO codes).
- `🇦` (single, unpaired) → **width 2** by UAX #11's default (RI block is East Asian Width `A`mbiguous, and `wcwidth 0.8.1` resolves ambiguous→wide in the default path). **This is the single most controversial case** — see below.
- `🇦🇦🇦` (3 RIs) → first 2 pair up (width 2), 3rd is unpaired (width 2) → total **4**.
- `🇦🇦🇦🇦` (4 RIs) → 2 pairs → total **4**.
- `🇦🇦🇦🇦🇦` (5 RIs) → 2 pairs + 1 single → total **6**.

### 6. Flag tag sequences — `U+1F3F4 <tag letters> U+E007F`

**Rule.** Base `U+1F3F4` (BLACK FLAG, default emoji presentation → wide) followed by a sequence of tag letters in the range `U+E0061..U+E007A` (subtag letters a–z) and terminated by `U+E007F` (CANCEL TAG) forms a single grapheme cluster of width **2**. All tag characters are zero-width `Default_Ignorable_Code_Point`s.

**Authoritative source.**
- UTS #51 §2.5 "Emoji Tag Sequences": https://www.unicode.org/reports/tr51/#emoji_tag_sequences
- Tag sequences list at https://www.unicode.org/Public/emoji/latest/emoji-sequences.txt (search for "tag sequences").
- Currently defined subdivisions: England (gbeng), Scotland (gbsct), Wales (gbwls), Texas (ustx), and a handful of others.
- `wcwidth.table_zero.ZERO_WIDTH` includes the entire `U+E0001..U+E007F` tag block.

**Edge case.** `U+1F3F4` *alone* (no tag letters) is the BLACK FLAG base, width 2. It's also the base for the pirate flag ZWJ sequence (`🏴‍☠️`).

### 7. Single-codepoint emoji — `🎉`, `🐲`, `👍`

**Rule.** Default-emoji-presentation codepoints are in UAX #11's **W**ide category → 2 cells. Already correct in legacy `wcwidth`. Document for completeness only — no grapheme-cluster logic needed; `wcwidth(cp)` returns 2.

**Source.** UTS #51 §1.4 "Emoji Presentation Property"; `wcwidth.table_wide.WIDE_EASTASIAN` includes the emoji blocks.

### 8. Skin-tone modifiers — `<emoji> U+1F3FB..U+1F3FF`

**Rule.** When a Fitzpatrick skin-tone modifier (U+1F3FB..U+1F3FF, the 5 shades) follows an `Emoji_Modifier_Base`, the pair forms a single grapheme cluster of width **2** (the modifier is `Extend`, zero-width).

**Authoritative source.**
- UTS #51 §2.6 "Emoji Modifiers": https://www.unicode.org/reports/tr51/#emoji_modifiers
- UAX #29 GB9 (`× Extend`) — Fitzpatrick modifiers have category `Extend`.
- `wcwidth._wcswidth` lines 162-164 skips the Fitzpatrick range when the preceding codepoint is in `_EMOJI_ZWJ_SET`.

**Canonical bases.** Defined in `emoji-data.txt` `Emoji_Modifier_Base` column: human-shaped emoji (👍 👊 👋 🙏 👱 👩 👨 🧑 etc.). When the modifier follows a non-base (e.g. `🇨🇳🏻` flag + skin), the modifier is technically misplaced; terminals render the modifier as a standalone 2-cell coloured square, so the cluster width becomes 2 + 2 = 4 in practice. PyInk should follow UAX #29 (modifier is still `Extend`, so the cluster is `🇨🇳🏻` of width 2) and note this divergence.

---

## Critical finding: PyInk's current `wcswidth` is broken — it doesn't use upstream `wcswidth`

`src/ink/layout/measure.py:71-85` defines a project-local `wcswidth` that **iterates per codepoint and sums `_wcwidth(ch)`**. The PRD describes this as equivalent to `wcwidth.wcswidth` but ANSI-stripped — **it is not**. `wcwidth 0.8.1`'s upstream `wcwidth.wcswidth` is fully grapheme-aware (see `wcwidth/_wcswidth.py:60-196`) and returns the correct widths for *every* cluster type in the table above.

Verified empirically (`wcwidth 0.8.1`):

```
CLUSTER                      PyInk's wcswidth   wcwidth.wcswidth   Terminal
                                                  (grapheme-aware)   reality
☠️  (U+2620 + VS16)              1                  2                  2
1️⃣  (keycap)                     1                  2                  2
👨‍👩‍👧  (family ZWJ)               6                  2                  2
🇨🇳  (flag pair)                 4                  2                  2
🏴󠁧󠁢󠁥󠁮󠁧󠁿  (England tag flag)        2                  2                  2
👍🏻  (skin tone)                  4                  2                  2
🧑🏻‍❤️‍💋‍🧑🏽  (multi-ZWJ)                11                 2                  2
```

**Implication for the fix.** PyInk has two implementation options:

1. **Quick path.** Replace the per-codepoint loop in `src/ink/layout/measure.py:71-85` with a call to `wcwidth.wcswidth(stripped)` (no new dependency — `wcwidth>=0.2.0` is already required; 0.8.1 is the current version). Add `iter_graphemes`-based segmentation to the wrap helpers (`_hard_break`, `_word_break`, `_take_visible`, `_take_visible_tail`) so they advance per cluster, not per codepoint. ~30 lines changed.
2. **Hand-rolled.** Implement UAX #29 GB9–GB13 + the VS15/VS16 lookup yourself, keying off the `Extended_Pictographic`, `Emoji_Presentation`, `Extend`, `Regional_Indicator`, and `SpacingMark` properties vendored from `emoji-data.txt` and `DerivedCoreProperties.txt`. Higher implementation cost, no observable benefit since `wcwidth 0.8.1` already does exactly this.

The quick path is **strongly recommended** unless the project wants to drop `wcwidth` as a dep (which the PRD's "minimal deps" note does *not* call for — `wcwidth` is already there).

---

## Terminal divergence — controversial cases (CRITICAL for the lookup table)

The widths above are what **Unicode prescribes** and what **grapheme-aware `wcwidth 0.8.1`** returns. They are also what the **modern terminals converge on**. They are **NOT** what every terminal actually renders. The following table, measured via `wcwidth.wcstwidth(s, term_program=<name>)` (the per-terminal override path shipped in `wcwidth 0.8.1`, sourced from the `ucs-detect` cross-terminal measurement project), shows the divergence:

| Cluster | WT | iTerm2 | kitty | wezterm | ghostty | xterm | Apple_Term | VTE | Alacritty |
|---|---|---|---|---|---|---|---|---|---|
| `🎉` single-cp | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| `☠️` presentation | **2** | 2 | 2 | 1 | 2 | **1** | **1** | **1** | **1** |
| `❤️` presentation | **2** | 2 | 2 | 1 | 2 | **1** | **1** | **1** | **1** |
| `1️⃣` keycap | **2** | **1** | 2 | 1 | 2 | **1** | **1** | **1** | **1** |
| `👨‍👩‍👧` ZWJ family | **2** | 2 | 2 | 2 | 2 | **6** | **8** | **6** | **6** |
| `👨‍👩‍👧‍👦` 4-person family | **2** | 2 | 2 | 2 | 2 | **8** | **11** | **8** | **8** |
| `🧑🏻‍🤝‍🧑🏽` people-hold-hands | **2** | 2 | 2 | 2 | 2 | **10** | **12** | **10** | **2** |
| `🇨🇳` flag pair | **2** | 2 | 2 | 1 | 2 | **1** | **1** | **1** | **1** |
| `🇦` unpaired RI | **2** | 2 | 2 | 1 | 2 | **1** | **1** | **1** | **1** |
| `🏴󠁧󠁢󠁥󠁮󠁧󠁿` England tag flag | **2** | 2 | 2 | 2 | 2 | 2 | **8** | 2 | 2 |
| `👍` thumbs-up | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |
| `👍🏻` thumbs-up skin | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 | 2 |

**Takeaways for PyInk:**

1. **The cluster-type → width mapping is unambiguous.** Use the Unicode-prescribed width (the table at the top of this file). This matches Windows Terminal, iTerm2, kitty, ghostty and wezterm — the terminals PyInk developers and end-users most commonly use.

2. **Known-divergent terminals are xterm, Apple Terminal, VTE, Alacritty, and wezterm** (for some clusters). These terminals render clusters *wider* than Unicode (or *narrower*, for presentation sequences). PyInk cannot perfectly match what every terminal renders without querying the terminal itself; the pragmatic choice is to follow Unicode and accept that some terminal/font combinations will visually misalign.

3. **For the specific bug PyInk is fixing** (column misalignment in tables / diffs containing emoji), the **Unicode-prescribed widths** are also the right answer because:
   - They match what the dominant modern terminals (WT, iTerm2, kitty, ghostty) actually render.
   - The PRD lists WT, iTerm2, Apple_Terminal, kitty, Alacritty as the target set — but PyInk's primary dev env is Windows Terminal (per PRD line 50), so WT's behaviour is the de-facto reference.
   - Even on divergent terminals, the Unicode-prescribed width is *closer* than today's per-codepoint sum (e.g. today `🇨🇳` measures 4, but xterm renders 1 — neither matches; the new code will measure 2, matching WT/iTerm2/kitty exactly and being a smaller-magnitude error on xterm/VTE).

4. **Optional: a future `term_program`-aware override.** `wcwidth 0.8.1` ships `wcstwidth(s, term_program=...)` which applies per-terminal overrides. PyInk could call this with `term_program=True` to auto-detect from `TERM_PROGRAM`/`TERM`. **Recommendation for the current fix: do NOT do this.** It would make PyInk's measured widths inconsistent between developers on different terminals (the layout differs at test time vs runtime). Stick with the Unicode baseline. If a future task adds a `pyink_term_program` knob to the layout context, `wcstwidth` is the drop-in replacement.

### Controversial edge cases worth knowing

- **Unpaired regional indicator.** Unicode says width 2 (`A`mbiguous → wide). xterm/VTE/Alacritty/Apple/wezterm render it 1. WT/iTerm2/kitty/ghostty render it 2. PyInk should pick **2** (matches `wcwidth` default and matches WT, the primary dev env). Risk: rare in real text — almost no-one types a lone `🇦`.
- **Non-canonical ZWJ chain** (`🚀‍🚀‍🚀`). UAX #29 still clusters it as 1 grapheme; no font has a ligature; terminals render each rocket separately (width 6). PyInk will report width 2 and the rendered output will overflow. This is unavoidable without a canonical-sequence lookup table. Mitigation: ship the `emoji-zwj-sequences.txt` list and treat any ZWJ chain not in the list as multiple clusters.
- **Malformed keycap** (`1⃣` without VS16). Unicode treats `U+20E3` as `Extend` so this is one grapheme; `wcwidth` measures 1; terminals render 1 or 2 depending on font. PyInk's lookup should follow `wcwidth` and return 1 for the malformed form (or upgrade to 2 only when the canonical 3-codepoint form is seen).
- **Skin tone after flag** (`🇨🇳🏻`). UAX #29 makes it one cluster; `wcwidth` reports 2; terminals render 2 (flag) + 2 (lone skin-tone square) = 4. PyInk will undercount by 2. Acceptable — this is invalid input.
- **VS15 on default-emoji** (`🎉︎`). Unicode says narrow (1). `wcwidth 0.8.1` has a bug and reports 2 (VS15 path only shrinks characters measured wide *in the same pass*, which doesn't always happen). PyInk's hand-rolled lookup should explicitly return 1 for this case.

---

## Decision table for PyInk's implementation

Walk the string with a UAX #29 grapheme clusterer; for each cluster `g`, compute width as follows (in order — first match wins):

| Cluster shape | Width | Source |
|---|---|---|
| **`<RI> <RI>`** (two regional indicators) | **2** | GB12 |
| **`<RI>`** (unpaired regional indicator, last in run) | **2** | UAX #11 `A`mbiguous→wide |
| **`<base> U+FE0F`** where `base ∈ VS16_NARROW_TO_WIDE` | **2** | UTS #51 §2.2 |
| **`<base> U+FE0F`** where `base ∉ VS16_NARROW_TO_WIDE` | `wcwidth(base)` | VS16 is a no-op |
| **`<base> U+FE0E`** where `base ∈ VS15_WIDE_TO_NARROW` | **1** | UTS #51 §2.2 |
| **`<base> U+FE0E`** where `base ∉ VS15_WIDE_TO_NARROW` | `wcwidth(base)` | VS15 is a no-op |
| **`<0-9\|#\*> U+FE0F U+20E3`** (canonical keycap) | **2** | UTS #51 §4.2 |
| **`<0-9\|#\*> U+20E3`** (non-canonical, no VS16) | `wcwidth(base)` | Treat `U+20E3` as Extend (0) |
| **`<emoji> (U+200D <emoji-or-modifier>)+`** (ZWJ chain) | **2** | GB11 |
| **`<emoji> U+1F3FB..U+1F3FF`** (skin tone) | **2** | UTS #51 §2.6 (modifier is Extend) |
| **`U+1F3F4 <tag letters> U+E007F`** (tag flag) | **2** | UTS #51 §2.5 (tags are zero-width) |
| **`<single Emoji_Presentation=true cp>`** | **2** | UAX #11 `W`ide |
| **`<base> <combining marks>`** (any other cluster) | `wcwidth(base) + sum(0)` | Existing per-codepoint logic |

### Lone / unclassifiable codepoints (defensive)

| Codepoint | Width |
|---|---|
| Control characters (C0/C1) | 0 (PyInk already coerces `-1` → 0 in `_char_width`) |
| `U+200D` ZWJ alone | 0 |
| `U+FE0E` / `U+FE0F` alone | 0 |
| `U+20E3` alone | 0 |
| `U+1F3FB..U+1F3FF` alone | 2 (`wcwidth` default) |
| `U+E0001..U+E007F` tag block | 0 |

---

## File / source references

### Internal — current broken behaviour

| File | Line | What's there |
|---|---|---|
| `src/ink/layout/measure.py` | 50-55 | Imports `wcwidth.wcwidth` as `_wcwidth`; never imports `wcwidth.wcswidth`. |
| `src/ink/layout/measure.py` | 71-85 | **The bug.** Local `wcswidth` iterates per codepoint; should call upstream `wcwidth.wcswidth` (which is grapheme-aware) or use `iter_graphemes`. |
| `src/ink/layout/measure.py` | 139-167 | `_hard_break` advances per codepoint — needs cluster-awareness. |
| `src/ink/layout/measure.py` | 170-221 | `_word_break` likewise. |
| `src/ink/layout/measure.py` | 326-343, 346-366 | `_take_visible`, `_take_visible_tail` likewise. |
| `.trellis/spec/frontend/rendering-contracts.md` | §2 (lines 139-211) | Documents the existing `string_width` contract; the sentence "CJK full-width characters count as 2 cells; combining characters and emoji are handled via `wcwidth`" needs to become "grapheme-cluster-aware" wording. |
| `.trellis/tasks/08-04-grapheme-aware-string-width-for-emoji-presentation-sequences/prd.md` | 36-42 | Lists the broken examples (`wcwidth("☠️") = 1`, etc.) — these are *per-codepoint* `wcwidth`, not grapheme-aware `wcswidth`. The PRD's "What I already know" section slightly understates how much the upstream library can do; the fix can lean on `wcwidth.wcswidth` directly. |

### External — authoritative sources

| Source | URL | What it gives you |
|---|---|---|
| UAX #11 East Asian Width | https://www.unicode.org/reports/tr11/ | Per-codepoint wide/narrow/ambiguous categories. The `W`ide set includes the emoji blocks. |
| UAX #29 Text Segmentation (Grapheme Cluster Boundaries) | https://www.unicode.org/reports/tr29/ | GB9, GB11, GB12, GB13 rules that define what a "cluster" is for emoji ZWJ sequences and regional indicators. |
| UTS #51 Unicode Emoji | https://www.unicode.org/reports/tr51/ | §2.2 (presentation sequences), §2.3 (flags), §2.4 (ZWJ), §2.5 (tag sequences), §2.6 (modifiers), §4.2 (keycaps). |
| `emoji-data.txt` | https://www.unicode.org/Public/UCD/latest/ucd/emoji/emoji-data.txt | Authoritative per-codepoint properties: `Emoji`, `Emoji_Presentation`, `Emoji_Modifier`, `Emoji_Modifier_Base`, `Emoji_Component`, `Extended_Pictographic`. |
| `emoji-sequences.txt` | https://www.unicode.org/Public/emoji/latest/emoji-sequences.txt | Canonical flag pairs and tag-flag sequences. |
| `emoji-zwj-sequences.txt` | https://www.unicode.org/Public/emoji/latest/emoji-zwj-sequences.txt | Canonical ZWJ sequences (family, role, activity, etc.). |
| Markus Kuhn `wcwidth.c` | https://www.cl.cam.ac.uk/~mgk25/ucs/wcwidth.c | The original POSIX C implementation; **not** grapheme-aware (sums per codepoint), but the reference for the zero/wide/ambiguous tables that ship in `wcwidth` 0.x. |
| `wcwidth` Python library | https://github.com/jquast/wcwidth | Pure-Python, grapheme-aware implementation. v0.8.1 ships: `wcswidth` (grapheme-aware), `iter_graphemes` (UAX #29 segmenter), `wcstwidth` (terminal-aware with per-terminal overrides), and the data tables `VS16_NARROW_TO_WIDE`, `VS15_WIDE_TO_NARROW`, `WIDE_EASTASIAN`, `ZERO_WIDTH`, `AMBIGUOUS_EASTASIAN`, `EXTENDED_PICTOGRAPHIC`. |
| `wcwidth/_wcswidth.py` | (inside the installed package) | The reference algorithm for the cluster-width lookup. Lines 30-196 (`_scan_zwj_cluster_end` + `wcswidth`) implement every rule in the decision table above. |
| `wcwidth/grapheme.py` | (inside the installed package) | UAX #29 grapheme cluster boundary implementation; pure Python; ships with the existing dep. |
| Microsoft Windows Terminal unicode-search doc | https://github.com/microsoft/terminal/blob/main/doc/unicode-search.md | WT's emoji-width behaviour reference. |
| kitty text-sizing protocol | https://sw.kovidgoyal.net/kitty/text-sizing-protocol/ | OSC 66 protocol for terminals that allow apps to override widths — irrelevant to PyInk today but documents kitty's approach. |
| `ucs-detect` cross-terminal measurement project | https://github.com/jquast/ucs-detect | The source of the per-terminal overrides that ship in `wcwidth.wcstwidth`. Used to produce the divergence table in this file. |

---

## Caveats / Not Found

1. **No way to query the live terminal's actual width for a cluster.** Terminals don't expose "how wide did you just render this glyph" over a standard escape sequence (kitty's OSC 66 text-sizing protocol is the closest, and it's kitty-only). PyInk must use a lookup table and accept divergence.
2. **`wcwidth 0.8.1`'s VS15 path has a known bug** for some default-emoji codepoints (`🎉︎` measures 2 when it should be 1). If PyInk calls `wcwidth.wcswidth` directly, this bug is inherited. Worth a separate test asserting `string_width("🎉︎") == 1` if VS15 support is in scope; otherwise document as a known minor divergence.
3. **The PRD's PRD-line-36 examples `wcwidth("☠️") = 1` etc.** are calling **`wcwidth.wcwidth` per codepoint and summing**, not `wcwidth.wcswidth`. The fix at the `string_width` boundary does **not** require a new dependency or a hand-rolled UAX #29 implementation — `wcwidth 0.8.1`'s `wcswidth` already returns 2 for all of them. The only substantive refactor needed is making the wrap helpers (`_hard_break`, `_word_break`, `_take_visible`, `_take_visible_tail`) advance per cluster instead of per codepoint, so wrap boundaries never split a cluster.
4. **No terminal measurement was performed by this research** — the divergence table is sourced from the `wcwidth 0.8.1` per-terminal override tables, which themselves come from the `ucs-detect` project. If PyInk needs to validate against an actual installed WT/iTerm/kitty, that's a separate empirical test task.
5. **Tag flag sequences for non-GB subdivisions** (e.g. other countries' subdivisions proposed in newer Unicode versions) — the `emoji-sequences.txt` list grows over time. PyInk should either pin a Unicode version or accept that the lookup is forward-compatible (treat any `U+1F3F4 <tag letters> U+E007F` as width 2 regardless of whether the tag letters form a canonical sequence).
