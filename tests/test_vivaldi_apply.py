"""End-to-end tests for the unified `vivaldi apply` orchestrator.

Mirrors tests/test_apply_live.py + tests/test_unified_apply.py (the
Brave equivalents) but focuses on the parts that diverge:

- the `vivaldi.actions[0]` list-of-one-dict shape
- gestures-field preservation when rewriting shortcuts
- the original-snapshot scheme used in place of Brave's
  `default_accelerators` mirror

The unified-apply mechanics (single backup, sudo preflight, missing-
vs-empty-table semantics, state sidecars, three-namespace round-trip)
are already covered for Brave and the Vivaldi orchestrator is a
copy with paths/process-name swapped, so we exercise just one
combined-apply test here rather than re-running every scenario.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from dotbrowser import vivaldi as vivaldi_pkg
from dotbrowser.vivaldi import pwa as pwa_mod
from dotbrowser.vivaldi import shortcuts as shortcuts_mod

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(profile_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "dotbrowser",
            "vivaldi",
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
    vivaldi_pkg.cmd_apply(args)


def _prefs(profile_root: Path) -> dict:
    return json.loads((profile_root / "Default" / "Preferences").read_text())


def _actions(profile_root: Path) -> dict:
    """Pull `vivaldi.actions[0]` out of a profile's Preferences."""
    return _prefs(profile_root)["vivaldi"]["actions"][0]


# ---------------------------------------------------------------------------
# Shortcuts round-trip
# ---------------------------------------------------------------------------


