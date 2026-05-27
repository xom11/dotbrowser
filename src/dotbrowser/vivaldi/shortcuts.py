"""Manage Vivaldi custom keyboard shortcuts via a TOML config file.

Vivaldi stores user-overridden shortcuts in the profile `Preferences`
JSON under `vivaldi.actions`, which is a list with a single dict
inside (Chromium pref-list shape). Each entry maps a command name
(e.g. `COMMAND_CLOSE_TAB`) to `{"shortcuts": ["meta+w"], "gestures": [...]}`.
We only manage the `shortcuts` field — `gestures` (mouse gestures) are
left untouched even when we rewrite the entry.

Differences from `brave/shortcuts.py`:

1. Command IDs are already human-readable in Vivaldi's pref, so there
   is no equivalent of `brave/command_ids.py`. Names round-trip exactly
   (`COMMAND_CLOSE_TAB` → `COMMAND_CLOSE_TAB`), no resolution step.

2. Vivaldi keeps NO `default_actions` mirror in `Preferences`. To support
   reset-on-removal, we capture each command's *original* shortcuts list
   in the state sidecar the first time we manage it; the next apply that
   drops the command from the config restores that snapshot. For a fresh
   profile whose actions have not been materialized yet, the original
   map comes from Vivaldi's installed `prefs_definitions.json` schema.

3. The accelerator value format is lowercase and tokenized with `+`
   (e.g. `meta+shift+t`). Vivaldi already serializes the same `meta+`
   spelling on every platform (cmd on macOS, win/super on Linux), so no
   `Meta+` ↔ `Command+` rewrite is needed (unlike Brave).

This module exposes:

- `plan_apply(prefs_path, prefs, raw_table)` — pure: builds a `Plan` from
  a parsed `[shortcuts]` table. Used by the unified `vivaldi apply` runner.
- CLI sub-actions `dump` and `list` (read-only). Apply lives at the
  `vivaldi` level (one entry point for shortcuts + settings + pwa).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from dotbrowser.vivaldi import schema as _schema
from dotbrowser.vivaldi.utils import (
    Plan,
    find_preferences,
    load_prefs,
)

ACTIONS_KEY_PATH = ("vivaldi", "actions")
NAMESPACE = "shortcuts"

# Shown only when both the profile action map and Vivaldi's installed
# defaults schema are unavailable. Normally a fresh profile is bootstrapped
# directly from prefs_definitions.json and never reaches this fallback.
_UNINITIALIZED_HINT = (
    "this Vivaldi profile has not seeded its keyboard command catalog yet "
    "(`vivaldi.actions[0]` in Preferences is empty or missing), and "
    "dotbrowser could not load installed defaults from "
    "`prefs_definitions.json`. Vivaldi writes the catalog after Settings "
    "-> Keyboard has been touched and the browser has been fully quit. "
    "Open Vivaldi, change or reset any shortcut under Settings -> Keyboard, "
    "quit the browser entirely so it flushes Preferences, then re-run; "
    "or set DOTBROWSER_VIVALDI_PREFS_DEF to the installed schema path."
)


def _validate_table(raw: object) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        sys.exit("error: [shortcuts] must be a table")
    out: dict[str, list[str]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.startswith("COMMAND_"):
            sys.exit(
                f"error: shortcuts.{name!r} must be a Vivaldi COMMAND_* name "
                "(run `dotbrowser vivaldi shortcuts list` to see known commands)"
            )
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            sys.exit(f"error: shortcuts.{name} must be a list of strings")
        out[name] = list(value)
    return out


def _get_actions_dict(prefs: dict) -> dict[str, dict[str, Any]]:
    """Return `vivaldi.actions[0]` as a dict, creating the scaffold if missing.

    Vivaldi stores its action map as a one-element list around a dict
    (the pref schema treats the whole map as a single ListPref entry).
    A fresh profile populates this on first launch, so it is almost
    always present — but we tolerate its absence so the apply path
    works against test fixtures and partially-initialized profiles.
    """
    vivaldi = prefs.setdefault("vivaldi", {})
    actions = vivaldi.get("actions")
    if not isinstance(actions, list) or not actions:
        actions = [{}]
        vivaldi["actions"] = actions
    inner = actions[0]
    if not isinstance(inner, dict):
        inner = {}
        actions[0] = inner
    return inner


def _read_actions_dict(prefs: dict) -> dict[str, dict[str, Any]]:
    """Read `vivaldi.actions[0]` without materializing missing preferences."""
    vivaldi = prefs.get("vivaldi")
    if not isinstance(vivaldi, dict):
        return {}
    actions = vivaldi.get("actions")
    if not isinstance(actions, list) or not actions:
        return {}
    inner = actions[0]
    return inner if isinstance(inner, dict) else {}


def _installed_default_actions() -> dict[str, dict[str, Any]] | None:
    """Return Vivaldi's platform-specific factory action map, if available."""
    defn = _schema.lookup(_schema.load_schema(), "vivaldi.actions")
    if not isinstance(defn, dict):
        return None

    if sys.platform == "darwin":
        default_key = "default_mac"
    elif sys.platform.startswith("linux"):
        default_key = "default_linux"
    else:
        default_key = "default"

    sections = defn.get(default_key)
    if (
        not isinstance(sections, list)
        or not sections
        or not isinstance(sections[0], dict)
    ):
        return None
    actions = sections[0]
    if not all(
        isinstance(name, str) and isinstance(entry, dict)
        for name, entry in actions.items()
    ):
        return None
    return copy.deepcopy(actions)


