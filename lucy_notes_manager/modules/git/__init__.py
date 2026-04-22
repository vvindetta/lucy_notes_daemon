"""
Git module package.

The `Git` class is the public entry point registered with the module manager.
Internal helpers are split across submodules:

- batch.py     — `_RepoBatch` dataclass
- paths.py     — path helpers and environment construction
- commands.py  — thin wrappers around `git` subprocess calls
- parsing.py   — pure parsers for git output / conflict markers
- conflicts.py — auto-resolve merge conflicts
- pull.py      — high-level pull+merge with auto-resolve
- module.py    — the `Git` class (event hooks + worker loop)

Imports are re-exported here for backwards compatibility.
"""

from lucy_notes_manager.modules.git.batch import _RepoBatch
from lucy_notes_manager.modules.git.module import Git

__all__ = ["Git", "_RepoBatch"]
