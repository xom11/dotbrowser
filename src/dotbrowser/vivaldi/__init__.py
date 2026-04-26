"""Vivaldi-browser-specific subcommands.

Top-level CLI shape (mirrors `dotbrowser brave`):

    dotbrowser vivaldi [--profile-root ...] [--profile ...] <ACTION> ...

Where <ACTION> is one of:
- `apply <file>` — unified apply for `[shortcuts]`, `[settings]` and
  `[pwa]` tables in a single TOML file. One kill-browser + backup +
  write_atomic cycle covers all three modules.
- `shortcuts dump|list` — read-only inspection (delegated to shortcuts).
- `settings dump` — read-only inspection (delegated to settings).
- `pwa dump` — read-only inspection (delegated to pwa).

Same orchestration model as the Brave package: each module exposes a
pure `plan_apply()` returning a `Plan`; this module collects plans,
prints the combined diff, runs a single I/O cycle (sudo preflight →
kill → backup → write_atomic → external apply → state sidecars →
verify → restart), and never partially writes if any module rejects.

TOML table semantics (missing-skips, empty-wipes) match Brave verbatim;
the only browser-specific knobs are profile path, process name, and the
PWA policy-file location (handled inside the respective modules).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from dotbrowser.vivaldi import pwa as pwa_mod
from dotbrowser.vivaldi import settings as settings_mod
from dotbrowser.vivaldi import shortcuts as shortcuts_mod
from dotbrowser.vivaldi.utils import (
    Plan,
    _vivaldi_pids,
    find_main_vivaldi_cmdline,
    find_preferences,
    kill_vivaldi_and_wait,
    load_prefs,
    restart_vivaldi,
    vivaldi_running,
    write_atomic,
)


def _default_profile_root() -> Path | None:
    """Vivaldi's profile root, per platform.

    Returns None for unsupported platforms; the CLI then requires
    --profile-root to be passed explicitly so that --help still works
    without crashing at import time.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Vivaldi"
    if sys.platform.startswith("linux"):
        return home / ".config" / "vivaldi"
    return None


DEFAULT_PROFILE_ROOT = _default_profile_root()


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _looks_like_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _load_toml_from_url(url: str) -> dict:
    """Fetch a TOML config over HTTP(S) and parse it.

    Prints the URL, byte size, and SHA-256 of the fetched payload so
    the user can verify exactly what's about to be applied (the same
    config can change between fetches if it points at a moving branch).
    """
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
    except urllib.error.URLError as e:
        sys.exit(f"error: failed to fetch {url}: {e.reason}")
    print(f"source: {url}")
    print(f"  size:   {len(data)} bytes")
    print(f"  sha256: {hashlib.sha256(data).hexdigest()}")
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        sys.exit(f"error: failed to parse TOML from {url}: {e}")


def _build_plans(prefs_path: Path, prefs: dict, doc: dict) -> list[Plan]:
    """Ask each module for a Plan. Missing tables = skip entirely."""
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
    if pwa_mod.NAMESPACE in doc:
        plans.append(
            pwa_mod.plan_apply(
                prefs_path, prefs, doc[pwa_mod.NAMESPACE]
            )
        )
    return plans


def cmd_apply(args: argparse.Namespace) -> None:
    prefs_path = find_preferences(args.profile_root, args.profile)
    src = args.config
    if _looks_like_url(src):
        doc = _load_toml_from_url(str(src))
    else:
        doc = _load_toml(Path(src))
    if not isinstance(doc, dict):
        sys.exit("error: TOML root must be a table")

    prefs = load_prefs(prefs_path)
    plans = _build_plans(prefs_path, prefs, doc)

    if not plans:
        sys.exit(
            "error: config has no [shortcuts], [settings] or [pwa] table — nothing to apply"
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

    # Sudo preflight before kill-browser. Same two-step probe as the
    # Brave orchestrator: silent fast path via `sudo -n true` (works for
    # NOPASSWD users / warm credential caches) and an interactive
    # fallback via `sudo -v` when that fails. See brave/__init__.py for
    # why `sudo -nv` is unsafe.
    needs_sudo = any(
        p.external_apply_fn is not None and not p.empty for p in plans
    )
    if needs_sudo:
        cached = subprocess.run(
            ["sudo", "-n", "true"], stderr=subprocess.DEVNULL
        ).returncode == 0
        if not cached:
            try:
                subprocess.run(["sudo", "-v"], check=True)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                sys.exit(
                    "error: [pwa] requires sudo to write the managed-policy "
                    f"file but auth failed: {e}\n"
                    "(if running non-interactively, run `sudo -v` from a "
                    "terminal first to cache credentials)"
                )

    saved_cmdline: list[str] | None = None
    vivaldi_was_killed = False
    if vivaldi_running():
        if not args.kill_browser:
            sys.exit(
                "error: Vivaldi is running. Close it first, or pass --kill-browser\n"
                "(Vivaldi caches prefs in memory and overwrites the file on exit,\n"
                "so editing while running is unreliable. --kill-browser SIGKILLs\n"
                "Vivaldi to prevent the flush, applies, then restarts it.)"
            )
        saved_cmdline = find_main_vivaldi_cmdline()
        pids = _vivaldi_pids()
        print(f"killing Vivaldi (pids: {' '.join(pids)})")
        kill_vivaldi_and_wait()
        vivaldi_was_killed = True

    backup = prefs_path.with_suffix(
        prefs_path.suffix + f".bak.{datetime.now():%Y%m%d-%H%M%S}"
    )
    shutil.copy2(prefs_path, backup)
    print(f"backup: {backup}")

    for plan in plans:
        plan.apply_fn(prefs)
    write_atomic(prefs_path, prefs)

    for plan in plans:
        if plan.external_apply_fn is not None:
            plan.external_apply_fn()

    for plan in plans:
        if plan.state_path is not None:
            plan.state_path.write_text(json.dumps(plan.state_payload, indent=2))

    reloaded = load_prefs(prefs_path)
    for plan in plans:
        plan.verify_fn(reloaded)
    print("ok — applied and verified")

    if saved_cmdline:
        used = restart_vivaldi(saved_cmdline)
        print(f"restarting Vivaldi: {' '.join(used)}")
    elif vivaldi_was_killed:
        print("Vivaldi killed; could not capture original command line — restart manually.")


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("vivaldi", help="Vivaldi browser commands")
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
        help="apply [shortcuts], [settings] and [pwa] tables from a TOML config",
    )
    a.add_argument(
        "config",
        help="path to a local TOML file, or http(s):// URL to fetch one",
    )
    a.add_argument("-n", "--dry-run", action="store_true")
    a.add_argument(
        "-k",
        "--kill-browser",
        action="store_true",
        help="if Vivaldi is running, SIGKILL it (so it can't flush in-memory "
        "prefs over our changes), apply, then restart it",
    )
    a.set_defaults(func=cmd_apply)

    shortcuts_mod.register(sub)
    settings_mod.register(sub)
    pwa_mod.register(sub)
