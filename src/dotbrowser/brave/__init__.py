"""Brave-browser-specific subcommands.

Top-level CLI shape:

    dotbrowser brave [--profile-root ...] [--profile ...] <ACTION> ...

Where <ACTION> is one of:
- `apply <file>` — unified apply for `[shortcuts]`, `[settings]` and
  `[pwa]` tables in a single TOML file (this module). One kill-browser
  + backup + write_atomic cycle covers all three modules.
- `shortcuts dump|list` — read-only inspection (delegated to shortcuts).
- `settings dump` — read-only inspection (delegated to settings).
- `pwa dump` — read-only inspection (delegated to pwa).

Each module exposes a pure `plan_apply()` that returns a `Plan`
(see utils.py). This module is the orchestrator: it loads `Preferences`
once, asks each module to plan its changes, prints the combined diff,
and runs a single I/O cycle. Modules that own external state (pwa
writes Brave's managed-policy file under /etc/) hook in via the
`external_apply_fn` field on `Plan`, which fires after `write_atomic`.

TOML semantics:
- Missing table (no `[shortcuts]` header)  → that module is skipped
  entirely. State file untouched. Safe default for users who only
  manage a subset of namespaces.
- Empty table (`[settings]` with no entries) → all previously managed
  keys are reset (popped / reverted to default). State file becomes
  empty. This is the explicit "wipe my managed entries" gesture.
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

from dotbrowser.brave import pwa as pwa_mod
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

    Linux probes both the direct-install location (.deb / .rpm / pacman
    / nix all share `~/.config/BraveSoftware/Brave-Browser`) and the
    Snap location (`~/snap/brave/current/.config/...`). Direct install
    wins when both have data — that matches what `which brave-browser`
    resolves to on a dual-install machine. The probe key is `Local
    State`: Chromium creates it on first launch, so its presence is a
    reliable "this profile has been used" signal independent of which
    `--profile` directory the user later targets. Flatpak Brave is not
    auto-detected because its sandbox also breaks our process-detection
    and restart paths — pass `--profile-root` if you want to try it.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    if sys.platform.startswith("linux"):
        candidates = (
            home / ".config" / "BraveSoftware" / "Brave-Browser",
            home / "snap" / "brave" / "current" / ".config" / "BraveSoftware" / "Brave-Browser",
        )
        for c in candidates:
            if (c / "Local State").exists():
                return c
        return candidates[0]
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

    # Preflight: any plan that escalates (currently only [pwa], which
    # sudo-writes Brave's managed-policy file) must succeed at sudo
    # *before* we kill Brave or back up Preferences. Otherwise an auth
    # failure deep in apply would leave the user with a killed browser
    # and a half-applied config.
    #
    # Two-step probe so the user gets the right behaviour in every
    # context: `sudo -n true` is the silent fast path that succeeds for
    # NOPASSWD users and warm credential caches without ever touching a
    # TTY. Only on its failure do we fall back to `sudo -v`, which will
    # prompt for a password — but only if there's a real terminal to
    # prompt on. The interactive fallback is needed because `sudo -nv`
    # mis-handles NOPASSWD on sudoers configs that set `Defaults use_pty`
    # (sudo treats `-v` as a forced reauthentication regardless of
    # NOPASSWD), so `-n true` is the only reliable "would sudo work?"
    # signal we can use here.
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
    brave_was_killed = False
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
        brave_was_killed = True

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

    # External side effects (e.g. pwa's sudo policy-file write) run
    # after Preferences are durable on disk. If one of these fails the
    # prefs side is at least consistent with itself; we error out below.
    for plan in plans:
        if plan.external_apply_fn is not None:
            plan.external_apply_fn()

    # State sidecars are written AFTER Preferences so a crash mid-apply
    # doesn't claim ownership of keys we failed to write. Modules with
    # their own external persistence (pwa) leave state_path as None.
    for plan in plans:
        if plan.state_path is not None:
            plan.state_path.write_text(json.dumps(plan.state_payload, indent=2))

    reloaded = load_prefs(prefs_path)
    for plan in plans:
        plan.verify_fn(reloaded)
    print("ok — applied and verified")

    if saved_cmdline:
        used = restart_brave(saved_cmdline)
        print(f"restarting Brave: {' '.join(used)}")
    elif brave_was_killed:
        # Killed but couldn't capture cmdline (rare race in find_main_brave_cmdline).
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
        help="if Brave is running, SIGKILL it (so it can't flush in-memory "
        "prefs over our changes), apply, then restart it",
    )
    a.set_defaults(func=cmd_apply)

    shortcuts_mod.register(sub)
    settings_mod.register(sub)
    pwa_mod.register(sub)