def _materialize_actions(prefs: dict, actions: dict[str, dict[str, Any]]) -> None:
    """Write a complete action baseline into a profile being bootstrapped."""
    vivaldi = prefs.setdefault("vivaldi", {})
    if not isinstance(vivaldi, dict):
        vivaldi = {}
        prefs["vivaldi"] = vivaldi
    vivaldi["actions"] = [copy.deepcopy(actions)]


def _state_file(prefs_path: Path) -> Path:
    return prefs_path.with_name(prefs_path.name + ".dotbrowser.shortcuts.json")


def _read_state(prefs_path: Path) -> dict[str, Any]:
    state = _state_file(prefs_path)
    if not state.exists():
        return {}
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def diff_summary(
    current: dict[str, dict],
    target: dict[str, list[str]],
    removed: dict[str, list[str]],
) -> list[str]:
    lines: list[str] = []
    for name in sorted(target):
        cur_entry = current.get(name)
        cur = cur_entry.get("shortcuts", []) if isinstance(cur_entry, dict) else None
        new = target[name]
        if cur is None:
            lines.append(f"  + {name}: {new}")
        elif cur != new:
            lines.append(f"  ~ {name}: {cur} -> {new}")
    for name in sorted(removed):
        cur_entry = current.get(name) or {}
        cur = cur_entry.get("shortcuts", []) if isinstance(cur_entry, dict) else []
        original = removed[name]
        if cur != original:
            lines.append(f"  - {name}: {cur} -> {original} (restore original)")
    return lines


