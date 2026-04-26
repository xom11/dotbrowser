"""Manage Brave general settings via a TOML config file.

Brave stores most user prefs in the profile `Preferences` JSON. Some of
those keys are "tracked prefs" — they have a corresponding entry under
`protection.macs.<dotted_path>` and are MAC-verified at launch. Writing
a tracked pref without updating its MAC causes Brave to silently reset
it to default on next launch (and log "Pref tampered with").

For v1 we refuse to write any key that has a `protection.macs.*` entry
in the user's profile; supporting MAC-protected prefs needs a Chromium
seed + byte-exact serialization, deferred to v2.

Config schema (TOML):

    [settings]
    "brave.tabs.vertical_tabs_enabled" = true
    "bookmark_bar.show_tab_groups" = true

Keys are dotted paths into the `Preferences` JSON. Values may be any
TOML scalar or array.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from dotbrowser.brave.utils import (
    _brave_pids,
    brave_running,
    find_main_brave_cmdline,
    find_preferences,
    kill_brave_and_wait,
    load_prefs,
    restart_brave,
    write_atomic,
)

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


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    raw = data.get("settings", {})
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


def _set_managed_keys(prefs_path: Path, keys: set[str]) -> None:
    state = _state_file(prefs_path)
    state.write_text(json.dumps({"managed_keys": sorted(keys)}, indent=2))


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


def cmd_apply(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    target = load_config(args.config)

    prefs = load_prefs(prefs_path)

    # Pre-flight: refuse MAC-protected keys and the `protection` subtree.
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
            "error: the following keys cannot be written in v1:\n  "
            + "\n  ".join(rejected)
            + "\n(remove them from your config; MAC support is planned for v2)"
        )

    target_keys = set(target)
    config_managed_keys = _get_managed_keys(prefs_path)
    removed_keys = config_managed_keys - target_keys

    diff = diff_summary(prefs, target, removed_keys)
    if not diff:
        print("no changes — Preferences already match config")
        return

    print(f"target: {prefs_path}")
    print("changes:")
    print("\n".join(diff))

    if args.dry_run:
        print("\n(dry-run, nothing written)")
        return

    saved_cmdline: list[str] | None = None
    if brave_running():
        if not args.kill_brave:
            sys.exit(
                "error: Brave is running. Close it first, or pass --kill-brave\n"
                "(Brave caches prefs in memory and overwrites the file on exit,\n"
                "so editing while running is unreliable. --kill-brave SIGKILLs\n"
                "Brave to prevent the flush, applies, then restarts it.)"
            )
        saved_cmdline = find_main_brave_cmdline()
        pids = _brave_pids()
        print(f"killing Brave (pids: {' '.join(pids)})")
        kill_brave_and_wait()

    backup = prefs_path.with_suffix(
        prefs_path.suffix + f".bak.{datetime.now():%Y%m%d-%H%M%S}"
    )
    shutil.copy2(prefs_path, backup)
    print(f"backup: {backup}")

    for key, value in target.items():
        _set_value(prefs, _split_key(key), value)
    for key in removed_keys:
        _pop_value(prefs, _split_key(key))
    write_atomic(prefs_path, prefs)
    _set_managed_keys(prefs_path, target_keys)

    reloaded = load_prefs(prefs_path)
    for key, value in target.items():
        got = _get_value(reloaded, _split_key(key))
        if got != value:
            sys.exit(f"error: verification failed for key {key!r}: got {got!r}")
    print("ok — applied and verified")

    if saved_cmdline:
        used = restart_brave(saved_cmdline)
        print(f"restarting Brave: {' '.join(used)}")
    elif args.kill_brave:
        print("Brave killed; could not capture original command line — restart manually.")


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
        help="manage general Brave settings (Preferences keys without MAC protection)",
    )
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    a = sub.add_parser("apply", help="patch Preferences from a TOML config")
    a.add_argument("config", type=Path)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument(
        "--kill-brave",
        action="store_true",
        help="if Brave is running, SIGKILL it (so it can't flush in-memory "
        "prefs over our changes), apply, then restart it",
    )
    a.set_defaults(func=cmd_apply)

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
