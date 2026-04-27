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
    cmd_init as _base_cmd_init,
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


def _default_profile_root() -> Path | None:
    """Brave's profile root, per platform.

    Returns None for unsupported platforms; the CLI then requires
    --profile-root to be passed explicitly so that --help still works
    on BSD / etc. without crashing at import time.
    """
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "User Data"
            if (candidate / "Local State").exists():
                return candidate
            return candidate
        return None
    if sys.platform.startswith("linux"):
        candidates = (
            home / ".config" / "BraveSoftware" / "Brave-Browser",
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
# Docs: https://github.com/nichochar/dotbrowser
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
# CLI handlers
# ---------------------------------------------------------------------------

def cmd_apply(args: argparse.Namespace) -> None:
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


def cmd_init(args: argparse.Namespace) -> None:
    _base_cmd_init(args, "brave", _INIT_TEMPLATE)


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
        module_registers=[
            shortcuts_mod.register,
            settings_mod.register,
            pwa_mod.register,
        ],
    )
