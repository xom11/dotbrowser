"""Shared orchestration logic for all browsers.

Handles TOML loading (file + URL), the unified ``apply`` cycle
(preflight -> kill -> backup -> write -> verify -> restart), the
``init`` command, and the ``register()`` argparse setup.

Process-interaction callbacks (running, kill, restart) are passed in
from each browser module so that tests can monkeypatch them at the
browser-module level.
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
from typing import Callable

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from dotbrowser._base.utils import Plan, find_preferences, load_prefs, write_atomic


_MAX_URL_CONFIG_BYTES = 256 * 1024


def _load_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _looks_like_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _load_toml_from_url(
    url: str,
    *,
    allow_http: bool = False,
    expect_sha256: str | None = None,
) -> dict:
    if url.startswith("http://") and not allow_http:
        sys.exit(
            f"error: refusing to fetch config over plain http: {url}\n"
            "  HTTP responses can be modified by anyone on the network and "
            "could inject\n"
            "  a malicious [pwa] table that runs through sudo. Use https:// "
            "or pass\n"
            "  --allow-http to opt in (e.g. for a trusted intranet host)."
        )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read(_MAX_URL_CONFIG_BYTES + 1)
    except urllib.error.URLError as e:
        sys.exit(f"error: failed to fetch {url}: {e.reason}")
    if len(data) > _MAX_URL_CONFIG_BYTES:
        sys.exit(
            f"error: config from {url} exceeds the {_MAX_URL_CONFIG_BYTES}-byte "
            f"limit. If this is intentional, fetch the file locally and pass "
            f"the path instead."
        )
    digest = hashlib.sha256(data).hexdigest()
    print(f"source: {url}")
    print(f"  size:   {len(data)} bytes")
    print(f"  sha256: {digest}")
    if expect_sha256 is not None:
        want = expect_sha256.strip().lower()
        if digest != want:
            sys.exit(
                f"error: sha256 mismatch for {url}\n"
                f"  expected: {want}\n"
                f"  got:      {digest}\n"
                "  refusing to apply -- the file may have changed or been "
                "tampered with."
            )
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        sys.exit(f"error: failed to parse TOML from {url}: {e}")


def load_toml_source(
    src: str,
    *,
    allow_http: bool = False,
    expect_sha256: str | None = None,
) -> dict:
    """Load a TOML config from a file path or URL."""
    if _looks_like_url(src):
        return _load_toml_from_url(
            src, allow_http=allow_http, expect_sha256=expect_sha256
        )
    return _load_toml(Path(src))


def cmd_apply(
    args: argparse.Namespace,
    *,
    display_name: str,
    running_fn: Callable[[], bool],
    pids_fn: Callable[[], list[str]],
    find_cmdline_fn: Callable[[], list[str] | None],
    kill_fn: Callable[[], None],
    restart_fn: Callable[[list[str]], list[str]],
    build_plans_fn: Callable,
) -> None:
    """Unified apply orchestrator.

    Process callbacks are resolved at call time in each browser's
    ``cmd_apply`` wrapper, so test monkeypatching of the browser
    module's function names takes effect.
    """
    prefs_path = find_preferences(args.profile_root, args.profile)
    doc = load_toml_source(
        args.config,
        allow_http=getattr(args, "allow_http", False),
        expect_sha256=getattr(args, "expect_sha256", None),
    )
    if not isinstance(doc, dict):
        sys.exit("error: TOML root must be a table")

    prefs = load_prefs(prefs_path)
    plans = build_plans_fn(prefs_path, prefs, doc)

    if not plans:
        sys.exit(
            "error: config has no [shortcuts], [settings] or [pwa] table "
            "-- nothing to apply"
        )

    non_empty = [p for p in plans if not p.empty]
    if not non_empty:
        print("no changes -- Preferences already match config")
        return

    print(f"target: {prefs_path}")
    for plan in plans:
        for warning in plan.warnings:
            print(warning)
    for plan in non_empty:
        print(f"{plan.namespace}:")
        print("\n".join(plan.diff_lines))

    if args.dry_run:
        print("\n(dry-run, nothing written)")
        return

    needs_escalation = any(
        p.external_apply_fn is not None and not p.empty for p in plans
    )
    if needs_escalation:
        if sys.platform == "win32":
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():
                sys.exit(
                    "error: [pwa] requires administrator privileges to write "
                    "to the Windows Registry.\n"
                    "Re-run this command from an elevated (Administrator) "
                    "command prompt or PowerShell."
                )
        else:
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
    was_killed = False
    if running_fn():
        if not args.kill_browser:
            sys.exit(
                f"error: {display_name} is running. Close it first, "
                f"or pass --kill-browser\n"
                f"({display_name} caches prefs in memory and overwrites "
                f"the file on exit,\nso editing while running is unreliable. "
                f"--kill-browser force-kills\n{display_name} to prevent "
                f"the flush, applies, then restarts it.)"
            )
        saved_cmdline = find_cmdline_fn()
        pid_list = pids_fn()
        print(f"killing {display_name} (pids: {' '.join(pid_list)})")
        kill_fn()
        was_killed = True

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
    print("ok -- applied and verified")

    if saved_cmdline:
        used = restart_fn(saved_cmdline)
        print(f"restarting {display_name}: {' '.join(used)}")
    elif was_killed:
        print(
            f"{display_name} killed; could not capture original "
            f"command line -- restart manually."
        )


def cmd_init(args: argparse.Namespace, browser_name: str, template: str) -> None:
    filename = args.output or f"{browser_name}.toml"
    text = template.replace("{filename}", filename)
    if args.output:
        dest = Path(args.output)
        if dest.exists():
            sys.exit(f"error: {dest} already exists -- refusing to overwrite")
        dest.write_text(text, encoding="utf-8")
        print(f"wrote {dest}")
    else:
        sys.stdout.write(text)


def register_browser(
    subparsers: argparse._SubParsersAction,
    *,
    name: str,
    help_text: str,
    default_profile_root: Path | None,
    cmd_apply_fn,
    cmd_init_fn=None,
    module_registers: list,
    setup_profile_args: Callable[[argparse.ArgumentParser], None] | None = None,
    normalize_args: Callable[[argparse.Namespace], None] | None = None,
) -> None:
    """Register a browser's full CLI subtree.

    ``setup_profile_args``, if provided, fully replaces the default
    ``--profile-root`` / ``--profile`` setup so a browser can add
    related flags (e.g. ``--channel`` for Brave's release channels) and
    defer profile-root resolution to runtime.
    """
    p = subparsers.add_parser(name, help=help_text)

    if normalize_args is not None:
        # Propagates onto every subcommand's namespace so cli.main()
        # can run it before dispatch.
        p.set_defaults(_normalize_args=normalize_args)

    if setup_profile_args is not None:
        setup_profile_args(p)
    elif default_profile_root is not None:
        p.add_argument(
            "-r",
            "--profile-root",
            type=Path,
            default=default_profile_root,
            help=f"default: {default_profile_root}",
        )
        p.add_argument(
            "-p",
            "--profile",
            default="Default",
            help="profile dir name (default: Default)",
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

    if cmd_init_fn is not None:
        i = sub.add_parser("init", help="scaffold a starter TOML config")
        i.add_argument(
            "-o",
            "--output",
            metavar="FILE",
            help="write to FILE instead of stdout",
        )
        i.set_defaults(func=cmd_init_fn)

    a = sub.add_parser(
        "apply",
        help="apply [shortcuts], [settings] and [pwa] tables from a TOML config",
    )
    a.add_argument(
        "config",
        help="path to a local TOML file, or https:// URL to fetch one "
        "(http:// is refused unless --allow-http is set)",
    )
    a.add_argument(
        "--expect-sha256",
        metavar="HEX",
        default=None,
        help="when fetching a URL, refuse to apply unless the response sha256 "
        "matches this hex digest",
    )
    a.add_argument(
        "--allow-http",
        action="store_true",
        help="allow fetching configs over plain http:// (NOT recommended; the "
        "response can be modified in transit and a malicious [pwa] table "
        "would run through sudo)",
    )
    a.add_argument("-n", "--dry-run", action="store_true")
    a.add_argument(
        "-k",
        "--kill-browser",
        action="store_true",
        help=f"if {name} is running, force-kill it (so it can't flush in-memory "
        "prefs over our changes), apply, then restart it",
    )
    a.set_defaults(func=cmd_apply_fn)

    for mod_register in module_registers:
        mod_register(sub)
