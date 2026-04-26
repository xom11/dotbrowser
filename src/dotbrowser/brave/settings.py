"""Manage Brave general settings via a TOML config file.

Brave stores most user prefs in the profile `Preferences` JSON. Some of
those keys are "tracked prefs" — they have a corresponding entry under
`protection.macs.<dotted_path>` and are MAC-verified at launch. Writing
a tracked pref without updating its MAC causes Brave to silently reset
it to default on next launch (and log "Pref tampered with").

For v1 we refuse to write any key that has a `protection.macs.*` entry
in the user's profile; supporting MAC-protected prefs needs a Chromium
seed + byte-exact serialization, deferred to v2.

Config schema (TOML), inside the unified `brave.toml`:

    [settings]
    "brave.tabs.vertical_tabs_enabled" = true
    "bookmark_bar.show_tab_groups" = true

Keys are dotted paths into the `Preferences` JSON. Values may be any
TOML scalar or array. This module exposes:

- `plan_apply(prefs_path, prefs, raw_table)` — pure: builds a `Plan` from
  a parsed `[settings]` table. Used by the unified `brave apply` runner.
- CLI sub-action `dump` (read-only). Apply lives at the `brave` level.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotbrowser.brave.utils import (
    Plan,
    find_preferences,
    load_prefs,
)

NAMESPACE = "settings"
_MISSING = object()


def _split_key(dotted: str) -> tuple[str, ...]:
    if not dotted:
        sys.exit("error: empty key in [settings]")
    return tuple(dotted.split("."))


def _get_value(prefs: dict, parts: tuple[str, ...]) -> Any:
    cur: Any = prefs
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


def _set_value(prefs: dict, parts: tuple[str, ...], value: Any) -> None:
    cur = prefs
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _pop_value(prefs: dict, parts: tuple[str, ...]) -> None:
    """Remove the leaf at `parts`. No-op if any path component is missing.

    We do not garbage-collect empty parent dicts: Brave tolerates them
    fine, and pruning could race with siblings written by Brave itself
    between our load and our write.
    """
    cur: Any = prefs
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def _is_mac_protected(prefs: dict, parts: tuple[str, ...]) -> bool:
    """True if `protection.macs.<parts>` exists at any depth.

    Catches both the exact match (`browser.show_home_button`) and any
    parent dict whose subtree contains a tracked leaf, because writing
    the parent would clobber the tracked child and invalidate its MAC.
    """
    macs = prefs.get("protection", {}).get("macs", {})
    cur: Any = macs
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    return True


def _validate_table(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        sys.exit("error: [settings] must be a table")
    return raw


def _state_file(prefs_path: Path) -> Path:
    return prefs_path.with_name(prefs_path.name + ".dotbrowser.settings.json")


def _get_managed_keys(prefs_path: Path) -> set[str]:
    state = _state_file(prefs_path)
    if not state.exists():
        return set()
    try:
        data = json.loads(state.read_text())
    except json.JSONDecodeError:
        return set()
    return set(data.get("managed_keys", []))


def diff_summary(
    prefs: dict,
    target: dict[str, Any],
    removed_keys: set[str],
) -> list[str]:
    lines = []
    for key in sorted(target):
        parts = _split_key(key)
        cur = _get_value(prefs, parts)
        new = target[key]
        if cur is _MISSING:
            lines.append(f"  + {key} = {json.dumps(new)}")
        elif cur != new:
            lines.append(f"  ~ {key}: {json.dumps(cur)} -> {json.dumps(new)}")
    for key in sorted(removed_keys):
        parts = _split_key(key)
        cur = _get_value(prefs, parts)
        if cur is _MISSING:
            continue
        lines.append(f"  - {key}: {json.dumps(cur)} (removed)")
    return lines


def plan_apply(prefs_path: Path, prefs: dict, raw_table: object) -> Plan:
    """Compute the apply plan for a `[settings]` TOML table.

    Refuses MAC-protected keys (and the whole `protection.*` subtree)
    before producing a plan; the entire batch is rejected if any key
    is invalid, so partial application is not possible.
    """
    target = _validate_table(raw_table)

    rejected: list[str] = []
    for key in target:
        parts = _split_key(key)
        if parts[0] == "protection":
            rejected.append(f"{key} (Chromium MAC bookkeeping subtree)")
            continue
        if _is_mac_protected(prefs, parts):
            rejected.append(f"{key} (MAC-protected; writing would be reset on launch)")
    if rejected:
        sys.exit(
            "error: the following [settings] keys cannot be written in v1:\n  "
            + "\n  ".join(rejected)
            + "\n(remove them from your config; MAC support is planned for v2)"
        )

    target_keys = set(target)
    config_managed_keys = _get_managed_keys(prefs_path)
    removed_keys = config_managed_keys - target_keys

    diff = diff_summary(prefs, target, removed_keys)

    def apply_fn(prefs: dict) -> None:
        for key, value in target.items():
            _set_value(prefs, _split_key(key), value)
        for key in removed_keys:
            _pop_value(prefs, _split_key(key))

    def verify_fn(reloaded: dict) -> None:
        for key, value in target.items():
            got = _get_value(reloaded, _split_key(key))
            if got != value:
                sys.exit(f"error: settings verification failed for key {key!r}: got {got!r}")

    return Plan(
        namespace=NAMESPACE,
        diff_lines=diff,
        state_path=_state_file(prefs_path),
        state_payload={"managed_keys": sorted(target_keys)},
        apply_fn=apply_fn,
        verify_fn=verify_fn,
    )


def _format_toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_format_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        # Inline table with quoted keys (handles dotted-path keys).
        items = ", ".join(
            f"{json.dumps(k)} = {_format_toml_value(val)}" for k, val in v.items()
        )
        return "{" + items + "}"
    raise ValueError(f"unsupported value type for TOML emission: {type(v).__name__}")


def cmd_dump(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    prefs = load_prefs(prefs_path)

    if args.keys:
        keys = list(args.keys)
    else:
        keys = sorted(_get_managed_keys(prefs_path))
        if not keys:
            sys.exit(
                "error: no managed keys to dump (state file is empty).\n"
                "Pass keys explicitly: `dump brave.tabs.vertical_tabs_enabled ...`"
            )

    lines = ["# Generated by `dotbrowser brave settings dump`", "[settings]"]
    missing: list[str] = []
    for key in keys:
        val = _get_value(prefs, _split_key(key))
        if val is _MISSING:
            missing.append(key)
            continue
        lines.append(f"{json.dumps(key)} = {_format_toml_value(val)}")
    if missing:
        lines.append("")
        lines.append("# keys not present in Preferences:")
        for k in missing:
            lines.append(f"#   {k}")
    out = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "settings",
        help="inspect general settings (apply lives at `brave apply`)",
    )
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    d = sub.add_parser(
        "dump",
        help="emit current values as TOML — managed keys (default) or specific keys",
    )
    d.add_argument(
        "keys",
        nargs="*",
        help="dotted-path keys to dump; defaults to currently-managed keys",
    )
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.set_defaults(func=cmd_dump)
