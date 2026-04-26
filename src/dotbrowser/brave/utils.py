"""Shared helpers for Brave subcommands.

Process detection / kill / restart, atomic JSON writes, and small dict
utilities used by both `shortcuts` and `settings`. Keeping these in a
single module means there is exactly one place that knows how Brave's
process layout differs between Linux and macOS.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Plan:
    """An applied-or-dry-run-able set of changes from one module.

    Each module's `plan_apply` returns one of these. The unified
    `brave apply` orchestrator collects plans from every module that
    has a corresponding TOML table, prints their diffs, and (if not
    dry-run) runs all `apply_fn`s against a single in-memory `Preferences`
    dict before a single `write_atomic`. State sidecars are written
    afterwards, and `verify_fn`s run against the reloaded prefs.

    `diff_lines` empty ⇔ this module has nothing to do for this config.
    """

    namespace: str  # e.g. "shortcuts" or "settings" — used as a section header
    diff_lines: list[str]
    state_path: Path
    state_payload: dict[str, Any]
    apply_fn: Callable[[dict], None]
    verify_fn: Callable[[dict], None]

    @property
    def empty(self) -> bool:
        return not self.diff_lines


def find_preferences(profile_root: Path, profile: str) -> Path:
    p = profile_root / profile / "Preferences"
    if not p.exists():
        sys.exit(f"error: Preferences not found at {p}")
    return p


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _brave_proc_name() -> str:
    """The exact basename `pgrep -x` matches against.

    On Linux the main binary is `brave` (the `brave-browser` wrapper exec's
    into it). On macOS the executable inside the .app bundle is literally
    `Brave Browser` with a space — Helper processes use a different name
    (`Brave Browser Helper`, `Brave Browser Helper (GPU)`, ...) so `pgrep -x`
    on the exact name already excludes them.
    """
    return "Brave Browser" if _is_macos() else "brave"


def brave_running() -> bool:
    try:
        subprocess.check_output(
            ["pgrep", "-x", _brave_proc_name()], stderr=subprocess.DEVNULL
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _brave_pids() -> list[str]:
    try:
        out = subprocess.check_output(
            ["pgrep", "-x", _brave_proc_name()], stderr=subprocess.DEVNULL
        )
        return out.decode().split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _read_cmdline(pid: str) -> list[str] | None:
    """Recover the command-line argv for a running Brave process.

    Linux: read `/proc/<pid>/cmdline`. Chromium subprocesses overwrite their
    argv region (setproctitle-style) and lose null separators, leaving a
    single space-joined string — fall back to `shlex.split` in that case.

    macOS: no `/proc`. `ps -o command= -p <pid>` returns the full command
    line as a single line, but the executable path itself contains
    unescaped spaces (`/Applications/Brave Browser.app/Contents/MacOS/Brave
    Browser`) so `shlex.split` would corrupt it. We return the line as a
    single-element list — that's enough for the "did we capture anything?"
    signal that drives restart, and the macOS restart path doesn't need the
    parsed argv anyway (it relaunches via `open -a "Brave Browser"`).
    """
    if _is_macos():
        try:
            out = subprocess.check_output(
                ["ps", "-o", "command=", "-p", pid],
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        line = out.decode("utf-8", "replace").strip()
        return [line] if line else None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError):
        return None
    parts = [a.decode("utf-8", "replace") for a in raw.rstrip(b"\0").split(b"\0")]
    if len(parts) == 1 and " " in parts[0]:
        return shlex.split(parts[0])
    return parts


def find_main_brave_cmdline() -> list[str] | None:
    """The main Brave process is the one without a `--type=...` arg
    (renderer/utility/gpu subprocesses all carry --type). On macOS the
    helper processes also have different basenames so `pgrep -x` already
    excludes them; the --type filter is still cheap insurance, applied as
    a substring check so it works against the un-tokenized macOS form."""
    for pid in _brave_pids():
        args = _read_cmdline(pid)
        if not args:
            continue
        if any("--type=" in a for a in args):
            continue
        return args
    return None


def kill_brave_and_wait(timeout: float = 5.0) -> None:
    subprocess.run(
        ["pkill", "-KILL", "-x", _brave_proc_name()], stderr=subprocess.DEVNULL
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not brave_running():
            return
        time.sleep(0.1)
    sys.exit(f"error: Brave still running after SIGKILL + {timeout}s wait")


def restart_brave(captured_cmdline: list[str]) -> list[str]:
    """Restart Brave the way the OS expects.

    Linux: prefer the `brave-browser` wrapper script in PATH. It sets
    `CHROME_WRAPPER` and fixes PATH for xdg utilities (default-browser
    registration, URL handlers); launching the inner binary directly
    silently breaks those.

    macOS: launch through `open -a "Brave Browser"` so Launch Services
    starts the .app bundle properly (re-registers URL handlers, restores
    dock state). Captured argv[1:] is forwarded via `--args` so any flags
    that were active stay active after restart.

    Returns the cmdline actually used (for logging).
    """
    if _is_macos():
        cmdline = ["open", "-a", "Brave Browser"]
        forwarded = captured_cmdline[1:]
        if forwarded:
            cmdline += ["--args", *forwarded]
    else:
        wrapper = shutil.which("brave-browser") or shutil.which("brave")
        if wrapper:
            cmdline = [wrapper, *captured_cmdline[1:]]
        else:
            cmdline = list(captured_cmdline)
    subprocess.Popen(
        cmdline,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return cmdline


def load_prefs(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def get_nested(d: dict, keys: tuple[str, ...]) -> dict:
    for k in keys:
        d = d.setdefault(k, {})
    return d


def write_atomic(path: Path, prefs: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(prefs, f, separators=(",", ":"))
    os.replace(tmp, path)
