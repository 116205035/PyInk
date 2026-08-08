# Releasing PyInk

Single-package repo. Release = bump 2 locations + commit + tag + push.

## 2 version locations (must stay in sync)

| File | Field |
|------|-------|
| `pyproject.toml` | `version = "X.Y.Z"` |
| `src/ink/__init__.py` | `__version__ = "X.Y.Z"` |

Drift between them = bug.

## Steps

```bash
# 1. Bump both locations to the same X.Y.Z
$EDITOR pyproject.toml              # version = "X.Y.Z"
$EDITOR src/ink/__init__.py         # __version__ = "X.Y.Z"

# 2. Sanity check
grep -nE '^(version|__version__)' pyproject.toml src/ink/__init__.py

# 3. Commit + tag + push
git commit -am "release: bump X.Y.Z"
git tag -a vX.Y.Z -m "release X.Y.Z"
git push
git push origin vX.Y.Z
```

## Roll back (before push)

```bash
git tag -d vX.Y.Z
git reset --hard HEAD~1
```

## After releasing PyInk — bump pin in JarvisCopilot

PyInk is consumed by JarvisCopilot as a PEP 508 direct URL dependency. After PyInk tag is pushed upstream, bump the pin in JarvisCopilot:

```bash
cd /d/Projects/JarvisCopilot

# Script handles 5 lockstep replacements + commit + tag pyink-vX.Y.Z
python tools/release.py --pyink X.Y.Z

# Refresh local venv (cache key changed, must --reinstall)
uv pip install --reinstall -e jarvis

# Push
git push origin pyink-vX.Y.Z
git push
```

The JarvisCopilot script will `git ls-remote` this repo to verify `vX.Y.Z` actually exists before running — so always push the PyInk tag first.

## SemVer guidance

- **patch** (0.2.0 → 0.2.1): bug fixes (e.g., grapheme-aware string_width)
- **minor** (0.2.x → 0.3.0): backward-compatible new features
- **major** (0.x → 1.0): breaking API changes

## Why no release script?

PyInk is a single package with 2 version locations and infrequent releases. A 5-line shell-each release flow doesn't justify a release.py — manual steps with this checklist are sufficient. If release frequency grows, revisit.
