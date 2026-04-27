"""Vivaldi-browser-specific subcommands.

Top-level CLI shape (mirrors ``dotbrowser brave``):

    dotbrowser vivaldi [--profile-root ...] [--profile ...] <ACTION> ...

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
    cmd_restore as _base_cmd_restore,
    register_browser,
)
from dotbrowser._base.utils import Plan
from dotbrowser.vivaldi import pwa as pwa_mod
from dotbrowser.vivaldi import settings as settings_mod
from dotbrowser.vivaldi import shortcuts as shortcuts_mod
from dotbrowser.vivaldi.utils import (  # noqa: F401
    BROWSER_PROCESS,
    _vivaldi_pids,
    find_main_vivaldi_cmdline,
    kill_vivaldi_and_wait,
    restart_vivaldi,
    vivaldi_running,
)


def _default_profile_root() -> Path | None:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Vivaldi"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "Vivaldi" / "User Data"
            if (candidate / "Local State").exists():
                return candidate
            return candidate
        return None
    if sys.platform.startswith("linux"):
        return home / ".config" / "vivaldi"
    return None


DEFAULT_PROFILE_ROOT = _default_profile_root()


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


_INIT_TEMPLATE = """\
# dotbrowser -- Vivaldi configuration
# Docs: https://github.com/xom11/dotbrowser
# List available shortcut names:  dotbrowser vivaldi shortcuts list
# Inspect current settings:       dotbrowser vivaldi settings dump
#
# Apply this file:
#   dotbrowser vivaldi apply {filename}
#
# Table semantics:
#   - missing header  -> module skipped, managed entries left alone.
#   - empty body      -> all previously-managed entries reset / popped.

[shortcuts]
# Command names are Vivaldi COMMAND_* identifiers. Values are lists of
# key combos in Vivaldi's lowercase format (e.g. "meta+w").
#
# COMMAND_CLOSE_TAB     = ["meta+w"]
# COMMAND_NEW_TAB       = ["meta+t"]
# COMMAND_REOPEN_TAB    = ["meta+shift+t"]
# COMMAND_SELECT_ALL    = ["meta+a"]

[settings]
# Keys are dotted paths into Vivaldi's Preferences JSON.
# MAC-protected keys are refused -- set those in the Vivaldi UI.
#
# "vivaldi.tabs.vertical_tabs_enabled" = true
# "omnibox.prevent_url_elisions"       = true

# [pwa]
# Force-installed Progressive Web Apps via Chromium enterprise policy.
# Requires sudo (Linux/macOS) or Administrator (Windows) + Vivaldi restart.
# Uncomment the header and add URLs to enable.
#
# urls = [
#   "https://squoosh.app/",
# ]
"""


def cmd_apply(args: argparse.Namespace) -> None:
    _base_cmd_apply(
        args,
        display_name="Vivaldi",
        running_fn=vivaldi_running,
        pids_fn=_vivaldi_pids,
        find_cmdline_fn=find_main_vivaldi_cmdline,
        kill_fn=kill_vivaldi_and_wait,
        restart_fn=restart_vivaldi,
        build_plans_fn=_build_plans,
    )


def cmd_init(args: argparse.Namespace) -> None:
    _base_cmd_init(args, "vivaldi", _INIT_TEMPLATE)


def cmd_restore(args: argparse.Namespace) -> None:
    _base_cmd_restore(
        args,
        display_name="Vivaldi",
        running_fn=vivaldi_running,
        pids_fn=_vivaldi_pids,
        find_cmdline_fn=find_main_vivaldi_cmdline,
        kill_fn=kill_vivaldi_and_wait,
        restart_fn=restart_vivaldi,
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    register_browser(
        subparsers,
        name="vivaldi",
        help_text="Vivaldi browser commands",
        default_profile_root=DEFAULT_PROFILE_ROOT,
        cmd_apply_fn=cmd_apply,
        cmd_init_fn=cmd_init,
        cmd_restore_fn=cmd_restore,
        module_registers=[
            shortcuts_mod.register,
            settings_mod.register,
            pwa_mod.register,
        ],
    )
