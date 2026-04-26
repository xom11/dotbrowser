"""Manage Brave custom keyboard shortcuts via a TOML config file.

Brave stores user-overridden accelerators in its profile `Preferences` JSON
under the `brave.accelerators` key. The values are NOT in the protected
`Secure Preferences` file, so direct patching does not trip MAC integrity
checks (verified against brave/brave-core source).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from dotbrowser.brave.command_ids import ID_TO_NAME, NAME_TO_ID

ACCELERATORS_KEY_PATH = ("brave", "accelerators")
DEFAULT_ACCELERATORS_KEY_PATH = ("brave", "default_accelerators")


def find_preferences(profile_root: Path, profile: str) -> Path:
    p = profile_root / profile / "Preferences"
    if not p.exists():
        sys.exit(f"error: Preferences not found at {p}")
    return p


def brave_running() -> bool:
    try:
        subprocess.check_output(["pgrep", "-x", "brave"], stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_prefs(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def get_nested(d: dict, keys: tuple[str, ...]) -> dict:
    for k in keys:
        d = d.setdefault(k, {})
    return d


def load_config(path: Path) -> dict[str, list[str]]:
    with path.open("rb") as f:
        data = tomllib.load(f)
    raw = data.get("shortcuts", {})
    if not isinstance(raw, dict):
        sys.exit("error: [shortcuts] must be a table")
    out: dict[str, list[str]] = {}
    for name, value in raw.items():
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            sys.exit(f"error: shortcuts.{name} must be a list of strings")
        out[name] = value
    return out


def resolve_command_ids(shortcuts: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    unknown = []
    for name, keys in shortcuts.items():
        if name not in NAME_TO_ID:
            unknown.append(name)
            continue
        out[str(NAME_TO_ID[name])] = keys
    if unknown:
        sys.exit(
            "error: unknown command name(s): "
            + ", ".join(sorted(unknown))
            + "\nrun `dotbrowser brave shortcuts list` to see all known commands"
        )
    return out


def diff_summary(
    current: dict, target: dict, removed_ids: set[str]
) -> list[str]:
    lines = []
    for cid, keys in target.items():
        name = ID_TO_NAME.get(int(cid), f"<unknown:{cid}>")
        if cid not in current:
            lines.append(f"  + {name}: {keys}")
        elif current[cid] != keys:
            lines.append(f"  ~ {name}: {current[cid]} -> {keys}")
    for cid in removed_ids:
        name = ID_TO_NAME.get(int(cid), f"<unknown:{cid}>")
        lines.append(f"  - {name}: {current[cid]} (reset to default)")
    return lines


def write_atomic(path: Path, prefs: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(prefs, f, separators=(",", ":"))
    os.replace(tmp, path)


def _state_file(prefs_path: Path) -> Path:
    return prefs_path.with_name(prefs_path.name + ".dotbrowser.shortcuts.json")


def _get_managed_ids(prefs_path: Path) -> set[str]:
    state = _state_file(prefs_path)
    if not state.exists():
        return set()
    try:
        data = json.loads(state.read_text())
    except json.JSONDecodeError:
        return set()
    return set(data.get("managed_ids", []))


def _set_managed_ids(prefs_path: Path, ids: set[str]) -> None:
    state = _state_file(prefs_path)
    state.write_text(json.dumps({"managed_ids": sorted(ids, key=int)}, indent=2))


def cmd_apply(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    config = load_config(args.config)
    target = resolve_command_ids(config)

    prefs = load_prefs(prefs_path)
    current = dict(get_nested(prefs, ACCELERATORS_KEY_PATH))
    defaults = dict(get_nested(prefs, DEFAULT_ACCELERATORS_KEY_PATH))

    target_ids = set(target)
    config_managed_ids = _get_managed_ids(prefs_path)
    removed_ids = {cid for cid in (config_managed_ids - target_ids) if cid in current}

    diff = diff_summary(current, target, removed_ids)
    if not diff:
        print("no changes — Preferences already match config")
        return

    print(f"target: {prefs_path}")
    print("changes:")
    print("\n".join(diff))

    if args.dry_run:
        print("\n(dry-run, nothing written)")
        return

    if brave_running() and not args.force:
        sys.exit(
            "error: Brave is running. Close it first, or pass --force to risk "
            "having Brave overwrite changes on exit."
        )

    backup = prefs_path.with_suffix(
        prefs_path.suffix + f".bak.{datetime.now():%Y%m%d-%H%M%S}"
    )
    shutil.copy2(prefs_path, backup)
    print(f"backup: {backup}")

    accels = get_nested(prefs, ACCELERATORS_KEY_PATH)
    for cid, keys in target.items():
        accels[cid] = keys
    for cid in removed_ids:
        if cid in defaults:
            accels[cid] = list(defaults[cid])
        else:
            accels.pop(cid, None)
    write_atomic(prefs_path, prefs)
    _set_managed_ids(prefs_path, target_ids)

    reloaded = load_prefs(prefs_path)
    reloaded_accels = get_nested(reloaded, ACCELERATORS_KEY_PATH)
    for cid, keys in target.items():
        if reloaded_accels.get(cid) != keys:
            sys.exit(f"error: verification failed for command {cid}")
    print("ok — applied and verified")


def cmd_dump(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    prefs = load_prefs(prefs_path)
    accels = get_nested(prefs, ACCELERATORS_KEY_PATH)
    defaults = get_nested(prefs, DEFAULT_ACCELERATORS_KEY_PATH)

    header = "# Generated by `dotbrowser brave shortcuts dump`"
    if not args.all:
        header += " (only entries that differ from defaults; pass --all to see every binding)"
    lines = [header, "[shortcuts]"]
    unknown_lines = []
    for cid, keys in sorted(accels.items(), key=lambda kv: int(kv[0])):
        if not args.all and defaults.get(cid) == keys:
            continue
        name = ID_TO_NAME.get(int(cid))
        keys_repr = "[" + ", ".join(json.dumps(k) for k in keys) + "]"
        if name:
            lines.append(f"{name} = {keys_repr}")
        else:
            unknown_lines.append(f"# unknown command id {cid} = {keys_repr}")
    if unknown_lines:
        lines.append("")
        lines.append("# IDs not recognized by this tool's mapping:")
        lines.extend(unknown_lines)
    out = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(out)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(out)


def cmd_list(args: argparse.Namespace) -> None:
    needle = (args.filter or "").lower()
    rows = [
        (name, cid)
        for name, cid in sorted(NAME_TO_ID.items())
        if needle in name
    ]
    width = max((len(n) for n, _ in rows), default=0)
    for name, cid in rows:
        print(f"{name:<{width}}  {cid}")
    print(f"\n{len(rows)} commands", file=sys.stderr)


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("shortcuts", help="manage keyboard shortcuts")
    sub = p.add_subparsers(dest="action", required=True, metavar="ACTION")

    a = sub.add_parser("apply", help="patch Preferences from a TOML config")
    a.add_argument("config", type=Path)
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--force", action="store_true", help="apply even if Brave is running")
    a.set_defaults(func=cmd_apply)

    d = sub.add_parser("dump", help="emit current shortcuts as TOML")
    d.add_argument("-o", "--output", help="write to file instead of stdout")
    d.add_argument(
        "--all",
        action="store_true",
        help="dump every binding, not just user-customized ones",
    )
    d.set_defaults(func=cmd_dump)

    l = sub.add_parser("list", help="list known command names")
    l.add_argument("filter", nargs="?", help="substring filter")
    l.set_defaults(func=cmd_list)