def test_shortcut_apply_then_restore_via_state(
    fake_vivaldi_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """Full cycle: apply override → re-apply (no-op) → drop key from
    config → original is restored from state. This is the Vivaldi-
    specific path because there's no `default_actions` mirror — the
    snapshot in the state file IS the recovery source.
    """
    monkeypatch.setattr(vivaldi_pkg, "vivaldi_running", lambda: False)

    cfg = tmp_path / "vivaldi.toml"

    # --- 1. dry-run via CLI: nothing written ---------------------------
    cfg.write_text(
        '[shortcuts]\n'
        'COMMAND_CLOSE_TAB = ["meta+x", "ctrl+f4"]\n'
    )
    before = (fake_vivaldi_profile_root / "Default" / "Preferences").read_bytes()
    r = _run_cli(fake_vivaldi_profile_root, "apply", str(cfg), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "(dry-run, nothing written)" in r.stdout
    after = (fake_vivaldi_profile_root / "Default" / "Preferences").read_bytes()
    assert before == after

    # --- 2. real apply: shortcut overridden, gestures preserved --------
    _apply(fake_vivaldi_profile_root, cfg)
    actions = _actions(fake_vivaldi_profile_root)
    assert actions["COMMAND_CLOSE_TAB"]["shortcuts"] == ["meta+x", "ctrl+f4"]
    # Gestures must survive — they're in the same dict as `shortcuts`
    # but managed by Vivaldi's gesture UI, not by us.
    assert actions["COMMAND_CLOSE_TAB"]["gestures"] == ["20"]

    sidecar = (
        fake_vivaldi_profile_root
        / "Default"
        / "Preferences.dotbrowser.shortcuts.json"
    )
    state = json.loads(sidecar.read_text())
    assert state["originals"] == {"COMMAND_CLOSE_TAB": ["meta+w"]}, \
        "first apply must snapshot the pre-override shortcuts"

    # --- 3. drop the key → original restored ---------------------------
    cfg.write_text("[shortcuts]\n")  # empty body = wipe-managed
    _apply(fake_vivaldi_profile_root, cfg)
    actions = _actions(fake_vivaldi_profile_root)
    assert actions["COMMAND_CLOSE_TAB"]["shortcuts"] == ["meta+w"]
    assert actions["COMMAND_CLOSE_TAB"]["gestures"] == ["20"]
    state = json.loads(sidecar.read_text())
    assert state["originals"] == {}


def test_apply_unknown_command_errors(
    fake_vivaldi_profile_root: Path, tmp_path: Path
) -> None:
    """Unknown COMMAND_* must fail loudly with no Preferences mutation."""
    cfg = tmp_path / "bad.toml"
    cfg.write_text('[shortcuts]\nCOMMAND_NOT_REAL = ["x"]\n')

    before = (fake_vivaldi_profile_root / "Default" / "Preferences").read_bytes()
    r = _run_cli(fake_vivaldi_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "unknown Vivaldi command" in (r.stdout + r.stderr)
    after = (fake_vivaldi_profile_root / "Default" / "Preferences").read_bytes()
    assert before == after


def test_dump_against_fake_profile(fake_vivaldi_profile_root: Path) -> None:
    """`shortcuts dump` (default) emits commands with non-empty bindings."""
    r = _run_cli(fake_vivaldi_profile_root, "shortcuts", "dump")
    assert r.returncode == 0
    assert "COMMAND_CLOSE_TAB" in r.stdout
    assert "COMMAND_NEW_TAB" in r.stdout

    r = _run_cli(fake_vivaldi_profile_root, "shortcuts", "list", "close")
    assert r.returncode == 0
    assert "COMMAND_CLOSE_TAB" in r.stdout


# ---------------------------------------------------------------------------
# Settings: MAC refusal still works against the same fixture
# ---------------------------------------------------------------------------


def test_settings_mac_refusal_blocks_apply(
    fake_vivaldi_profile_root: Path, tmp_path: Path
) -> None:
    """Vivaldi shares Chromium's tracked-pref MAC system, so writing a
    MAC-protected key must be refused exactly like in Brave.
    """
    cfg = tmp_path / "bad-settings.toml"
    cfg.write_text(
        '[settings]\n'
        '"browser.show_home_button" = false\n'
    )
    r = _run_cli(fake_vivaldi_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "MAC-protected" in (r.stdout + r.stderr)


def test_settings_apply_writes_then_pops(
    fake_vivaldi_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(vivaldi_pkg, "vivaldi_running", lambda: False)

    cfg = tmp_path / "settings.toml"
    cfg.write_text('[settings]\n"vivaldi.tabs.minimize" = true\n')
    _apply(fake_vivaldi_profile_root, cfg)
    assert _prefs(fake_vivaldi_profile_root)["vivaldi"]["tabs"]["minimize"] is True

    # Empty body wipes managed keys.
    cfg.write_text('[settings]\n')
    _apply(fake_vivaldi_profile_root, cfg)
    # Key was popped (Vivaldi falls back to compiled-in default at runtime).
    assert "minimize" not in _prefs(fake_vivaldi_profile_root)["vivaldi"].get("tabs", {})


# ---------------------------------------------------------------------------
# Combined apply: shortcuts + settings + pwa in one cycle
# ---------------------------------------------------------------------------


def test_three_namespace_apply_in_one_cycle(
    fake_vivaldi_profile_root: Path, tmp_path: Path, monkeypatch
) -> None:
    """All three modules in one TOML must apply with a single backup +
    write_atomic and an external pwa write that succeeds AFTER prefs
    are durable on disk."""
    if not (sys.platform.startswith("linux") or sys.platform == "darwin" or sys.platform == "win32"):
        pytest.skip("pwa apply path is implemented for Linux, macOS and Windows")

    monkeypatch.setattr(vivaldi_pkg, "vivaldi_running", lambda: False)

    if sys.platform == "darwin":
        fake_policy = tmp_path / "policy" / "com.vivaldi.Vivaldi.plist"
    else:
        fake_policy = tmp_path / "policy" / "dotbrowser-pwa.json"

    if sys.platform == "win32":
        import ctypes

        def fake_read_payload() -> dict:
            if not fake_policy.exists():
                return {}
            try:
                return json.loads(fake_policy.read_text())
            except (json.JSONDecodeError, OSError):
                return {}

        monkeypatch.setattr(pwa_mod, "_read_existing_payload", fake_read_payload)

        def fake_sudo_write(entries: list[dict]) -> None:
            fake_policy.parent.mkdir(parents=True, exist_ok=True)
            payload = {pwa_mod.POLICY_KEY: entries}
            fake_policy.write_text(json.dumps(payload, indent=2))

        monkeypatch.setattr(pwa_mod, "_sudo_write_policy", fake_sudo_write)
        monkeypatch.setattr(ctypes.windll.shell32, "IsUserAnAdmin", lambda: 1)
    else:
        monkeypatch.setattr(pwa_mod, "POLICY_FILE", fake_policy)

        def fake_sudo_write(entries: list[dict]) -> None:
            fake_policy.parent.mkdir(parents=True, exist_ok=True)
            fake_policy.write_bytes(pwa_mod._build_policy_payload(entries))

        monkeypatch.setattr(pwa_mod, "_sudo_write_policy", fake_sudo_write)

        real_run = subprocess.run

        def fake_run(cmd, *args, **kwargs):
            if list(cmd[:3]) == ["sudo", "-n", "true"]:
                return subprocess.CompletedProcess(cmd, 0)
            if list(cmd[:2]) == ["sudo", "-v"]:
                return subprocess.CompletedProcess(cmd, 0)
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(vivaldi_pkg.subprocess, "run", fake_run)

    cfg = tmp_path / "vivaldi.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'COMMAND_CLOSE_TAB = ["meta+x"]\n'
        '\n'
        '[settings]\n'
        '"vivaldi.tabs.minimize" = true\n'
        '\n'
        '[pwa]\n'
        'urls = ["https://squoosh.app/"]\n'
    )
    _apply(fake_vivaldi_profile_root, cfg)

    p = _prefs(fake_vivaldi_profile_root)
    assert p["vivaldi"]["actions"][0]["COMMAND_CLOSE_TAB"]["shortcuts"] == ["meta+x"]
    assert p["vivaldi"]["tabs"]["minimize"] is True

    if sys.platform == "darwin":
        with fake_policy.open("rb") as f:
            pol = plistlib.load(f)
    else:
        pol = json.loads(fake_policy.read_text())
    assert [e["url"] for e in pol[pwa_mod.POLICY_KEY]] == ["https://squoosh.app/"]

    # Single backup despite three plans — that's the unified-cycle promise.
    backups = list((fake_vivaldi_profile_root / "Default").glob("Preferences.bak.*"))
    assert len(backups) == 1


def test_settings_refusal_blocks_shortcuts_write_too(
    fake_vivaldi_profile_root: Path, tmp_path: Path
) -> None:
    """Atomicity: a MAC-protected key in [settings] must veto the
    whole apply, including shortcut changes."""
    cfg = tmp_path / "vivaldi.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'COMMAND_CLOSE_TAB = ["meta+x"]\n'
        '\n'
        '[settings]\n'
        '"browser.show_home_button" = false\n'
    )

    before = (fake_vivaldi_profile_root / "Default" / "Preferences").read_bytes()
    r = _run_cli(fake_vivaldi_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "MAC-protected" in (r.stdout + r.stderr)
    after = (fake_vivaldi_profile_root / "Default" / "Preferences").read_bytes()
    assert before == after

    sc_state = (
        fake_vivaldi_profile_root
        / "Default"
        / "Preferences.dotbrowser.shortcuts.json"
    )
    st_state = (
        fake_vivaldi_profile_root
        / "Default"
        / "Preferences.dotbrowser.settings.json"
    )
    assert not sc_state.exists()
    assert not st_state.exists()


def test_dry_run_shows_grouped_diff(
    fake_vivaldi_profile_root: Path, tmp_path: Path
) -> None:
    cfg = tmp_path / "vivaldi.toml"
    cfg.write_text(
        '[shortcuts]\n'
        'COMMAND_CLOSE_TAB = ["meta+x"]\n'
        '\n'
        '[settings]\n'
        '"vivaldi.tabs.minimize" = true\n'
    )
    r = _run_cli(fake_vivaldi_profile_root, "apply", str(cfg), "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "shortcuts:" in r.stdout
    assert "settings:" in r.stdout
    assert "(dry-run, nothing written)" in r.stdout


def test_apply_empty_config_errors(
    fake_vivaldi_profile_root: Path, tmp_path: Path
) -> None:
    """A TOML with no recognized table must fail rather than silently
    no-op (matches the Brave behavior)."""
    cfg = tmp_path / "empty.toml"
    cfg.write_text("# nothing here\n")
    r = _run_cli(fake_vivaldi_profile_root, "apply", str(cfg))
    assert r.returncode != 0
    assert "no [shortcuts], [settings] or [pwa]" in (r.stdout + r.stderr)
