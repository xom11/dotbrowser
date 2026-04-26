"""Tests specific to the unified `brave apply` orchestration.

The other apply test files cover one namespace each via the same
unified entry point. This file focuses on the cross-module concerns:
combined diff, single-cycle write, and missing-vs-empty-table semantics.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotbrowser import brave as brave_pkg

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def combined_profile_root(tmp_path: Path) -> Path:
    """Profile that has both shortcuts defaults (for the shortcuts side)
    AND a MAC entry (so the parent-of-protected refusal can also be
    exercised in cross-module cases). No tracked-pref refusal in the
    happy paths so we can write a combined config end-to-end."""
    from dotbrowser.brave.command_ids import NAME_TO_ID

    profile = tmp_path / "Default"
    profile.mkdir()
    prefs = {
        "brave": {
            "accelerators": {
                str(NAME_TO_ID["focus_location"]): ["Alt+KeyL"],
            },
            "default_accelerators": {
                str(NAME_TO_ID["focus_location"]): ["Control+KeyL"],
                str(NAME_TO_ID["new_tab"]): ["Control+KeyT"],
            },
            "tabs": {
                "vertical_tabs_enabled": False,
            },
        },
        "bookmark_bar": {"show_tab_groups": False},
        "protection": {
            "macs": {"browser": {"show_home_button": "DEAD" * 16}}
        },
    }
    (profile / "Preferences").write_text(json.dumps(prefs))
    return tmp_path


def _run_cli(profile_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
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
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def _apply(profile_root: Path, config: Path) -> None:
    args = argparse.Namespace(
        profile_root=profile_root,
        profile="Default",
        config=config,
        dry_run=False,
        kill_browser=False,
    )
    brave_pkg.cmd_apply(args)


def test_combined_apply_writes_both_namespaces_in_one_cycle(
    combined_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """A single TOML with both [shortcuts] and [settings] should produce
    one backup, one Preferences write, and two state files."""
    monkeypatch.setattr(brave_pkg, "brave_running", lambda: False)

    cfg = tmp_path / "brave.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'focus_location = ["Alt+KeyD"]\n'
        '\n'
        '[settings]\n'
        '"brave.tabs.vertical_tabs_enabled" = true\n'
        '"bookmark_bar.show_tab_groups" = true\n'
    )

    _apply(combined_profile_root, cfg)

    p = json.loads((combined_profile_root / "Default" / "Preferences").read_text())

    from dotbrowser.brave.command_ids import NAME_TO_ID
    # shortcuts side
    assert p["brave"]["accelerators"][str(NAME_TO_ID["focus_location"])] == ["Alt+KeyD"]
    # settings side
    assert p["brave"]["tabs"]["vertical_tabs_enabled"] is True
    assert p["bookmark_bar"]["show_tab_groups"] is True

    # Both sidecar state files should exist with the right entries
    sc_state = json.loads(
        (combined_profile_root / "Default" / "Preferences.dotbrowser.shortcuts.json").read_text()
    )
    st_state = json.loads(
        (combined_profile_root / "Default" / "Preferences.dotbrowser.settings.json").read_text()
    )
    assert sc_state["managed_ids"] == [str(NAME_TO_ID["focus_location"])]
    assert sorted(st_state["managed_keys"]) == [
        "bookmark_bar.show_tab_groups",
        "brave.tabs.vertical_tabs_enabled",
    ]

    # Exactly one timestamped backup — a combined apply should not produce
    # one backup per module.
    backups = list((combined_profile_root / "Default").glob("Preferences.bak.*"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"


def test_combined_dry_run_shows_grouped_diff(
    combined_profile_root: Path, tmp_path: Path
) -> None:
    """Dry-run output should label each section by namespace so the user
    can tell which module is doing what."""
    cfg = tmp_path / "brave.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'focus_location = ["Alt+KeyD"]\n'
        '\n'
        '[settings]\n'
        '"brave.tabs.vertical_tabs_enabled" = true\n'
    )

    r = _run_cli(combined_profile_root, "apply", str(cfg), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "shortcuts:" in r.stdout
    assert "settings:" in r.stdout
    assert "(dry-run, nothing written)" in r.stdout


def test_settings_refusal_blocks_shortcuts_write_too(
    combined_profile_root: Path, tmp_path: Path
) -> None:
    """If [settings] contains a MAC-protected key, the WHOLE apply must
    fail — shortcuts must not be written either, since we promise a
    single atomic cycle. This is the "no partial write" guarantee."""
    cfg = tmp_path / "brave.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'focus_location = ["Alt+KeyD"]\n'
        '\n'
        '[settings]\n'
        '"browser.show_home_button" = false\n'
    )

    before = (combined_profile_root / "Default" / "Preferences").read_bytes()
    r = _run_cli(combined_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "MAC-protected" in (r.stdout + r.stderr)

    after = (combined_profile_root / "Default" / "Preferences").read_bytes()
    assert before == after, "shortcuts must not be written when settings refuses"

    # No state file should have been created either
    assert not (combined_profile_root / "Default" / "Preferences.dotbrowser.shortcuts.json").exists()
    assert not (combined_profile_root / "Default" / "Preferences.dotbrowser.settings.json").exists()


def test_missing_table_skips_module_entirely(
    combined_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """A TOML without [settings] must not touch settings state, even if
    a previous run left a state file behind."""
    monkeypatch.setattr(brave_pkg, "brave_running", lambda: False)

    # First run: apply both namespaces, populating both state files.
    full = tmp_path / "full.toml"
    full.write_text(
        '[shortcuts]\n'
        'focus_location = ["Alt+KeyD"]\n'
        '\n'
        '[settings]\n'
        '"brave.tabs.vertical_tabs_enabled" = true\n'
    )
    _apply(combined_profile_root, full)

    settings_state_path = (
        combined_profile_root / "Default" / "Preferences.dotbrowser.settings.json"
    )
    settings_state_before = settings_state_path.read_text()

    p_before = json.loads((combined_profile_root / "Default" / "Preferences").read_text())
    settings_value_before = p_before["brave"]["tabs"]["vertical_tabs_enabled"]

    # Second run: only [shortcuts] in the TOML. The settings module must
    # be skipped entirely — state file unchanged, pref value unchanged.
    only_sc = tmp_path / "only-sc.toml"
    only_sc.write_text(
        '[shortcuts]\n'
        'focus_location = ["Alt+KeyZ"]\n'
    )
    _apply(combined_profile_root, only_sc)

    assert settings_state_path.read_text() == settings_state_before
    p_after = json.loads((combined_profile_root / "Default" / "Preferences").read_text())
    assert p_after["brave"]["tabs"]["vertical_tabs_enabled"] == settings_value_before


def test_empty_table_resets_managed_entries(
    combined_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """An EMPTY [settings] header (different from missing) is the explicit
    "wipe all my managed settings" gesture — managed keys are popped from
    Preferences and the state file becomes empty."""
    monkeypatch.setattr(brave_pkg, "brave_running", lambda: False)

    # Seed: apply something
    seed = tmp_path / "seed.toml"
    seed.write_text(
        '[settings]\n'
        '"brave.tabs.vertical_tabs_enabled" = true\n'
        '"bookmark_bar.show_tab_groups" = true\n'
    )
    _apply(combined_profile_root, seed)
    p = json.loads((combined_profile_root / "Default" / "Preferences").read_text())
    assert p["brave"]["tabs"]["vertical_tabs_enabled"] is True

    # Now apply with [settings] header but no body
    empty = tmp_path / "empty.toml"
    empty.write_text(
        '[shortcuts]\n'
        'focus_location = ["Alt+KeyD"]\n'
        '\n'
        '[settings]\n'
    )
    _apply(combined_profile_root, empty)

    p = json.loads((combined_profile_root / "Default" / "Preferences").read_text())
    # Both settings managed by the seed should be popped
    assert "vertical_tabs_enabled" not in p["brave"].get("tabs", {})
    assert "show_tab_groups" not in p.get("bookmark_bar", {})

    state = json.loads(
        (combined_profile_root / "Default" / "Preferences.dotbrowser.settings.json").read_text()
    )
    assert state["managed_keys"] == []
