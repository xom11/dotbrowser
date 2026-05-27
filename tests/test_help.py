"""User-facing help text regression coverage.

These tests execute the installed CLI surface instead of introspecting
argparse internals. Help is part of dotbrowser's discoverability contract:
it must report only capabilities that the selected browser actually has.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "dotbrowser", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _help(*args: str) -> str:
    result = _run(*args, "--help")
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_root_help_explains_capabilities_and_workflow() -> None:
    out = _help()
    assert "Capability overview" in out
    assert "Brave" in out and "[shortcuts] [settings] [pwa]" in out
    assert "Edge" in out and "[settings] [pwa]" in out
    assert "Typical workflow" in out
    assert "apply --dry-run" in out
    assert "restore" in out


def test_all_browser_help_advertises_automatic_live_apply() -> None:
    brave = _help("brave")
    assert "[shortcuts] [settings] [pwa]" in brave
    assert "--channel" in brave
    for browser in ("brave", "vivaldi", "chrome", "edge"):
        browser_help = _help(browser)
        apply_help = _help(browser, "apply")
        assert "live apply" in apply_help.lower()
        assert "--kill-browser" not in apply_help
        assert "--live-port" not in apply_help
        assert _run(browser, "launch", "--help").returncode != 0

    chrome_apply = _help("chrome", "apply")
    edge_apply = _help("edge", "apply")
    assert "[shortcuts]" not in chrome_apply
    assert "[shortcuts]" not in edge_apply


def test_removed_apply_flags_are_rejected() -> None:
    removed = [
        ("--kill-browser",),
        ("--live-port", "9333"),
    ]
    for option in removed:
        result = _run("brave", "apply", *option, "missing.toml")
        assert result.returncode != 0
        assert "unrecognized arguments" in result.stderr


def test_export_and_restore_help_state_deliberate_limits() -> None:
    export = _help("brave", "export")
    restore = _help("brave", "restore")
    chrome_export = _help("chrome", "export")
    assert "[settings] is intentionally not exported" in export
    assert "[pwa] policy is not restored" in restore
    assert "[pwa] only" in chrome_export


def test_namespace_help_explains_specialized_discovery() -> None:
    shortcuts = _help("brave", "shortcuts")
    settings = _help("chrome", "settings")
    pwa = _help("edge", "pwa")
    vivaldi_settings = _help("vivaldi", "settings")
    assert "Chromium KeyEvent codes" in shortcuts
    assert "MAC-protected" in settings
    assert "managed policy" in pwa
    assert "prefs schema" in vivaldi_settings
    assert "describe" in vivaldi_settings