def _shortcut_collision_warnings(
    current: dict[str, dict],
    target: dict[str, list[str]],
    removed: dict[str, list[str]],
) -> list[str]:
    """Warn when a configured shortcut remains bound to another command.

    Vivaldi can keep ambiguous bindings in Preferences. When a user sets
    ``COMMAND_FOCUS_SEARCHFIELD = ["ctrl+s"]`` but ``COMMAND_SAVE_PAGE``
    still owns ``ctrl+s``, the file writes correctly but runtime dispatch
    may still feel wrong. We warn instead of auto-unbinding unrelated
    commands because dotbrowser only manages commands named in the config.
    """
    final: dict[str, list[str]] = {}
    for name, entry in current.items():
        if not isinstance(entry, dict):
            continue
        keys = entry.get("shortcuts", [])
        if isinstance(keys, list):
            final[name] = list(keys)
    for name, keys in removed.items():
        final[name] = list(keys)
    for name, keys in target.items():
        final[name] = list(keys)

    by_key: dict[str, list[str]] = {}
    for name, keys in final.items():
        for key in keys:
            by_key.setdefault(key, []).append(name)

    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()
    for name in sorted(target):
        for key in target[name]:
            others = sorted(cmd for cmd in by_key.get(key, []) if cmd != name)
            if not others:
                continue
            marker = (name, key)
            if marker in seen:
                continue
            seen.add(marker)
            warnings.append(
                f"warning: shortcut {key!r} is also bound to "
                f"{', '.join(others)}; Vivaldi may keep routing it there "
                f"unless you also unbind or change that command"
            )
    return warnings


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    """Compute the apply plan for a `[shortcuts]` TOML table.

    Pure: validates input, reads state + current `vivaldi.actions[0]`,
    snapshots originals for newly-managed commands, and returns a `Plan`.
    Does not write anything — the caller (unified runner) handles
    backups, kill-browser, write_atomic, state-file write, and verify.

    Reject unknown commands with a clear error rather than silently writing
    them. For a profile that has not written `vivaldi.actions` yet, load the
    installed platform-specific defaults as the authoritative catalog and
    materialize that baseline only when the plan is actually applied.
    """
    config = _validate_table(raw_table)
    current = _read_actions_dict(prefs)
    bootstrap_actions: dict[str, dict[str, Any]] | None = None

    if config and not current:
        bootstrap_actions = _installed_default_actions()
        if bootstrap_actions is None:
            sys.exit("error: " + _UNINITIALIZED_HINT)
        current = bootstrap_actions

    unknown = sorted(name for name in config if name not in current)
    if unknown:
        sys.exit(
            "error: unknown Vivaldi command(s): "
            + ", ".join(unknown)
            + "\nrun `dotbrowser vivaldi shortcuts list` to see known commands"
        )

    state = _read_state(prefs_path)
    originals: dict[str, list[str]] = dict(state.get("originals", {}))

    # Snapshot originals for any command we're newly managing. Subsequent
    # applies leave the snapshot untouched so successive overrides don't
    # erase the true original — only the first transition from "unmanaged"
    # to "managed" captures it.
    new_originals = dict(originals)
    for name in config:
        if name not in new_originals:
            cur_entry = current.get(name) or {}
            cur_shortcuts = cur_entry.get("shortcuts", []) if isinstance(cur_entry, dict) else []
            new_originals[name] = list(cur_shortcuts)

    target_names = set(config)
    previously_managed = set(originals)
    removed_names = previously_managed - target_names
    removed_with_original = {n: list(originals[n]) for n in removed_names}

    diff = diff_summary(current, config, removed_with_original)
    warnings = _shortcut_collision_warnings(current, config, removed_with_original)

    def apply_fn(prefs: dict) -> None:
        if bootstrap_actions is not None and not _read_actions_dict(prefs):
            _materialize_actions(prefs, bootstrap_actions)
        actions = _get_actions_dict(prefs)
        for name, keys in config.items():
            entry = actions.get(name)
            if not isinstance(entry, dict):
                # Unknown commands were rejected above; getting here
                # means the dict shape changed underfoot. Recreate the
                # entry rather than crashing.
                entry = {}
                actions[name] = entry
            entry["shortcuts"] = list(keys)
        for name, original in removed_with_original.items():
            entry = actions.get(name)
            if not isinstance(entry, dict):
                # Command is gone from the pref entirely (e.g. user
                # downgraded Vivaldi). Best-effort restore: skip.
                continue
            entry["shortcuts"] = list(original)

    def verify_fn(reloaded: dict) -> None:
        actions = _get_actions_dict(reloaded)
        for name, keys in config.items():
            entry = actions.get(name) or {}
            got = entry.get("shortcuts") if isinstance(entry, dict) else None
            if got != list(keys):
                sys.exit(
                    f"error: shortcuts verification failed for {name}: got {got!r}"
                )

    # Drop originals for commands that are no longer managed AND whose
    # restoration we've now scheduled — so the next apply sees a clean
    # state. Originals for still-managed commands persist.
    final_originals = {
        name: keys for name, keys in new_originals.items() if name in target_names
    }

    return Plan(
        namespace=NAMESPACE,
        diff_lines=diff,
        state_path=_state_file(prefs_path),
        state_payload={"originals": final_originals},
        apply_fn=apply_fn,
        verify_fn=verify_fn,
        warnings=warnings,
    )


