"""Vivaldi-specific process config + re-exports of shared utilities."""
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
# Vivaldi-specific process configuration
# ---------------------------------------------------------------------------

BROWSER_PROCESS = BrowserProcess(
    display_name="Vivaldi",
    proc_name_linux="vivaldi-bin",
    proc_name_macos="Vivaldi",
    proc_name_windows="vivaldi.exe",
    macos_app_name="Vivaldi",
    linux_wrappers=["vivaldi", "vivaldi-stable"],
    windows_exe_relpath=("Vivaldi", "Application", "vivaldi.exe"),
)


# ---------------------------------------------------------------------------
# Backward-compatible function aliases
# ---------------------------------------------------------------------------

def _vivaldi_proc_name() -> str:
    return BROWSER_PROCESS.proc_name()


def vivaldi_running() -> bool:
    return BROWSER_PROCESS.running()


def _vivaldi_pids() -> list[str]:
    return BROWSER_PROCESS.pids()


def find_main_vivaldi_cmdline() -> list[str] | None:
    return BROWSER_PROCESS.find_main_cmdline()


def kill_vivaldi_and_wait(timeout: float = 5.0) -> None:
    BROWSER_PROCESS.kill_and_wait(timeout)


def restart_vivaldi(captured_cmdline: list[str]) -> list[str]:
    return BROWSER_PROCESS.restart(captured_cmdline)
