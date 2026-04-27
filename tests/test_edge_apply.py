"""Tests for dotbrowser edge — settings apply and init."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dotbrowser import edge as edge_pkg
from dotbrowser.edge import settings as st
from dotbrowser.edge import pwa


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_edge_profile(tmp_path: Path) -> Path:
    """Minimal Edge profile for testing."""
    profile = tmp_path / "Default"
    profile.mkdir()
    prefs = {
        "omnibox": {"prevent_url_elisions": False},
        "bookmark_bar": {"show_on_all_tabs": True},
    }
    (profile / "Preferences").write_text(json.dumps(prefs))
    return tmp_path


def _apply(profile_root: Path, config: Path, monkeypatch) -> None:
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    args = argparse.Namespace(
        profile_root=profile_root,
        profile="Default",
        config=str(config),
        dry_run=False,
        kill_browser=False,
    )
    edge_pkg.cmd_apply(args)


# ---------------------------------------------------------------------------
# Settings apply
# ---------------------------------------------------------------------------


def test_settings_apply_writes_and_verifies(
    fake_edge_profile: Path, tmp_path: Path, monkeypatch
) -> None:
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        '[settings]\n'
        '"omnibox.prevent_url_elisions" = true\n'
        '"bookmark_bar.show_on_all_tabs" = false\n'
    )
    _apply(fake_edge_profile, cfg, monkeypatch)

    prefs = json.loads(
        (fake_edge_profile / "Default" / "Preferences").read_text()
    )
    assert prefs["omnibox"]["prevent_url_elisions"] is True
    assert prefs["bookmark_bar"]["show_on_all_tabs"] is False


def test_settings_apply_refuses_mac_protected_key(
    fake_edge_profile: Path, tmp_path: Path
) -> None:
    """MAC-protected keys must be refused."""
    prefs_path = fake_edge_profile / "Default" / "Preferences"
    prefs = json.loads(prefs_path.read_text())
    prefs["protection"] = {"macs": {"homepage": "somehash"}}
    prefs_path.write_text(json.dumps(prefs))

    with pytest.raises(SystemExit, match="MAC-protected"):
        st.plan_apply(
            prefs_path,
            json.loads(prefs_path.read_text()),
            {"homepage": "https://example.com"},
        )


def test_dry_run_does_not_write(
    fake_edge_profile: Path, tmp_path: Path, monkeypatch
) -> None:
    cfg = tmp_path / "edge.toml"
    cfg.write_text(
        '[settings]\n"omnibox.prevent_url_elisions" = true\n'
    )
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    args = argparse.Namespace(
        profile_root=fake_edge_profile,
        profile="Default",
        config=str(cfg),
        dry_run=True,
        kill_browser=False,
    )
    edge_pkg.cmd_apply(args)

    prefs = json.loads(
        (fake_edge_profile / "Default" / "Preferences").read_text()
    )
    # Should NOT have changed
    assert prefs["omnibox"]["prevent_url_elisions"] is False


def test_empty_config_errors(fake_edge_profile: Path, tmp_path: Path, monkeypatch) -> None:
    """A TOML with no recognized tables should error."""
    cfg = tmp_path / "empty.toml"
    cfg.write_text("# nothing here\n")
    monkeypatch.setattr(edge_pkg, "edge_running", lambda: False)
    args = argparse.Namespace(
        profile_root=fake_edge_profile,
        profile="Default",
        config=str(cfg),
        dry_run=False,
        kill_browser=False,
    )
    with pytest.raises(SystemExit, match="nothing to apply"):
        edge_pkg.cmd_apply(args)


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def test_init_stdout() -> None:
    """Edge init should print a valid template."""
    r = subprocess.run(
        [sys.executable, "-m", "dotbrowser", "edge", "init"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert r.returncode == 0
    assert "[settings]" in r.stdout
    assert "# [pwa]" in r.stdout
    # Edge doesn't have [shortcuts]
    assert "[shortcuts]" not in r.stdout


def test_init_output_file(tmp_path: Path) -> None:
    dest = tmp_path / "my-edge.toml"
    r = subprocess.run(
        [sys.executable, "-m", "dotbrowser", "edge", "init", "-o", str(dest)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert r.returncode == 0
    content = dest.read_text(encoding="utf-8")
    assert "[settings]" in content
    assert "my-edge.toml" in content
