"""Brave-browser-specific subcommands.

Top-level CLI shape:

    dotbrowser brave [--profile-root ...] [--profile ...] <ACTION> ...

Where <ACTION> is one of:
- `apply <file>` — unified apply for `[shortcuts]` and `[settings]`
  tables in a single TOML file (this module). Single kill-browser +
  backup + write_atomic cycle covers both modules.
- `shortcuts dump|list` — read-only inspection (delegated to shortcuts).
- `settings dump` — read-only inspection (delegated to settings).

The two modules each expose a pure `plan_apply()` that returns a
`Plan` (see utils.py). This module is the orchestrator: it loads
`Preferences` once, asks each module to plan its changes, prints the
combined diff, and runs a single I/O cycle.

TOML semantics:
- Missing table (no `[shortcuts]` header)  → that module is skipped
  entirely. State file untouched. Safe default for users who only
  manage one of the two namespaces.
- Empty table (`[settings]` with no entries) → all previously managed
  keys are reset (popped / reverted to default). State file becomes
  empty. This is the explicit "wipe my managed entries" gesture.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from dotbrowser.brave import settings as settings_mod
from dotbrowser.brave import shortcuts as shortcuts_mod
from dotbrowser.brave.utils import (
    Plan,
    _brave_pids,
    brave_running,
    find_main_brave_cmdline,
    find_preferences,
    kill_brave_and_wait,
    load_prefs,
    restart_brave,
    write_atomic,
)


def _default_profile_root() -> Path | None:
    """Brave's profile root, per platform.

    Returns None for unsupported platforms; the CLI then requires
    --profile-root to be passed explicitly so that --help still works
    on Windows / BSD / etc. without crashing at import time.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("linux"):
        return home / ".config" / "BraveSoftware" / "Brave-Browser"
    return None


DEFAULT_PROFILE_ROOT = _default_profile_root()


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _build_plans(prefs_path: Path, prefs: dict, doc: dict) -> list[Plan]:
    """Ask each module for a Plan. Missing tables = skip entirely.

    Tables are matched by their key name in the parsed TOML. Empty
    tables still produce a Plan (which will reset all managed entries).
    """
    plans: list[Plan] = []
    if shortcuts_mod.NAMESPACE in doc:
        plans.append(
            shortcuts_mod.plan_apply(
                prefs_path, prefs, doc[shortcuts_mod.NAMESPACE]
            )
        )
    if settings_mod.NAMESPACE in doc:
        plans.append(
            settings_mod.plan_apply(
                prefs_path, prefs, doc[settings_mod.NAMESPACE]
            )
        )
    return plans


def cmd_apply(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    doc = _load_toml(args.config)
    if not isinstance(doc, dict):
        sys.exit("error: TOML root must be a table")

    prefs = load_prefs(prefs_path)
    plans = _build_plans(prefs_path, prefs, doc)

    if not plans:
        sys.exit(
            "error: config has no [shortcuts] or [settings] table — nothing to apply"
        )

    non_empty = [p for p in plans if not p.empty]
    if not non_empty:
        print("no changes — Preferences already match config")
        return

    print(f"target: {prefs_path}")
    for plan in non_empty:
        print(f"{plan.namespace}:")
        print("\n".join(plan.diff_lines))

    if args.dry_run:
        print("\n(dry-run, nothing written)")
        return

    saved_cmdline: list[str] | None = None
    if brave_running():
        if not args.kill_browser:
            sys.exit(
                "error: Brave is running. Close it first, or pass --kill-browser\n"
                "(Brave caches prefs in memory and overwrites the file on exit,\n"
                "so editing while running is unreliable. --kill-browser SIGKILLs\n"
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

    # Apply ALL mutations to the in-memory dict before a single write,
    # so a crash between modules can't leave Preferences half-updated.
    for plan in plans:
        plan.apply_fn(prefs)
    write_atomic(prefs_path, prefs)

    # State sidecars are written AFTER Preferences so a crash mid-apply
    # doesn't claim ownership of keys we failed to write.
    for plan in plans:
        plan.state_path.write_text(json.dumps(plan.state_payload, indent=2))

    reloaded = load_prefs(prefs_path)
    for plan in plans:
        plan.verify_fn(reloaded)
    print("ok — applied and verified")

    if saved_cmdline:
        used = restart_brave(saved_cmdline)
        print(f"restarting Brave: {' '.join(used)}")
    elif args.kill_browser:
        print("Brave killed; could not capture original command line — restart manually.")


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("brave", help="Brave browser commands")
    if DEFAULT_PROFILE_ROOT is not None:
        p.add_argument(
            "-r",
            "--profile-root",
            type=Path,
            default=DEFAULT_PROFILE_ROOT,
            help=f"default: {DEFAULT_PROFILE_ROOT}",
        )
    else:
        p.add_argument(
            "-r",
            "--profile-root",
            type=Path,
            required=True,
            help=f"required (no default for platform {sys.platform!r})",
        )
    p.add_argument(
        "-p",
        "--profile",
        default="Default",
        help="profile dir name (default: Default)",
    )
    sub = p.add_subparsers(dest="module", required=True, metavar="ACTION")

    a = sub.add_parser(
        "apply",
        help="apply [shortcuts] and [settings] tables from a TOML config",
    )
    a.add_argument("config", type=Path)
    a.add_argument("-n", "--dry-run", action="store_true")
    a.add_argument(
        "-k",
        "--kill-browser",
        action="store_true",
        help="if Brave is running, SIGKILL it (so it can't flush in-memory "
        "prefs over our changes), apply, then restart it",
    )
    a.set_defaults(func=cmd_apply)

    shortcuts_mod.register(sub)
    settings_mod.register(sub)
