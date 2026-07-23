# Code Reuse Thinking Guide

> **Purpose**: Stop and think before creating new code - does it already exist?

---

## The Problem

**Duplicated code is the #1 source of inconsistency bugs.**

When you copy-paste or rewrite existing logic:
- Bug fixes don't propagate
- Behavior diverges over time
- Codebase becomes harder to understand

---

## Before Writing New Code

### Step 1: Search First

```bash
# Search for similar function names
grep -r "functionName" .

# Search for similar logic
grep -r "keyword" .
```

### Step 2: Ask These Questions

| Question | If Yes... |
|----------|-----------|
| Does a similar function exist? | Use or extend it |
| Is this pattern used elsewhere? | Follow the existing pattern |
| Could this be a shared utility? | Create it in the right place |
| Am I copying code from another file? | **STOP** - extract to shared |

---

## Common Duplication Patterns

### Pattern 1: Copy-Paste Functions

**Bad**: Copying a validation function to another file

**Good**: Extract to shared utilities, import where needed

### Pattern 2: Similar Components

**Bad**: Creating a new component that's 80% similar to existing

**Good**: Extend existing component with props/variants

### Pattern 3: Repeated Constants

**Bad**: Defining the same constant in multiple files

**Good**: Single source of truth, import everywhere

---

## When to Abstract

**Abstract when**:
- Same code appears 3+ times
- Logic is complex enough to have bugs
- Multiple people might need this

**Don't abstract when**:
- Only used once
- Trivial one-liner
- Abstraction would be more complex than duplication

---

## After Batch Modifications

When you've made similar changes to multiple files:

1. **Review**: Did you catch all instances?
2. **Search**: Run grep to find any missed
3. **Consider**: Should this be abstracted?

---

## Gotcha: Asymmetric Mechanisms Producing Same Output

**Problem**: When two different mechanisms must produce the same file set (e.g., recursive directory copy for init vs. manual `files.set()` for update), structural changes (renaming, moving, adding subdirectories) only propagate through the automatic mechanism. The manual one silently drifts.

**Symptom**: Init works perfectly, but update creates files at wrong paths or misses files entirely.

**Prevention checklist**:
- [ ] When migrating directory structures, search for ALL code paths that reference the old structure
- [ ] If one path is auto-derived (glob/copy) and another is manually listed, the manual one needs updating
- [ ] Add a regression test that compares outputs from both mechanisms

---

## Gotcha: Shared Helpers for Cross-Cutting Rendering Concerns

**Problem**: Two factories need the same non-trivial transformation
(e.g. Pygments `[(token_type, value), ...]` → ANSI-coded string). Each
factory reimplementing it locally risks silent divergence: one factory
honours a theme key, the other doesn't; one splits multi-line token
values on `\n`, the other doesn't; one emits a trailing reset, the
other doesn't. The visual output drifts without any test catching it.

**Concrete example**: `tokens_to_ansi_string` in
`src/ink/externals/highlighted_code.py` is the canonical Pygments-tokens
→ ANSI converter. `StructuredDiff` (`src/ink/externals/diff.py`)
imports and reuses it instead of writing its own. When the converter's
behaviour changes (new token type, multi-line edge case), both
factories get the fix.

**Prevention checklist**:
- [ ] Before writing a tokeniser / formatter / normaliser, search for an
  existing one in `src/ink/externals/` and `src/ink/`
- [ ] If two factories share a transformation, extract it next to the
  primary consumer and re-export, rather than copy-pasting
- [ ] Cross-reference the shared helper in the relevant contract spec
  (e.g. `frontend/rendering-contracts.md` Section 3 for the tokens→ANSI
  converter) so future contributors find it

---

## Checklist Before Commit

- [ ] Searched for existing similar code
- [ ] No copy-pasted logic that should be shared
- [ ] Constants defined in one place
- [ ] Similar patterns follow same structure
