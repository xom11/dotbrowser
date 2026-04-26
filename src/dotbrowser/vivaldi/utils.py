"""Shared helpers for Vivaldi subcommands.

Mirrors brave/utils.py: process detection, kill, restart, write_atomic,
and the `Plan` dataclass. Vivaldi is also Chromium-based, so the I/O
shape is the same; what differs is process names and the macOS .app
basename, which is the only thing this module encodes.
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
    """Same contract as brave.utils.Plan — see that file for the full
    rationale. Duplicated here so each browser package is self-contained
    (no cross-browser import chain) and so adding a third browser later
    is a copy-and-edit rather than a refactor of shared infrastructure.
    """

    namespace: str
    diff_lines: list[str]
    apply_fn: Callable[[dict], None]
    verify_fn: Callable[[dict], None]
    state_path: Path | None = None
    state_payload: dict[str, Any] | None = None
    external_apply_fn: Callable[[], None] | None = None

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


def _vivaldi_proc_name() -> str:
    """The basename `pgrep -x` matches.

    On macOS the executable inside the .app is literally `Vivaldi` (no
    space — different from Brave, which is `Brave Browser`). On Linux
    the user-facing wrapper is `vivaldi`, but the actual main process
    after exec is `vivaldi-bin`; pgrep on either name will miss the
    other, so we match the inner binary and rely on the wrapper having
    already exec'd by the time we look.
    """
    return "Vivaldi" if _is_macos() else "vivaldi-bin"


def vivaldi_running() -> bool:
    try:
        subprocess.check_output(
            ["pgrep", "-x", _vivaldi_proc_name()], stderr=subprocess.DEVNULL
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _vivaldi_pids() -> list[str]:
    try:
        out = subprocess.check_output(
            ["pgrep", "-x", _vivaldi_proc_name()], stderr=subprocess.DEVNULL
        )
        return out.decode().split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _read_cmdline(pid: str) -> list[str] | None:
    """Recover the argv for a running Vivaldi process. Same platform
    split as brave.utils._read_cmdline — see there for why."""
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


def find_main_vivaldi_cmdline() -> list[str] | None:
    """Pick the main process: the one without `--type=...`. Renderer /
    utility / GPU subprocesses all carry --type."""
    for pid in _vivaldi_pids():
        args = _read_cmdline(pid)
        if not args:
            continue
        if any("--type=" in a for a in args):
            continue
        return args
    return None


def kill_vivaldi_and_wait(timeout: float = 5.0) -> None:
    subprocess.run(
        ["pkill", "-KILL", "-x", _vivaldi_proc_name()], stderr=subprocess.DEVNULL
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not vivaldi_running():
            return
        time.sleep(0.1)
    sys.exit(f"error: Vivaldi still running after SIGKILL + {timeout}s wait")


def restart_vivaldi(captured_cmdline: list[str]) -> list[str]:
    """Restart Vivaldi the way the OS expects.

    macOS: `open -a "Vivaldi"` so Launch Services starts the .app
    bundle properly (re-registers URL handlers, restores dock state).
    Captured argv[1:] is forwarded via `--args`.

    Linux: prefer the `vivaldi` wrapper script in PATH over the captured
    argv[0] (which is `vivaldi-bin` after the wrapper has exec'd into
    it). Launching the inner binary directly bypasses the wrapper's
    PATH/env setup that Vivaldi expects for things like default-browser
    registration.
    """
    if _is_macos():
        cmdline = ["open", "-a", "Vivaldi"]
        forwarded = captured_cmdline[1:]
        if forwarded:
            cmdline += ["--args", *forwarded]
    else:
        wrapper = shutil.which("vivaldi") or shutil.which("vivaldi-stable")
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
