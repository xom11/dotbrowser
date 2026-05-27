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


def test_brave_and_vivaldi_help_advertise_live_apply() -> None:
    brave = _help("brave")
    vivaldi_apply = _help("vivaldi", "apply")
    assert "[shortcuts] [settings] [pwa]" in brave
    assert "live apply" in brave.lower()
    assert "--channel" in brave
    assert "live apply" in vivaldi_apply.lower()
    assert "--live-port" in vivaldi_apply


def test_edge_and_chrome_help_only_advertise_supported_apply_features() -> None:
    edge_apply = _help("edge", "apply")
    chrome = _help("chrome")
    chrome_apply = _help("chrome", "apply")
    assert "[settings] [pwa]" in chrome
    assert "[shortcuts]" not in chrome_apply
    assert "[shortcuts]" not in edge_apply
    assert "--live-port" not in chrome_apply
    assert "--live-port" not in edge_apply
    assert "offline apply" in chrome.lower()


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
