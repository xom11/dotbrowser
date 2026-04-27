"""Microsoft Edge browser subcommands.

Edge is Chromium-based and uses the same Preferences JSON model as
Brave and Vivaldi.  Shortcuts are NOT supported — Edge does not expose
a user-facing shortcut customization API in the Preferences file
(unlike Brave's ``brave.accelerators``).

Supported modules: ``[settings]`` and ``[pwa]``.
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
from dotbrowser.edge import pwa as pwa_mod
from dotbrowser.edge import settings as settings_mod
from dotbrowser.edge.utils import (  # noqa: F401
    BROWSER_PROCESS,
    _edge_pids,
    edge_running,
    find_main_edge_cmdline,
    kill_edge_and_wait,
    restart_edge,
)


def _default_profile_root() -> Path | None:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Microsoft Edge"
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidate = Path(local_app_data) / "Microsoft" / "Edge" / "User Data"
            if (candidate / "Local State").exists():
                return candidate
            return candidate
        return None
    if sys.platform.startswith("linux"):
        return home / ".config" / "microsoft-edge"
    return None


DEFAULT_PROFILE_ROOT = _default_profile_root()


def _build_plans(prefs_path: Path, prefs: dict, doc: dict) -> list[Plan]:
    plans: list[Plan] = []
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
# dotbrowser -- Microsoft Edge configuration
# Docs: https://github.com/xom11/dotbrowser
# Inspect current settings:  dotbrowser edge settings dump
#
# Apply this file:
#   dotbrowser edge apply {filename}
#
# Table semantics:
#   - missing header  -> module skipped, managed entries left alone.
#   - empty body      -> all previously-managed entries reset / popped.
#
# NOTE: Edge does not support custom keyboard shortcuts via Preferences.
# Only [settings] and [pwa] are available.

[settings]
# Keys are dotted paths into Edge's Preferences JSON.
# MAC-protected keys are refused -- set those in the Edge UI.
#
# "browser.show_home_button"           = true
# "omnibox.prevent_url_elisions"       = true
# "bookmark_bar.show_on_all_tabs"      = false

# [pwa]
# Force-installed Progressive Web Apps via Chromium enterprise policy.
# Requires sudo (Linux/macOS) or Administrator (Windows) + Edge restart.
# Uncomment the header and add URLs to enable.
#
# urls = [
#   "https://squoosh.app/",
# ]
"""


def cmd_apply(args: argparse.Namespace) -> None:
    _base_cmd_apply(
        args,
        display_name="Edge",
        running_fn=edge_running,
        pids_fn=_edge_pids,
        find_cmdline_fn=find_main_edge_cmdline,
        kill_fn=kill_edge_and_wait,
        restart_fn=restart_edge,
        build_plans_fn=_build_plans,
    )


def cmd_init(args: argparse.Namespace) -> None:
    _base_cmd_init(args, "edge", _INIT_TEMPLATE)


def _export_pwa(args: argparse.Namespace, prefs_path: Path, prefs: dict) -> list[str] | None:
    if pwa_mod.POLICY_FILE is None and sys.platform != "win32":
        return None
    return pwa_mod.build_dump_block()


def cmd_export(args: argparse.Namespace) -> None:
    _base_cmd_export(args, browser_name="edge", builders=[_export_pwa])


def cmd_restore(args: argparse.Namespace) -> None:
    _base_cmd_restore(
        args,
        display_name="Edge",
        running_fn=edge_running,
        pids_fn=_edge_pids,
        find_cmdline_fn=find_main_edge_cmdline,
        kill_fn=kill_edge_and_wait,
        restart_fn=restart_edge,
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    register_browser(
        subparsers,
        name="edge",
        help_text="Microsoft Edge browser commands",
        default_profile_root=DEFAULT_PROFILE_ROOT,
        cmd_apply_fn=cmd_apply,
        cmd_init_fn=cmd_init,
        cmd_restore_fn=cmd_restore,
        cmd_export_fn=cmd_export,
        module_registers=[
            settings_mod.register,
            pwa_mod.register,
        ],
    )