def build_dump_block(
    prefs: dict,
    *,
    all_bindings: bool = False,
    header_comment: str | None = None,
) -> list[str]:
    """Pure builder for the `[shortcuts]` TOML block.

    Vivaldi has no defaults mirror, so "non-default" cannot be computed
    exactly.  The closest approximation is "commands with non-empty
    shortcuts" -- which still includes Vivaldi's compiled-in defaults.
    The export command documents this limitation in its top-of-file
    comment.
    """
    actions = _read_actions_dict(prefs)
    lines: list[str] = []
    if header_comment is not None:
        lines.append(header_comment)
    lines.append("[shortcuts]")
    for name in sorted(actions):
        entry = actions[name]
        if not isinstance(entry, dict):
            continue
        keys = entry.get("shortcuts", [])
        if not all_bindings and not keys:
            continue
        keys_repr = "[" + ", ".join(json.dumps(k) for k in keys) + "]"
        lines.append(f"{name} = {keys_repr}")
    return lines


def cmd_dump(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    prefs = load_prefs(prefs_path)

    header = "# Generated by `dotbrowser vivaldi shortcuts dump`"
    if not args.all:
        header += " (only commands with non-empty shortcuts; pass --all for every command)"
    lines = build_dump_block(prefs, all_bindings=args.all, header_comment=header)
    out = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def cmd_list(args: argparse.Namespace) -> None:
    """List COMMAND_* names known to *this* profile.

    Prefer the user's persisted `vivaldi.actions[0]`; before Vivaldi has
    materialized that preference, fall back to factory actions from the
    installed prefs schema for the same browser version.
    """
    prefs_path = find_preferences(args.profile_root, args.profile)
    prefs = load_prefs(prefs_path)
    actions = _read_actions_dict(prefs)
    if not actions:
        actions = _installed_default_actions() or {}

    needle = (args.filter or "").lower()
    rows = sorted(name for name in actions if needle in name.lower())
    for name in rows:
        print(name)
    print(f"\n{len(rows)} commands", file=sys.stderr)
    if not actions:
        print(_UNINITIALIZED_HINT, file=sys.stderr)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "shortcuts",
        help="inspect keyboard shortcuts (apply lives at `vivaldi apply`)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
Inspect Vivaldi keyboard shortcut bindings managed through [shortcuts].

Config keys are Vivaldi COMMAND_* identifiers, such as `COMMAND_CLOSE_TAB`.
Values use Vivaldi lowercase binding syntax, such as `meta+w` or
`ctrl+shift+tab`. Installed defaults seed command discovery when available.""",
        epilog="""\
Examples:
  dotbrowser vivaldi shortcuts list tab
  dotbrowser vivaldi shortcuts dump -o shortcuts.toml
  dotbrowser vivaldi shortcuts dump --all""",
    )
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    d = sub.add_parser(
        "dump",
        help="emit current shortcuts as TOML",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
Emit a [shortcuts] TOML table from Vivaldi Preferences.

Vivaldi has no stored defaults mirror, so the default output contains
commands with non-empty bindings, including compiled-in defaults. Use
`--all` to also include unbound commands.""",
        epilog="""\
Examples:
  dotbrowser vivaldi shortcuts dump
  dotbrowser vivaldi shortcuts dump --all -o all-shortcuts.toml""",
    )
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="dump every command, not just ones with bindings",
    )
    d.set_defaults(func=cmd_dump)

    l = sub.add_parser(
        "list",
        help="list command names known to this profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="""\
List Vivaldi COMMAND_* names accepted in a [shortcuts] table.

Commands come from the selected profile or, for a fresh profile, the
installed Vivaldi preference schema when it can be found.""",
        epilog="""\
Examples:
  dotbrowser vivaldi shortcuts list
  dotbrowser vivaldi shortcuts list tab""",
    )
    l.add_argument("filter", nargs="?", help="substring filter (case-insensitive)")
    l.set_defaults(func=cmd_list)
