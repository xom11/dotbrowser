"""Chrome-specific process config + re-exports of shared utilities."""
from __future__ import annotations

from dotbrowser._base.utils import (  # noqa: F401
    Plan,
    find_preferences,
    get_nested,
    load_prefs,
    write_atomic,
)
from dotbrowser._base.process import BrowserProcess

BROWSER_PROCESS = BrowserProcess(
    display_name="Chrome",
    proc_name_linux="chrome",
    proc_name_macos="Google Chrome",
    proc_name_windows="chrome.exe",
    macos_app_name="Google Chrome",
    linux_wrappers=["google-chrome", "google-chrome-stable", "chromium"],
    windows_exe_relpath=(
        "Google", "Chrome", "Application", "chrome.exe",
    ),
)


def _chrome_proc_name() -> str:
    return BROWSER_PROCESS.proc_name()


def chrome_running() -> bool:
    return BROWSER_PROCESS.running()


def _chrome_pids() -> list[str]:
    return BROWSER_PROCESS.pids()


def find_main_chrome_cmdline() -> list[str] | None:
    return BROWSER_PROCESS.find_main_cmdline()


def kill_chrome_and_wait(timeout: float = 5.0) -> None:
    BROWSER_PROCESS.kill_and_wait(timeout)


def restart_chrome(captured_cmdline: list[str]) -> list[str]:
    return BROWSER_PROCESS.restart(captured_cmdline)
