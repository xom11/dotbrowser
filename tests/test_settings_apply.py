"""End-to-end tests for `brave settings apply` against a fake profile.

Exercises the full apply/refuse/reset round-trip via in-process calls
to `cmd_apply`. The CLI subprocess is used only for dry-run + error
paths so we don't depend on a venv layout.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotbrowser.brave import settings as st

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(profile_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dotbrowser",
            "brave",
            "--profile-root",
            str(profile_root),
            "settings",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def _write_config(path: Path, mapping: dict) -> None:
    lines = ["[settings]"]
    for key, value in mapping.items():
        lines.append(f"{json.dumps(key)} = {st._format_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n")


def _prefs(profile_root: Path) -> dict:
    return json.loads((profile_root / "Default" / "Preferences").read_text())


def _apply(profile_root: Path, config: Path, *, kill_brave: bool = False) -> None:
    args = argparse.Namespace(
        profile_root=profile_root,
        profile="Default",
        config=config,
        dry_run=False,
        kill_brave=kill_brave,
    )
    st.cmd_apply(args)


def test_apply_writes_then_drops(
    fake_settings_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """Round-trip: apply → re-apply (no-op) → drop key → key is popped."""
    monkeypatch.setattr(st, "brave_running", lambda: False)

    cfg = tmp_path / "settings.toml"

    # 1. dry-run via CLI: no write
    _write_config(cfg, {"brave.tabs.vertical_tabs_enabled": True})
    before = (fake_settings_profile_root / "Default" / "Preferences").read_bytes()
    r = _run_cli(fake_settings_profile_root, "apply", str(cfg), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "(dry-run, nothing written)" in r.stdout
    after = (fake_settings_profile_root / "Default" / "Preferences").read_bytes()
    assert before == after

    # 2. real apply (in-process): keys land in Preferences
    _write_config(
        cfg,
        {
            "brave.tabs.vertical_tabs_enabled": True,
            "brave.tabs.vertical_tabs_collapsed": False,
            "bookmark_bar.show_tab_groups": True,
        },
    )
    _apply(fake_settings_profile_root, cfg)

    p = _prefs(fake_settings_profile_root)
    assert p["brave"]["tabs"]["vertical_tabs_enabled"] is True
    assert p["brave"]["tabs"]["vertical_tabs_collapsed"] is False
    assert p["bookmark_bar"]["show_tab_groups"] is True
    # Untouched key stays
    assert p["some"]["unrelated"] == "preference"

    sidecar = (
        fake_settings_profile_root
        / "Default"
        / "Preferences.dotbrowser.settings.json"
    )
    state = json.loads(sidecar.read_text())
    assert sorted(state["managed_keys"]) == [
        "bookmark_bar.show_tab_groups",
        "brave.tabs.vertical_tabs_collapsed",
        "brave.tabs.vertical_tabs_enabled",
    ]

    backups = list((fake_settings_profile_root / "Default").glob("Preferences.bak.*"))
    assert backups

    # 3. idempotent re-apply
    _apply(fake_settings_profile_root, cfg)

    # 4. drop two keys → they are popped from Preferences (no default mirror)
    _write_config(cfg, {"brave.tabs.vertical_tabs_enabled": True})
    _apply(fake_settings_profile_root, cfg)
    p = _prefs(fake_settings_profile_root)
    assert p["brave"]["tabs"]["vertical_tabs_enabled"] is True
    assert "vertical_tabs_collapsed" not in p["brave"]["tabs"]
    assert "show_tab_groups" not in p["bookmark_bar"]

    state = json.loads(sidecar.read_text())
    assert state["managed_keys"] == ["brave.tabs.vertical_tabs_enabled"]


def test_apply_refuses_mac_protected_keys(
    fake_settings_profile_root: Path, tmp_path: Path
) -> None:
    """Any key whose path matches an entry in protection.macs must be
    refused before any write happens."""
    cfg = tmp_path / "bad.toml"
    _write_config(
        cfg,
        {
            "homepage": "https://evil.example",
            "browser.show_home_button": False,
            "brave.tabs.vertical_tabs_enabled": True,
        },
    )

    before = (fake_settings_profile_root / "Default" / "Preferences").read_bytes()
    r = _run_cli(fake_settings_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "homepage" in out
    assert "browser.show_home_button" in out
    assert "MAC-protected" in out

    after = (fake_settings_profile_root / "Default" / "Preferences").read_bytes()
    assert before == after, "refused apply must not modify Preferences"


def test_apply_refuses_protection_subtree(
    fake_settings_profile_root: Path, tmp_path: Path
) -> None:
    """Writing under `protection.*` must always be refused — that's
    Chromium's MAC bookkeeping subtree."""
    cfg = tmp_path / "bad.toml"
    _write_config(cfg, {"protection.macs.foo": "anything"})

    r = _run_cli(fake_settings_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "Chromium MAC bookkeeping" in (r.stdout + r.stderr)


def test_apply_refuses_parent_of_mac_protected_leaf(
    fake_settings_profile_root: Path, tmp_path: Path
) -> None:
    """`browser` is a parent of the tracked `browser.show_home_button`.
    Writing the parent dict would clobber the tracked child, so it must
    be refused too."""
    cfg = tmp_path / "bad.toml"
    _write_config(cfg, {"browser": {"show_home_button": False}})

    r = _run_cli(fake_settings_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "MAC-protected" in (r.stdout + r.stderr)


def test_apply_creates_missing_nested_path(
    fake_settings_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """A key whose parent dicts don't exist yet must be created."""
    monkeypatch.setattr(st, "brave_running", lambda: False)

    cfg = tmp_path / "settings.toml"
    _write_config(cfg, {"brave.new_namespace.some_flag": True})
    _apply(fake_settings_profile_root, cfg)

    p = _prefs(fake_settings_profile_root)
    assert p["brave"]["new_namespace"]["some_flag"] is True


def test_dump_managed_keys_round_trip(
    fake_settings_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """`dump` (no args) should emit a TOML doc that, when parsed, has
    exactly the managed keys with their current values."""
    monkeypatch.setattr(st, "brave_running", lambda: False)

    cfg = tmp_path / "in.toml"
    _write_config(
        cfg,
        {
            "brave.tabs.vertical_tabs_enabled": True,
            "bookmark_bar.show_tab_groups": True,
        },
    )
    _apply(fake_settings_profile_root, cfg)

    r = _run_cli(fake_settings_profile_root, "dump")
    assert r.returncode == 0, r.stderr

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore
    parsed = tomllib.loads(r.stdout)
    assert parsed["settings"] == {
        "brave.tabs.vertical_tabs_enabled": True,
        "bookmark_bar.show_tab_groups": True,
    }


def test_dump_explicit_keys(fake_settings_profile_root: Path) -> None:
    """`dump <key>...` should emit those exact keys regardless of state."""
    r = _run_cli(
        fake_settings_profile_root,
        "dump",
        "brave.tabs.vertical_tabs_enabled",
        "bookmark_bar.show_tab_groups",
        "missing.never.set",
    )
    assert r.returncode == 0, r.stderr
    assert '"brave.tabs.vertical_tabs_enabled" = false' in r.stdout
    assert '"bookmark_bar.show_tab_groups" = false' in r.stdout
    # Missing keys are reported as a comment, not silently emitted
    assert "missing.never.set" in r.stdout
    assert "# keys not present" in r.stdout


def test_dump_no_managed_errors(fake_settings_profile_root: Path) -> None:
    """First-run `dump` (no state file, no args) should fail loudly so
    the user knows to pass keys explicitly."""
    r = _run_cli(fake_settings_profile_root, "dump")
    assert r.returncode != 0
    assert "no managed keys" in (r.stdout + r.stderr)
