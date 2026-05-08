"""Vivaldi prefs-schema awareness.

Vivaldi ships a complete prefs schema at
``<install>/resources/vivaldi/prefs_definitions.json`` describing every
key it stores in ``Preferences`` -- type, default, and (for enums) the
``name -> int`` mapping.  Brave/Edge/Chrome ship no comparable file, so
this module is intentionally Vivaldi-only.

What this is for:

1. ``apply`` time -- coerce friendly TOML values (e.g. enum names like
   ``"left"``) into the on-disk integer Vivaldi expects, and reject
   obvious type mismatches before they silently no-op at runtime.

2. ``settings search`` / ``settings describe`` -- discoverability:
   "what's the key for tab bar position?" without grepping a 25k-line
   JSON manually.

The loader is cached and falls back to ``None`` when the schema can't
be located -- in that case ``apply`` keeps its pre-schema behavior
(write blindly, hope for the best) and the new subcommands print a
clear "schema not found" message instead of crashing.
"""
from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

# Top-level keys in prefs_definitions.json that hold pref defs.  Other
# top-level keys (``syncable``, ``important``, ``documentation``) are
# metadata and skipped.
_VIVALDI_ROOTS: tuple[str, ...] = ("vivaldi",)
_CHROMIUM_ROOTS: tuple[str, ...] = ("chromium", "chromium_local")

# Field that marks a node as a pref leaf (vs. an intermediate group).
_TYPE_FIELD = "type"

# Env var override -- mainly for tests, but also useful when Vivaldi is
# installed somewhere unusual.
_ENV_OVERRIDE = "DOTBROWSER_VIVALDI_PREFS_DEF"


def _candidate_paths() -> list[Path]:
    """Filesystem locations where Vivaldi may have installed the schema.

    Order matters: stable channel first, then snapshots, then user-local
    flatpak-style installs.  The first one that exists wins.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return [
            Path("/Applications/Vivaldi.app/Contents/Resources/vivaldi/prefs_definitions.json"),
            home / "Applications/Vivaldi.app/Contents/Resources/vivaldi/prefs_definitions.json",
        ]
    if sys.platform.startswith("linux"):
        return [
            Path("/opt/vivaldi/resources/vivaldi/prefs_definitions.json"),
            Path("/opt/vivaldi-snapshot/resources/vivaldi/prefs_definitions.json"),
            Path("/usr/lib/vivaldi/resources/vivaldi/prefs_definitions.json"),
            Path("/usr/share/vivaldi/resources/vivaldi/prefs_definitions.json"),
            home / ".local/share/flatpak/app/com.vivaldi.Vivaldi/current/active/files/extra/vivaldi/resources/vivaldi/prefs_definitions.json",
            Path("/var/lib/flatpak/app/com.vivaldi.Vivaldi/current/active/files/extra/vivaldi/resources/vivaldi/prefs_definitions.json"),
        ]
    if sys.platform == "win32":
        candidates: list[Path] = []
        for env in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env)
            if not base:
                continue
            root = Path(base) / "Vivaldi" / "Application"
            if not root.exists():
                continue
            for child in root.iterdir():
                if child.is_dir() and child.name[:1].isdigit():
                    candidate = child / "resources" / "vivaldi" / "prefs_definitions.json"
                    if candidate.exists():
                        candidates.append(candidate)
        return candidates
    return []


def find_schema_path() -> Path | None:
    """First existing schema path, or ``None``.

    Honors ``DOTBROWSER_VIVALDI_PREFS_DEF`` for explicit override -- the
    env var wins even if it points at a missing file (so tests can
    assert "schema absent" deterministically).
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override is not None:
        p = Path(override)
        return p if p.exists() else None
    for cand in _candidate_paths():
        if cand.exists():
            return cand
    return None


def _is_leaf(node: Any) -> bool:
    return isinstance(node, dict) and _TYPE_FIELD in node


