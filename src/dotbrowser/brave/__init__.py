"""Brave-browser-specific subcommands.

Top-level CLI shape:

    dotbrowser brave [--profile-root ...] [--profile ...] <ACTION> ...

Where <ACTION> is one of:
- ``init`` -- scaffold a commented starter TOML config.
- ``apply <file>`` -- unified apply for ``[shortcuts]``, ``[settings]``
  and ``[pwa]`` tables in a single TOML file.
- ``shortcuts dump|list`` -- read-only inspection (delegated to shortcuts).
- ``settings dump`` -- read-only inspection (delegated to settings).
- ``pwa dump`` -- read-only inspection (delegated to pwa).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotbrowser._base.orchestrator import (
    cmd_apply as _base_cmd_apply,
    cmd_export as _base_cmd_export,
    cmd_init as _base_cmd_init,
    cmd_restore as _base_cmd_restore,
    register_browser,
)
from dotbrowser._base.utils import Plan
from dotbrowser.brave import pwa as pwa_mod
from dotbrowser.brave import settings as settings_mod
from dotbrowser.brave import shortcuts as shortcuts_mod
from dotbrowser.brave.utils import (  # noqa: F401
    BROWSER_PROCESS,
    _brave_pids,
    brave_running,
    find_main_brave_cmdline,
    kill_brave_and_wait,
    restart_brave,
)


CHANNELS = ("stable", "beta", "nightly")

# Path-suffix Brave appends to the Brave-Browser directory name for
# beta/nightly channels.  Same on every OS.
_CHANNEL_DIR_SUFFIX = {"stable": "", "beta": "-Beta", "nightly": "-Nightly"}


def _default_profile_root(channel: str = "stable") -> Path | None:
    """Brave's profile root, per platform and channel.

    Returns None for unsupported platforms; the CLI then requires
    --profile-root to be passed explicitly so that --help still works
    on BSD / etc. without crashing at import time.

    Snap and Flatpak only ship a stable channel (verified against
    Brave's official packaging), so non-stable channels probe only
    the direct-install path.
    """
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel: {channel!r}")
    suffix = _CHANNEL_DIR_SUFFIX[channel]
    home = Path.home()
    if sys.platform == "darwin":
        return (
            home / "Library" / "Application Support" / "BraveSoftware"
            / f"Brave-Browser{suffix}"
        )
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = (
                Path(local_app_data) / "BraveSoftware"
                / f"Brave-Browser{suffix}" / "User Data"
            )
            return candidate
        return None
    if sys.platform.startswith("linux"):
        direct = home / ".config" / "BraveSoftware" / f"Brave-Browser{suffix}"
        if channel != "stable":
            # Snap/Flatpak only ship stable.
            return direct
        candidates = (
            direct,
            home / "snap" / "brave" / "current" / ".config" / "BraveSoftware" / "Brave-Browser",
            home / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser",
        )
        for c in candidates:
            if (c / "Local State").exists():
                return c
        return candidates[0]
    return None


DEFAULT_PROFILE_ROOT = _default_profile_root()


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

def _build_plans(prefs_path: Path, prefs: dict, doc: dict) -> list[Plan]:
    plans: list[Plan] = []
    if shortcuts_mod.NAMESPACE in doc:
        plans.append(
            shortcuts_mod.plan_apply(prefs_path, prefs, doc[shortcuts_mod.NAMESPACE])
        )
    if settings_mod.NAMESPACE in doc:
        plans.append(
            settings_mod.plan_apply(prefs_path, prefs, doc[settings_mod.NAMESPACE])
        )
    if pwa_mod.NAMESPACE in doc:
        plans.append(
            pwa_mod.plan_apply(prefs_path, prefs, doc[pwa_mod.NAMESPACE])
        )
    return plans


# ---------------------------------------------------------------------------
# Init template
# ---------------------------------------------------------------------------

_INIT_TEMPLATE = """\
# dotbrowser -- Brave configuration
# Docs: https://github.com/xom11/dotbrowser
# List available shortcut names:  dotbrowser brave shortcuts list
# Inspect current settings:       dotbrowser brave settings dump
#
# Apply this file:
#   dotbrowser brave apply {filename}
#
# Table semantics:
#   - missing header  -> module skipped, managed entries left alone.
#   - empty body      -> all previously-managed entries reset / popped.

[shortcuts]
# Command names map to Brave accelerators. Values are lists of key combos
# using Chromium KeyEvent codes (https://www.w3.org/TR/uievents-code/).
# Meta+ is auto-translated to Cmd on macOS.
#
# back                = ["Alt+KeyH"]
# forward             = ["Alt+KeyL"]
# select_previous_tab = ["Alt+KeyK"]
# select_next_tab     = ["Alt+KeyJ"]
# new_tab             = ["Control+KeyT"]
# close_tab           = ["Control+KeyW"]
# reload              = ["Control+KeyR"]

[settings]
# Keys are dotted paths into Brave's Preferences JSON.
# MAC-protected keys (homepage, default search, etc.) are refused -- set those
# in the Brave UI.
#
# "brave.tabs.vertical_tabs_enabled"   = true
# "brave.tabs.vertical_tabs_collapsed" = true
# "omnibox.prevent_url_elisions"       = true
# "brave.new_tab_page.show_stats"      = false
# "brave.new_tab_page.show_brave_news" = false

# [pwa]
# Force-installed Progressive Web Apps via Chromium enterprise policy.
# Requires sudo (Linux/macOS) or Administrator (Windows) + Brave restart.
# Uncomment the header and add URLs to enable.
#
# urls = [
#   "https://squoosh.app/",
# ]
"""


# ---------------------------------------------------------------------------
# Channel-aware argument resolution
# ---------------------------------------------------------------------------

def _setup_brave_profile_args(parser: argparse.ArgumentParser) -> None:
    """Brave-specific profile flags.

    ``--channel`` selects between stable, beta, and nightly. The
    default for ``--profile-root`` is deferred to runtime
    (``_normalize_brave_args``) because it depends on which channel
    the user picked.
    """
    parser.add_argument(
        "--channel",
        choices=CHANNELS,
        default="stable",
        help="Brave release channel (default: stable)",
    )
    parser.add_argument(
        "-r",
        "--profile-root",
        type=Path,
        default=None,
        help="default: auto-detect from --channel",
    )
    parser.add_argument(
        "-p",
        "--profile",
        default="Default",
        help="profile dir name (default: Default)",
    )


def _normalize_brave_args(args: argparse.Namespace) -> None:
    """Fill in ``args.profile_root`` from ``args.channel`` when omitted."""
    if getattr(args, "profile_root", None) is None:
        root = _default_profile_root(args.channel)
        if root is None:
            sys.exit(
                f"error: no default Brave profile root for platform "
                f"{sys.platform!r} (channel={args.channel!r}); "
                f"pass --profile-root explicitly"
            )
        args.profile_root = root


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------

def cmd_apply(args: argparse.Namespace) -> None:
    """Unified apply for Brave.

    For ``--channel stable`` we keep the module-level callbacks so
    tests can monkeypatch ``brave_pkg.brave_running`` etc.  For
    beta/nightly we use a freshly built BrowserProcess (those channels
    have no test coverage today).
    """
    channel = getattr(args, "channel", "stable")
    if channel == "stable":
        _base_cmd_apply(
            args,
            display_name="Brave",
            running_fn=brave_running,
            pids_fn=_brave_pids,
            find_cmdline_fn=find_main_brave_cmdline,
            kill_fn=kill_brave_and_wait,
            restart_fn=restart_brave,
            build_plans_fn=_build_plans,
        )
        return

    from dotbrowser.brave.utils import _make_browser_process
    proc = _make_browser_process(channel)
    _base_cmd_apply(
        args,
        display_name=proc.display_name,
        running_fn=proc.running,
        pids_fn=proc.pids,
        find_cmdline_fn=proc.find_main_cmdline,
        kill_fn=proc.kill_and_wait,
        restart_fn=proc.restart,
        build_plans_fn=_build_plans,
    )


def cmd_init(args: argparse.Namespace) -> None:
    _base_cmd_init(args, "brave", _INIT_TEMPLATE)


def _export_shortcuts(args: argparse.Namespace, prefs_path: Path, prefs: dict) -> list[str]:
    return shortcuts_mod.build_dump_block(
        prefs, all_bindings=getattr(args, "all_shortcuts", False)
    )


def _export_pwa(args: argparse.Namespace, prefs_path: Path, prefs: dict) -> list[str] | None:
    if pwa_mod.POLICY_FILE is None and sys.platform != "win32":
        return None
    return pwa_mod.build_dump_block()


def cmd_export(args: argparse.Namespace) -> None:
    _base_cmd_export(
        args,
        browser_name="brave",
        builders=[_export_shortcuts, _export_pwa],
    )


def cmd_restore(args: argparse.Namespace) -> None:
    """Restore Preferences from an apply-time backup.

    Resolves process callbacks the same way ``cmd_apply`` does so the
    Linux non-stable channel filter (and macOS app-name distinction)
    are honored when killing the right Brave install.
    """
    channel = getattr(args, "channel", "stable")
    if channel == "stable":
        _base_cmd_restore(
            args,
            display_name="Brave",
            running_fn=brave_running,
            pids_fn=_brave_pids,
            find_cmdline_fn=find_main_brave_cmdline,
            kill_fn=kill_brave_and_wait,
            restart_fn=restart_brave,
        )
        return

    from dotbrowser.brave.utils import _make_browser_process
    proc = _make_browser_process(channel)
    _base_cmd_restore(
        args,
        display_name=proc.display_name,
        running_fn=proc.running,
        pids_fn=proc.pids,
        find_cmdline_fn=proc.find_main_cmdline,
        kill_fn=proc.kill_and_wait,
        restart_fn=proc.restart,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> None:
    register_browser(
        subparsers,
        name="brave",
        help_text="Brave browser commands",
        default_profile_root=DEFAULT_PROFILE_ROOT,
        cmd_apply_fn=cmd_apply,
        cmd_init_fn=cmd_init,
        cmd_restore_fn=cmd_restore,
        cmd_export_fn=cmd_export,
        export_has_shortcuts=True,
        module_registers=[
            shortcuts_mod.register,
            settings_mod.register,
            pwa_mod.register,
        ],
        setup_profile_args=_setup_brave_profile_args,
        normalize_args=_normalize_brave_args,
    )
