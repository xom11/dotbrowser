"""Edge-specific process config + re-exports of shared utilities."""
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
    display_name="Edge",
    proc_name_linux="msedge",
    proc_name_macos="Microsoft Edge",
    proc_name_windows="msedge.exe",
    macos_app_name="Microsoft Edge",
    linux_wrappers=["microsoft-edge", "microsoft-edge-stable"],
    windows_exe_relpath=(
        "Microsoft", "Edge", "Application", "msedge.exe",
    ),
)


def _edge_proc_name() -> str:
    return BROWSER_PROCESS.proc_name()


def edge_running() -> bool:
    return BROWSER_PROCESS.running()


def _edge_pids() -> list[str]:
    return BROWSER_PROCESS.pids()


def find_main_edge_cmdline() -> list[str] | None:
    return BROWSER_PROCESS.find_main_cmdline()


def kill_edge_and_wait(timeout: float = 5.0) -> None:
    BROWSER_PROCESS.kill_and_wait(timeout)


def restart_edge(captured_cmdline: list[str]) -> list[str]:
    return BROWSER_PROCESS.restart(captured_cmdline)