def _flatten_vivaldi_subtree(node: Any, prefix: tuple[str, ...]) -> Iterator[tuple[str, dict]]:
    """Walk the ``vivaldi`` subtree, emitting ``(dotted_key, def)``.

    Vivaldi prefs are expressed as nested objects: a leaf is any dict
    that carries a ``type`` field.  Intermediate groups recurse.
    """
    if _is_leaf(node):
        yield ".".join(prefix), node
        return
    if not isinstance(node, dict):
        return
    for k, v in node.items():
        yield from _flatten_vivaldi_subtree(v, prefix + (k,))


def _flatten_chromium_subtree(node: Any) -> Iterator[tuple[str, dict]]:
    """Walk a chromium-style subtree, emitting ``(dotted_key, def)``.

    Chromium prefs are expressed as a flat dict ``{kFooBar: {path:
    "foo.bar", type: ...}}`` -- the actual dotted key lives in the
    ``path`` field, and the C++ identifier (``kFooBar``) is just a
    handle.  Entries without a ``path`` are skipped.
    """
    if not isinstance(node, dict):
        return
    for entry in node.values():
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if isinstance(path, str) and path and _TYPE_FIELD in entry:
            yield path, entry


@functools.lru_cache(maxsize=1)
def load_schema() -> dict[str, dict] | None:
    """Return ``{dotted_key: def}`` or ``None`` when no schema is found.

    Cached for the process lifetime; schema doesn't change while
    dotbrowser is running.  Tests that need to flip the schema across
    cases call ``load_schema.cache_clear()``.
    """
    path = find_schema_path()
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    flat: dict[str, dict] = {}
    for root in _VIVALDI_ROOTS:
        sub = raw.get(root)
        if isinstance(sub, dict):
            for key, defn in _flatten_vivaldi_subtree(sub, (root,)):
                flat[key] = defn
    for root in _CHROMIUM_ROOTS:
        sub = raw.get(root)
        if isinstance(sub, dict):
            for key, defn in _flatten_chromium_subtree(sub):
                flat[key] = defn
    return flat


def lookup(schema: dict[str, dict] | None, key: str) -> dict | None:
    """Return the def for ``key`` or ``None``."""
    if schema is None:
        return None
    return schema.get(key)


# --------------------------------------------------------------------------
# Coercion + validation
# --------------------------------------------------------------------------

# Map schema ``type`` -> Python type(s) the value must be (post-coercion).
# ``enum`` is handled separately because it gets coerced.  ``double`` is
# float|int (TOML emits ints for whole numbers, JSON keeps the float).
# ``file_path`` is just a string at the JSON layer.
_TYPE_CHECK: dict[str, tuple[type, ...]] = {
    "boolean": (bool,),
    "integer": (int,),
    "double": (int, float),
    "string": (str,),
    "file_path": (str,),
    "list": (list,),
    "dictionary": (dict,),
}


def _is_strict_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_simple(t: Any, v: Any) -> bool:
    """Whether ``v`` matches schema type ``t`` (non-enum types only)."""
    if not isinstance(t, str):
        return True
    expected = _TYPE_CHECK.get(t)
    if expected is None:
        # Unknown type -- be permissive rather than reject.
        return True
    if t == "boolean":
        return isinstance(v, bool)
    if t == "integer":
        return _is_strict_int(v)
    if t == "double":
        return _is_strict_int(v) or isinstance(v, float)
    return isinstance(v, expected)


