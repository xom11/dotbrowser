"""Brave-specific process config + re-exports of shared utilities.

This module is the public API that ``brave/shortcuts.py``,
``brave/settings.py``, and ``brave/pwa.py`` import from.  It re-exports
the shared utilities from ``_base`` so existing imports keep working.
"""
from __future__ import annotations

# Re-export shared utilities (used by shortcuts.py, settings.py, pwa.py)
from dotbrowser._base.utils import (  # noqa: F401
    Plan,
    find_preferences,
    get_nested,
    load_prefs,
    write_atomic,
)
from dotbrowser._base.process import BrowserProcess

# ---------------------------------------------------------------------------
# Brave-specific process configuration
# ---------------------------------------------------------------------------

BROWSER_PROCESS = BrowserProcess(
    display_name="Brave",
    proc_name_linux="brave",
    proc_name_macos="Brave Browser",
    proc_name_windows="brave.exe",
    macos_app_name="Brave Browser",
    linux_wrappers=["brave-browser", "brave"],
    windows_exe_relpath=(
        "BraveSoftware", "Brave-Browser", "Application", "brave.exe",
    ),
    flatpak_prefix="/app/brave/",
    flatpak_app_id="com.brave.Browser",
)


# ---------------------------------------------------------------------------
# Backward-compatible function aliases (used by brave/__init__.py and tests)
# ---------------------------------------------------------------------------

def _brave_proc_name() -> str:
    return BROWSER_PROCESS.proc_name()


def brave_running() -> bool:
    return BROWSER_PROCESS.running()


def _brave_pids() -> list[str]:
    return BROWSER_PROCESS.pids()


def find_main_brave_cmdline() -> list[str] | None:
    return BROWSER_PROCESS.find_main_cmdline()


def kill_brave_and_wait(timeout: float = 5.0) -> None:
    BROWSER_PROCESS.kill_and_wait(timeout)


def restart_brave(captured_cmdline: list[str]) -> list[str]:
    return BROWSER_PROCESS.restart(captured_cmdline)


def _is_flatpak_brave_cmdline(captured_cmdline: list[str]) -> bool:
    return BROWSER_PROCESS._is_flatpak_cmdline(captured_cmdline)