def _path_exists_in_prefs(prefs: dict, key: str) -> bool:
    """Whether ``key`` resolves to an existing leaf in ``prefs``.

    Used to suppress "unknown key" warnings for prefs that Vivaldi
    actually stores at runtime but doesn't document in
    ``prefs_definitions.json`` (a non-trivial set, e.g.
    ``vivaldi.tabs.vertical_tabs_enabled``).  Reaching the leaf is
    enough; the value type is irrelevant here.
    """
    cur: Any = prefs
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def coerce_and_validate(
    target: dict[str, Any],
    schema: dict[str, dict] | None,
    current_prefs: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Mutate ``target`` to coerce friendly values, return (warnings, errors).

    - Enum string -> int via ``enum_values`` mapping (the file format
      Vivaldi actually expects on disk).  ``"left"`` -> ``1``.
    - Type mismatch on bool/int/string/list/dict -> error string.
    - Unknown key (not in schema AND not present in current
      ``Preferences``) -> warning. Vivaldi has many runtime-set prefs
      that aren't declared in ``prefs_definitions.json``; suppressing
      the warning when the key is already present avoids false-positive
      typo alerts on every legitimate write.

    When ``schema`` is ``None`` the function is a no-op and returns
    empty lists -- callers stay schema-optional.
    """
    if schema is None:
        return [], []

    warnings: list[str] = []
    errors: list[str] = []

    for key in list(target.keys()):
        defn = schema.get(key)
        if defn is None:
            if current_prefs is not None and _path_exists_in_prefs(current_prefs, key):
                continue
            warnings.append(
                f"unknown setting key {key!r} (not in Vivaldi prefs schema "
                f"and not present in Preferences; will be written as-is, "
                f"but verify spelling)"
            )
            continue

        t = defn.get(_TYPE_FIELD)
        v = target[key]

        if t == "enum":
            enum_values = defn.get("enum_values") or {}
            valid_ints = set(enum_values.values())
            if isinstance(v, str):
                if v in enum_values:
                    target[key] = enum_values[v]
                else:
                    valid_names = ", ".join(repr(k) for k in enum_values)
                    errors.append(
                        f"{key}: enum value {v!r} not recognised "
                        f"(expected one of: {valid_names})"
                    )
            elif _is_strict_int(v):
                if v not in valid_ints:
                    errors.append(
                        f"{key}: enum int {v} out of range "
                        f"(expected one of: {sorted(valid_ints)})"
                    )
            else:
                valid_names = ", ".join(repr(k) for k in enum_values)
                errors.append(
                    f"{key}: enum expects a string or int "
                    f"(one of: {valid_names}), got {type(v).__name__}"
                )
            continue

        if not _check_simple(t, v):
            errors.append(
                f"{key}: schema expects {t}, got {type(v).__name__} "
                f"(value: {v!r})"
            )

    return warnings, errors


# --------------------------------------------------------------------------
# Search / describe (used by CLI)
# --------------------------------------------------------------------------

def search(schema: dict[str, dict] | None, query: str) -> list[tuple[str, dict]]:
    """Return ``[(key, def), ...]`` matching ``query`` (case-insensitive).

    Matches against the dotted key, the description, and (for enums)
    the enum value names.  Multi-word queries match if every whitespace-
    separated token is found somewhere.  Results are sorted by key.
    """
    if schema is None:
        return []
    tokens = [t.lower() for t in query.split() if t]
    if not tokens:
        return []

    matches: list[tuple[str, dict]] = []
    for key, defn in schema.items():
        haystack = key.lower()
        desc = defn.get("description")
        if isinstance(desc, str):
            haystack += " " + desc.lower()
        enum_values = defn.get("enum_values")
        if isinstance(enum_values, dict):
            haystack += " " + " ".join(str(k).lower() for k in enum_values)
        if all(tok in haystack for tok in tokens):
            matches.append((key, defn))
    matches.sort(key=lambda kv: kv[0])
    return matches


def format_def(key: str, defn: dict) -> list[str]:
    """Render a single schema entry as human-readable lines.

    Used by both ``settings search`` and ``settings describe`` so the
    output shape stays consistent.
    """
    lines = [key]
    desc = defn.get("description")
    if isinstance(desc, str) and desc:
        lines.append(f"  description: {desc}")
    t = defn.get(_TYPE_FIELD, "?")
    default = defn.get("default")
    type_line = f"  type: {t}"
    if default is not None:
        type_line += f"   default: {default!r}"
    lines.append(type_line)
    if t == "enum":
        enum_values = defn.get("enum_values") or {}
        pairs = ", ".join(f"{k!r}={v}" for k, v in enum_values.items())
        lines.append(f"  values: {pairs}")
    return lines
